"""위험도 기준: 기본 기준 · 저장하면 새 버전 + 점수 재계산 · 미리보기는 저장 안 함 · 잘못된 기준 거부 · 도로폭 반영"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, inspect
from test_api import API, _post, _site, drain

from app.ai_engine.passability.risk import RiskContext, RiskParams, risk_score
from app.ai_engine.passability.rules import PassabilityResult
from app.db import SessionLocal, engine
from app.db.models import RiskPolicy
from app.main import app


@pytest.fixture(autouse=True)
def _clean_policies():
    yield
    if not inspect(engine).has_table("risk_policies"):  # 앱을 띄우지 않은 단위 테스트
        return
    with SessionLocal() as db:  # 다른 테스트 점수에 영향이 없게 기본 기준으로 되돌린다
        db.execute(delete(RiskPolicy))
        db.commit()


def _res(verdict="narrowed", ratio=0.45, m=None):
    occ = None if ratio is None else int(100 * (1 - ratio))
    return PassabilityResult(verdict, ratio, m, occ, None, None, [], {})


def _scored(c, site_id, seeds=range(30, 60)):
    """dummy 엔진에서 점수가 나온(판단 유보가 아닌) 관측 하나"""
    for seed in seeds:
        oid = _post(c, seed=seed, site_id=site_id).json()["observation_id"]
        drain()
        o = c.get(f"{API}/observations/{oid}").json()
        if o["risk_score"] is not None:
            return o
    pytest.skip("dummy 엔진이 점수 있는 결과를 내지 않음")


def test_terms_and_bands():
    p = RiskParams()
    s, d = risk_score(_res(), p)
    assert d["terms"]["width"] == 0.55 and d["missing"] == ["length", "absolute", "detour", "cause"]
    assert 30 <= s <= 69 and d["grade"] in ("low", "medium", "high")
    # 넓은 도로·차량·좁은 남은 폭이면 같은 비율이라도 더 위험
    s2, d2 = risk_score(_res(m=0.8), p, RiskContext(causes=["차량"], road_width_m=30, road_lanes=6))
    assert d2["terms"]["absolute"] == 1.0 and d2["terms"]["detour"] == 1.0 and s2 > s
    # 판정 구간: 통과 가능은 아무리 커도 29 이하, 단절은 70 이상
    assert risk_score(_res("passable", 0.0), p)[0] <= 29
    assert risk_score(_res("blocked", 1.0), p)[0] >= 70
    # 보차혼용 도로는 도로폭과 상관없이 detour = shared_road
    assert risk_score(_res(), p, RiskContext(selected_surface="road", road_width_m=4))[1]["terms"]["detour"] == 1.0
    assert risk_score(_res("undetermined", None), p)[0] is None


@pytest.mark.parametrize(
    "bad",
    [
        {"weights": {"width": 1, "speed": 1}},
        {"weights": {"width": 0}},
        {"min_pass_width_m": 2.5},
        {"bands": {k: {"min": 50, "max": 10} for k in ("passable", "narrowed", "blocked")}},
        {"grades": [{"min": 50, "code": "high", "label": "높음"}]},
        {"cause_weights": {"차량": 1.5}},
    ],
)
def test_invalid_policy_rejected(bad):
    with TestClient(app) as c:
        r = c.put(f"{API}/risk-policy", json={"params": {**RiskParams().model_dump(mode="json"), **bad}})
        assert r.status_code == 422, r.text
        assert c.get(f"{API}/risk-policy").json()["version"] is None


def test_policy_versions_preview_and_recompute():
    with TestClient(app) as c:
        cur = c.get(f"{API}/risk-policy").json()
        assert cur["version"] is None and cur["params"] == cur["defaults"]
        o = _scored(c, _site(c, "위험도 기준 구간")["id"])
        assert o["risk_policy_version"] is None and o["risk_grade_label"] in ("낮음", "보통", "높음")

        # 모든 판정을 0~0으로 자르는 극단 기준 → 미리보기는 0, 저장은 안 됨
        params = cur["defaults"] | {
            "bands": {k: {"min": 0, "max": 0} for k in ("passable", "narrowed", "blocked")},
            "grades": [{"min": 0, "code": "none", "label": "없음"}],
        }
        pv = c.post(f"{API}/risk-policy/preview", json={"params": params}).json()
        item = next(i for i in pv["items"] if i["observation_id"] == o["observation_id"])
        assert item["preview"] == {**item["preview"], "risk_score": 0, "grade": "none"}
        assert c.get(f"{API}/observations/{o['observation_id']}").json()["risk_score"] == o["risk_score"]

        saved = c.put(f"{API}/risk-policy", json={"params": params, "note": "시험", "actor": "교통행정과"}).json()
        assert saved["version"] == 1 and saved["recomputed"] >= 1
        got = c.get(f"{API}/observations/{o['observation_id']}").json()
        assert got["risk_score"] == 0 and got["risk_grade"] == "none" and got["risk_policy_version"] == 1

        # 되돌리기 = 기본값을 새 버전으로 저장
        back = c.put(f"{API}/risk-policy", json={"params": cur["defaults"]}).json()
        assert back["version"] == 2
        assert c.get(f"{API}/observations/{o['observation_id']}").json()["risk_score"] == o["risk_score"]
        assert [h["version"] for h in c.get(f"{API}/risk-policy/history").json()] == [2, 1]


def test_site_road_width_changes_detour_term():
    with TestClient(app) as c:
        s = _site(c, "도로폭 반영 구간")
        o = _scored(c, s["id"], range(60, 90))
        assert c.patch(f"{API}/sites/{s['id']}", json={"road_width_m": 30}).json()["road_width_source"] == "field"
        ana = c.get(f"{API}/observations/{o['observation_id']}/analyses").json()[-1]
        assert ana["evidence"]["risk"]["terms"]["detour"] == 1.0

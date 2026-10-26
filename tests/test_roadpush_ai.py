"""
roadpush_ai 엔진 연결 (RoadPush_AI 팀 코드).
- 7-B 결과 → 백엔드 판정 규칙 (AI 계약의 소비 규칙)
- 합성 마스크로 실제 roadpush_ai Phase 7-A → 7-B 실행 (모델 없이. AI 저장소가 옆 폴더에 없으면 건너뜀)
- 모델 팀 attach_cause 규칙 → 원인 배지
- 파이프라인: 7-B 기록 → 판정·overlay·근거 (Phase 6 모델 실행만 가짜로 바꿈)
"""

import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.ai_engine.passability.phase7 import to_result
from app.ai_engine.passability.risk import risk_score
from app.config import get_settings

AI_SRC = Path(__file__).resolve().parents[2] / "RoadPush_AI" / "surface_passability" / "src"
OBSTACLE_ROOT = Path(__file__).resolve().parents[2] / "RoadPush_AI" / "obstacle_yolo"


def _record(**kw) -> dict:
    """phase7_b_result_v2 최소 형태 (authoritative · relative_only 기본)"""
    r = {
        "schema_version": "phase7_b_result_v2",
        "selected_surface": "sidewalk",
        "phase7a_status": "selected",
        "result_authority": "authoritative",
        "measurement_scope": "full",
        "measurement_confidence": "reliable",
        "human_review_required": False,
        "abstention_reasons": [],
        "minimum_passable_ratio": 0.7,
        "raw_minimum_passable_ratio": 0.1,
        "absolute_passable_width_m": None,
        "agent_width_m": None,
        "required_safe_width_m": None,
        "passability_state": "relative_only",
        "bottleneck": {"cause": "recognized_obstacle"},
        "flags": [],
    }
    r.update(kw)
    return r


# ── 7-B → 판정 (AI docs/PHASE7B_PRODUCTION_LOGIC.md 4·5·7장)


def test_relative_only_uses_backend_ratio_policy_not_raw_minimum():
    assert to_result(_record(minimum_passable_ratio=0.7)).verdict == "passable"
    assert to_result(_record(minimum_passable_ratio=0.45)).verdict == "narrowed"
    res = to_result(_record(minimum_passable_ratio=0.2))
    assert res.verdict == "blocked" and res.occupancy == 80 and res.bottleneck_ratio == 0.2
    # raw_minimum_passable_ratio(0.1)로 상태를 정하지 않는다
    assert to_result(_record(minimum_passable_ratio=0.65, raw_minimum_passable_ratio=0.01)).verdict == "passable"


def test_ai_physical_state_wins_when_widths_were_given():
    rec = _record(
        minimum_passable_ratio=0.5,
        passability_state="passable",
        absolute_passable_width_m=1.2,
        agent_width_m=0.9,
        required_safe_width_m=1.08,
    )
    res = to_result(rec)
    assert res.verdict == "passable"  # 상대폭 기준(0.6)이면 좁아짐이지만 AI 실폭 판정을 따른다
    assert res.bottleneck_m_est == 1.2
    assert "1.08m" in res.reasons[0]


def test_provisional_review_is_never_a_verdict_or_risk():
    rec = _record(
        phase7a_status="ambiguous_hold",
        result_authority="provisional_review",
        measurement_scope="provisional",
        human_review_required=True,
        minimum_passable_ratio=0.2,
    )
    res = to_result(rec)
    assert res.verdict == "undetermined" and res.uncertainty_code == "boundary_review"
    assert res.bottleneck_ratio is None and res.occupancy is None
    assert risk_score(res)[0] is None  # 집계·우선순위에 넣지 않는다
    assert any("검토용 가계산" in r and "20%" in r for r in res.reasons)


def test_typed_null_maps_abstention_reason_without_zero():
    res = to_result(
        _record(
            phase7a_status="failed",
            result_authority="none",
            measurement_scope="none",
            measurement_confidence="insufficient",
            abstention_reasons=["phase7a_failed"],
            minimum_passable_ratio=None,
            passability_state="undetermined",
        )
    )
    assert res.verdict == "undetermined" and res.uncertainty_code == "boundary_failed"
    assert res.bottleneck_ratio is None  # null을 0으로 바꾸지 않는다
    assert (
        to_result(_record(selected_surface=None, minimum_passable_ratio=None)).uncertainty_code == "surface_uncertain"
    )


def test_zero_ratio_is_blocked_and_partial_scope_is_noted():
    res = to_result(_record(minimum_passable_ratio=0.0, passability_state="blocked"))
    assert res.verdict == "blocked" and res.occupancy == 100
    res = to_result(_record(measurement_scope="partial", human_review_required=True))
    assert res.verdict == "passable" and any("앞부분만" in r for r in res.reasons)


# ── 실제 roadpush_ai 7-A → 7-B (합성 마스크)


def _synthetic_phase6(root: Path, obstacle: bool = True):
    """
    사다리꼴 보도 + 가운데 장애물. Phase 6이 sidewalk에 저장하는 파일 이름으로 쓴다.
    실제 분석 크기(긴 변 700px)의 절반: V6 경계 적합이 행 수에 비례해 느려서 (700px 합성 1장 약 40초, CPU)
    """
    h, w = 263, 350
    structural = np.zeros((h, w), bool)
    for y in range(60, h):
        t = (y - 60) / (h - 60)
        structural[y, int(w * (0.42 - 0.30 * t)) : int(w * (0.58 + 0.30 * t))] = True
    blocker = np.zeros_like(structural)
    if obstacle:
        blocker[165:210, 100:165] = True
        blocker &= structural
    labels = np.full((h, w), 17, np.uint8)  # Mapillary building
    labels[structural] = 15  # sidewalk
    labels[blocker] = 55  # car
    d = root / "phase6" / "sidewalk"
    d.mkdir(parents=True)
    cv2.imwrite(str(d / "07_proposed_pruned_boundary.png"), structural.astype(np.uint8) * 255)
    cv2.imwrite(str(d / "08_applied_blocker.png"), blocker.astype(np.uint8) * 255)
    return labels, blocker


@pytest.fixture
def roadpush():
    if not (AI_SRC / "roadpush_ai").is_dir():
        pytest.skip("RoadPush_AI 저장소가 옆 폴더에 없음")
    pytest.importorskip("scipy")
    # conftest가 ROADPUSH_AI_ROOT(모델·캐시 위치)를 임시 폴더로 돌려 두었으므로 코드 위치만 직접 넣는다
    if str(AI_SRC) not in sys.path:
        sys.path.insert(0, str(AI_SRC))
    from app.ai_engine import roadpush

    roadpush.prepare_runtime()
    return roadpush


NO_WIDTHS = {"surface_width_m": None, "agent_width_m": None, "safety_factor": None}


def test_measure_runs_real_phase7a_and_7b(tmp_path, roadpush):
    labels, blocker = _synthetic_phase6(tmp_path)
    selection = {"selected_surface": "sidewalk", "state": "sidewalk", "reasons": []}
    widths = {**NO_WIDTHS, "surface_width_m": 2.0}
    m = roadpush.measure("syn", selection, labels, tmp_path / "phase6", {"sidewalk": None}, widths)
    rec = m.record  # compose_phase7_b_result 안에서 validate_phase7_b_record를 통과한 기록
    assert rec["schema_version"] == "phase7_b_result_v2"
    assert rec["phase7a_policy_id"] == "phase7_extension_track_s1_v4_probe_human_review_grading"
    assert rec["result_authority"] == "authoritative" and rec["measurement_confidence"] == "reliable"
    ratio = rec["minimum_passable_ratio"]
    assert 0 < ratio < 1  # 가운데 장애물이 통로를 줄인다
    assert rec["absolute_passable_width_m"] == pytest.approx(2.0 * ratio)
    assert rec["passability_state"] == "relative_only"  # 통행 폭(agent) 입력 없음
    assert m.blocker_path.name == "08_applied_blocker.png"
    assert m.occupancy is not None and (m.occupancy & blocker).sum() == blocker.sum()

    cleaned = roadpush._relative(rec, tmp_path)
    text = json.dumps(cleaned)  # DB JSON에 들어간다
    assert str(tmp_path) not in text and str(tmp_path).replace("\\", "\\\\") not in text  # 로컬 경로를 내지 않는다
    assert cleaned["diagnostics"]["phase7a"]["provenance"]["phase6_blocker_path"] == (
        "phase6/sidewalk/08_applied_blocker.png"
    )

    clear = roadpush.measure(
        "clear",
        selection,
        _synthetic_phase6(tmp_path / "clear", obstacle=False)[0],
        tmp_path / "clear" / "phase6",
        {"sidewalk": None},
        NO_WIDTHS,
    )
    assert clear.record["minimum_passable_ratio"] > ratio


def test_measure_surface_uncertain_is_typed_null(tmp_path, roadpush):
    labels, _ = _synthetic_phase6(tmp_path)
    selection = {"selected_surface": None, "state": "uncertain_none", "reasons": ["no_surface"]}
    m = roadpush.measure("none", selection, labels, tmp_path / "phase6", {}, NO_WIDTHS)
    assert m.record["result_authority"] == "none" and m.record["minimum_passable_ratio"] is None
    assert m.blocker_path is None and m.structural is None
    assert to_result(m.record).uncertainty_code == "surface_uncertain"


# ── 모델 팀 원인 규칙 (attach_cause.attribute) → 원인 배지


def test_attach_cause_rule_maps_to_cause_badges():
    if not (OBSTACLE_ROOT / "service" / "attach_cause.py").is_file():
        pytest.skip("RoadPush_AI/obstacle_yolo가 옆 폴더에 없음")
    pytest.importorskip("ultralytics")
    from app.ai_engine import causes

    ac = causes._service_module(OBSTACLE_ROOT)
    blocker = np.zeros((100, 100), bool)
    blocker[60:80, 10:30] = True  # 차량 바닥
    blocker[60:70, 60:90] = True  # 클래스 없는 물체
    det = {"name_ko": "", "type": "C", "action": "", "confidence": 0.9}
    dets = [
        {**det, "class": "vehicle", "box_analysis": [8, 20, 32, 80]},
        {**det, "class": "micromobility", "type": "A", "box_analysis": [0, 0, 5, 5]},  # 길을 막지 않는 탐지
    ]
    status, comps, _, used = ac.attribute(blocker, dets)
    assert status == "partially_attributed" and used == {0}
    assert causes.badges(comps) == ["차량"]
    unclassified = [c for c in comps if not c["causes"]]
    assert causes.badges(unclassified) == ["장애물"]
    assert causes.badges([]) == []


# ── 파이프라인: 7-B 기록 → 판정·overlay·근거


@pytest.fixture
def fake_engine(tmp_path, roadpush, monkeypatch):
    """roadpush_ai 엔진 모드 + Mask2Former·Phase 6만 합성 결과로 바꾼다 (7-A·7-B는 실제 AI 코드). 호출 인자를 모은다"""
    labels, _ = _synthetic_phase6(tmp_path / "ai")
    selection = {"selected_surface": "sidewalk", "state": "sidewalk", "reasons": []}
    calls: list[dict] = []

    def fake_run_photo(image_path, work_dir, **kw):
        calls.append({"image_path": Path(image_path), "work_dir": Path(work_dir), **kw})
        Path(work_dir).mkdir(parents=True, exist_ok=False)  # 실제처럼 재시도마다 새 폴더여야 한다
        widths = {k: kw[k] for k in NO_WIDTHS}
        m = roadpush.measure(kw["sample_id"], selection, labels, tmp_path / "ai" / "phase6", {"sidewalk": None}, widths)
        return roadpush.Phase7Run(
            record=roadpush._relative(m.record, tmp_path),
            image_bgr=np.full((*labels.shape, 3), 120, np.uint8),
            labels=labels,
            selection=selection,
            v6=m.v6,
            phase7a=m.phase7a,
            structural=m.structural,
            occupancy=m.occupancy,
            phase6_structural=m.phase6_structural,
            phase6_blocker=m.phase6_blocker,
            blocker_path=m.blocker_path,
            image_sha256="0" * 64,
            idempotency_key=roadpush.idempotency_key("0" * 64, "image_upload", widths, m.occupancy_source, "p"),
            versions={"semantic": "mask2former_swin_large_mapillary@cpu", "phase7b": "phase7_b_result_v2"},
        )

    monkeypatch.setattr(roadpush, "run_photo", fake_run_photo)
    monkeypatch.setattr(get_settings(), "analysis_engine", "roadpush_ai")
    monkeypatch.setattr(get_settings(), "obstacle_yolo_root", None)  # 원인 모델 없이
    return calls


def test_pipeline_roadpush_engine_end_to_end(tmp_path, fake_engine):
    from app.ai_engine import pipeline

    photo = tmp_path / "photo.jpg"
    cv2.imwrite(str(photo), np.full((60, 80, 3), 128, np.uint8))
    out = pipeline.analyze(
        photo, "image", work_dir=tmp_path / "w", cache_dir=tmp_path / "c", storage_root=tmp_path, sample_id="obs_1"
    )
    assert out.result.verdict in ("passable", "narrowed", "blocked")
    assert out.result.bottleneck_ratio == round(out.evidence["phase7b"]["minimum_passable_ratio"], 3)
    assert out.risk is not None and out.causes == []
    assert out.rule_version.startswith("phase7_b_result_v2/")
    assert set(out.images) == {"class_map", "surface_overlay", "obstacle_overlay"}
    assert out.images["obstacle_overlay"].shape == (263, 350, 3)  # 분석 크기 (합성 Phase 6과 같음)
    assert fake_engine[0]["input_profile"] == "image_upload" and fake_engine[0]["surface_width_m"] is None
    assert out.evidence["engine"] == "roadpush_ai" and out.evidence["cause"] is None
    json.dumps(out.evidence)  # analyses.evidence(JSON)에 그대로 저장된다


def test_worker_saves_phase7_result_through_api(fake_engine):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services import worker

    api = "/api/v1"
    jpg = cv2.imencode(".jpg", np.full((360, 480, 3), 128, np.uint8))[1].tobytes()
    with TestClient(app) as c:
        site = c.post(
            f"{api}/sites", json={"name": "7-B 확인 구간", "lat": 37.3868, "lng": 127.1273, "sidewalk_width_m": 2.4}
        )
        data = {"captured_at": "2026-10-26T10:00:00+09:00", "lat": 37.3868, "lng": 127.1273, "source": "cctv"}
        data["site_id"] = site.json()["id"]
        oid = c.post(f"{api}/observations", data=data, files={"file": ("p.jpg", jpg, "image/jpeg")}).json()[
            "observation_id"
        ]
        while worker.run_once():
            pass
        call = fake_engine[-1]
        assert call["sample_id"] == oid and call["input_profile"] == "cctv"
        assert call["surface_width_m"] == 2.4  # 구간 보도 폭 → 7-B 실폭
        assert call["work_dir"].as_posix().endswith("/ai/attempt-1")

        got = c.get(f"{api}/observations/{oid}").json()
        assert got["processing_status"] == "completed", got
        assert got["passability"] in ("passable", "narrowed", "blocked")
        assert got["bottleneck_m_est"] is not None and got["confidence"] is None
        assert got["images"]["obstacle_overlay"]
        ana = c.get(f"{api}/observations/{oid}/analyses").json()[-1]
        assert ana["evidence"]["phase7b"]["schema_version"] == "phase7_b_result_v2"
        assert ana["evidence"]["phase7b"]["surface_width_m"] == 2.4
        assert ":\\" not in json.dumps(ana["evidence"])  # 로컬 절대 경로(C:\…)를 응답에 내지 않는다


def test_unknown_engine_is_rejected(tmp_path, monkeypatch):
    from app.ai_engine import pipeline

    monkeypatch.setattr(get_settings(), "analysis_engine", "selectstar")
    with pytest.raises(ValueError, match="ANALYSIS_ENGINE"):
        pipeline.analyze(
            tmp_path / "x.jpg", "image", work_dir=tmp_path, cache_dir=tmp_path, storage_root=tmp_path, sample_id="x"
        )

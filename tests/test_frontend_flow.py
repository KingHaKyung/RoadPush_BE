"""프론트(src/lib/useObservations.ts)가 실제로 보내는 요청 순서 그대로 확인한다"""

from fastapi.testclient import TestClient
from test_api import API, _post, _site, drain

from app.main import app


def _bulk(c, ids, state, dept, content):
    # 프론트 setActions: 처리 상태와 부서를 함께 보내고, 화면 기록 문구를 content로 보낸다
    body = {"observation_ids": ids, "state": state, "dept": dept, "content": content, "actor": "교통행정과"}
    r = c.post(f"{API}/observations/bulk-action", json=body)
    assert r.status_code == 200
    return r.json()[0]


def test_dept_only_change_on_untouched_observation_is_saved():
    """미처리 관측에서 담당 부서만 바꾸면 state=none + dept로 온다 → 부서가 저장돼야 한다"""
    with TestClient(app) as c:
        s = _site(c, "부서만 배정 구간")
        oid = _post(c, seed=11, site_id=s["id"]).json()["observation_id"]
        drain()
        o = _bulk(c, [oid], "none", "도로관리과", "담당 부서: 미배정 → 도로관리과")
        assert o["action"]["state"] == "none" and o["action"]["dept"] == "도로관리과"


def test_dept_can_go_back_to_unassigned():
    """'미배정'으로 되돌리면 프론트는 dept=null을 보낸다 → 부서가 비어야 한다"""
    with TestClient(app) as c:
        s = _site(c, "부서 해제 구간")
        oid = _post(c, seed=12, site_id=s["id"]).json()["observation_id"]
        drain()
        _bulk(c, [oid], "inspection_requested", "주차관리과", "처리 요청서 전달 (주차관리과)")
        o = _bulk(c, [oid], "inspection_requested", None, "담당 부서: 주차관리과 → 미배정")
        assert o["action"]["dept"] is None


def test_status_log_text_is_not_repeated_in_history():
    """'처리 상태: …' 문구를 content로 보내도 이력에 같은 말이 두 번 붙지 않는다"""
    with TestClient(app) as c:
        s = _site(c, "이력 중복 구간")
        oid = _post(c, seed=13, site_id=s["id"]).json()["observation_id"]
        drain()
        _bulk(c, [oid], "inspection_requested", "주차관리과", "처리 상태: 현장점검 요청")
        o = _bulk(c, [oid], "in_progress", "주차관리과", "처리 상태: 조치 중")
        txts = [h["txt"] for h in o["history"] if h["txt"].startswith("처리 상태")]
        assert txts == ["처리 상태: 현장점검 요청 (주차관리과)", "처리 상태: 조치 중 (주차관리과)"]


def test_delete_observation_removes_rows_and_files():
    """잘못 올린 관측 삭제: 분석·검수·메모·조치·사진 파일까지. 관측이 남은 구간은 지울 수 없다"""
    from app.services.storage import get_storage

    with TestClient(app) as c:
        s = _site(c, "삭제 구간")
        keep = _post(c, seed=21, site_id=s["id"]).json()["observation_id"]
        oid = _post(c, seed=22, site_id=s["id"], captured_at="2026-10-19T09:00:00+09:00").json()["observation_id"]
        drain()
        c.post(f"{API}/observations/{oid}/reviews", json={"status": "confirmed"})
        c.post(f"{API}/observations/{oid}/notes", json={"content": "지울 메모"})
        _bulk(c, [oid], "inspection_requested", "주차관리과", None)
        files = get_storage().path(f"observations/{oid}")
        assert files.exists()

        assert c.delete(f"{API}/observations/{oid}").status_code == 204
        assert c.get(f"{API}/observations/{oid}").status_code == 404
        assert not files.exists()
        assert [o["observation_id"] for o in c.get(f"{API}/sites/{s['id']}/observations").json()] == [keep]
        assert c.delete(f"{API}/observations/{oid}").status_code == 404

        assert c.delete(f"{API}/sites/{s['id']}").status_code == 409  # 관측이 남아 있음
        assert c.delete(f"{API}/observations/{keep}").status_code == 204
        assert c.delete(f"{API}/sites/{s['id']}").status_code == 204
        assert c.get(f"{API}/sites/{s['id']}").status_code == 404

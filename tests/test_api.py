import io
from datetime import UTC, datetime, timedelta

import cv2
import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.services import worker

API = "/api/v1"
LAT, LNG = 37.386851, 127.127334


def _jpg(seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    img = rng.integers(60, 200, (360, 480, 3), dtype=np.uint8)
    return cv2.imencode(".jpg", img)[1].tobytes()


def _post(c, seed=1, **form):
    data = {"captured_at": "2026-10-18T14:32:00+09:00", "lat": LAT, "lng": LNG, "source": "upload", **form}
    return c.post(f"{API}/observations", data=data, files={"file": (f"p{seed}.jpg", _jpg(seed), "image/jpeg")})


def _site(c, name="서현로 18길") -> dict:
    return c.post(f"{API}/sites", json={"name": name, "lat": LAT, "lng": LNG, "sidewalk_width_m": 2.4}).json()


def drain():
    while worker.run_once():
        pass


def test_response_follows_master_spec_4_4():
    with TestClient(app) as c:
        r = _post(c, new_site_name="4.4 확인 구간")
        assert r.status_code == 202, r.text
        o = r.json()
        for key in [
            "observation_id", "analysis_id", "site_id", "location", "captured_at", "processing_status",
            "passability", "risk_score", "uncertainty_reason", "images", "review_status", "human_passability",
        ]:  # fmt: skip
            assert key in o
        assert o["processing_status"] == "pending" and o["passability"] is None and o["risk_score"] is None
        assert o["captured_at"] == "2026-10-18T14:32:00+09:00"
        assert set(o["images"]) == {"original", "surface_overlay", "obstacle_overlay"}

        drain()
        got = c.get(f"{API}/observations/{o['observation_id']}").json()
        assert got["processing_status"] == "completed"
        assert got["passability"] in ("passable", "narrowed", "blocked", "undetermined")
        assert got["review_status"] == "pending" and got["human_passability"] is None
        assert got["images"]["surface_overlay"].startswith("http")
        assert c.get(got["images"]["surface_overlay"].split("testserver")[1]).status_code == 200
        if got["passability"] != "undetermined":
            assert got["risk_complete"] is False  # 단일 사진은 길이 항 측정 불가


def test_site_must_be_confirmed():
    with TestClient(app) as c:
        assert _post(c).status_code == 422  # 구간 미확인
        assert _post(c, lat=None, lng=None, new_site_name="x").status_code == 422  # 신규 구간인데 위치 없음
        s = _site(c, "후보 구간")
        cands = c.get(f"{API}/sites/candidates", params={"lat": LAT + 0.0001, "lng": LNG}).json()
        assert any(x["site"]["id"] == s["id"] for x in cands["sites"])
        far = c.get(f"{API}/sites/candidates", params={"lat": LAT + 0.01, "lng": LNG}).json()
        assert all(x["site"]["id"] != s["id"] for x in far["sites"])


def test_review_is_per_analysis_and_not_inherited():
    with TestClient(app) as c:
        s = _site(c)
        oid = _post(c, site_id=s["id"]).json()["observation_id"]
        assert c.post(f"{API}/observations/{oid}/reviews", json={"status": "confirmed"}).status_code == 409
        drain()
        assert (
            c.post(f"{API}/observations/{oid}/reviews", json={"status": "corrected"}).status_code == 422
        )  # 수정에는 사람 판정 필요
        rv = c.post(
            f"{API}/observations/{oid}/reviews",
            json={
                "status": "corrected",
                "human_passability": "blocked",
                "comment": "현장 확인",
                "reviewer": "교통행정과",
            },
        ).json()
        assert rv["review_status"] == "corrected" and rv["human_passability"] == "blocked"
        assert rv["passability"] is not None  # AI 결과는 그대로

        re = c.post(f"{API}/observations/{oid}/reanalyze", json={"note": "밝기 보정"}).json()
        assert re["processing_status"] == "pending" and re["review_status"] == "pending"
        drain()
        after = c.get(f"{API}/observations/{oid}").json()
        assert after["review_status"] == "pending" and after["human_passability"] is None
        assert after["analysis_trigger"] == "reanalysis"
        hist = c.get(f"{API}/observations/{oid}/analyses").json()
        assert [h["trigger"] for h in hist] == ["initial", "reanalysis"]


def test_review_cancel_returns_to_pending():
    with TestClient(app) as c:
        s = _site(c, "검수 취소 구간")
        oid = _post(c, seed=5, site_id=s["id"]).json()["observation_id"]
        drain()
        assert c.post(f"{API}/observations/{oid}/reviews/cancel").status_code == 409  # 검수 전
        c.post(f"{API}/observations/{oid}/reviews", json={"status": "corrected", "human_passability": "passable"})
        body = {"comment": "오판", "reviewer": "교통행정과"}
        o = c.post(f"{API}/observations/{oid}/reviews/cancel", json=body).json()
        assert o["review_status"] == "pending" and o["human_passability"] is None and o["review"] is None
        assert any(h["txt"] == "검수 취소 · 오판" for h in o["history"])
        assert c.post(f"{API}/observations/{oid}/reviews/cancel").status_code == 409  # 이미 취소
        again = c.post(f"{API}/observations/{oid}/reviews", json={"status": "confirmed"}).json()
        assert again["review_status"] == "confirmed"


def test_reanalyze_cancel_keeps_previous_result():
    with TestClient(app) as c:
        s = _site(c, "재분석 취소 구간")
        oid = _post(c, seed=6, site_id=s["id"]).json()["observation_id"]
        assert c.post(f"{API}/observations/{oid}/reanalyze/cancel").status_code == 409  # 처음 분석은 취소 안 함
        drain()
        c.post(f"{API}/observations/{oid}/reviews", json={"status": "confirmed"})
        before = c.get(f"{API}/observations/{oid}").json()
        c.post(f"{API}/observations/{oid}/reanalyze", json={})
        o = c.post(f"{API}/observations/{oid}/reanalyze/cancel").json()
        assert o["processing_status"] == "completed" and o["analysis_id"] == before["analysis_id"]
        assert o["review_status"] == "confirmed" and o["passability"] == before["passability"]
        assert any(h["txt"] == "재분석 요청 취소" for h in o["history"])
        drain()  # 취소한 분석은 워커가 가져가지 않는다
        assert c.get(f"{API}/observations/{oid}").json()["analysis_id"] == before["analysis_id"]
        statuses = [a["status"] for a in c.get(f"{API}/observations/{oid}/analyses").json()]
        assert statuses == ["completed", "cancelled"]
        # 취소 뒤 다시 요청할 수 있다
        assert c.post(f"{API}/observations/{oid}/reanalyze", json={}).json()["processing_status"] == "pending"


def test_note_goes_to_history_without_changing_state():
    with TestClient(app) as c:
        s = _site(c, "메모 구간")
        oid = _post(c, seed=7, site_id=s["id"]).json()["observation_id"]
        drain()
        r = c.post(f"{API}/observations/{oid}/notes", json={"content": " 접수번호 1234 ", "author": "교통행정과"})
        assert r.status_code == 201
        o = r.json()
        assert o["action"]["state"] == "none" and o["review_status"] == "pending"
        assert any(h["txt"] == "메모: 접수번호 1234" and h["by"] == "교통행정과" for h in o["history"])
        assert c.post(f"{API}/observations/{oid}/notes", json={"content": "   "}).status_code == 422
        assert c.post(f"{API}/observations/obs_none/notes", json={"content": "x"}).status_code == 404


def test_review_human_causes():
    with TestClient(app) as c:
        s = _site(c, "원인 수정 구간")
        oid = _post(c, seed=12, site_id=s["id"]).json()["observation_id"]
        drain()
        url = f"{API}/observations/{oid}/reviews"
        o = c.post(url, json={"status": "corrected", "human_passability": "blocked"}).json()
        assert o["human_causes"] is None and o["review"]["human_causes"] is None  # 생략 = AI 원인 그대로

        o = c.post(
            url,
            json={"status": "corrected", "human_passability": "narrowed", "human_causes": ["적치물", "차량", "적치물"]},
        ).json()
        assert o["human_causes"] == ["적치물", "차량"] and o["review"]["human_causes"] == ["적치물", "차량"]
        assert any(h["txt"] == "판정 수정 → 좁아짐 · 원인 적치물·차량" for h in o["history"])

        o = c.post(url, json={"status": "corrected", "human_passability": "passable", "human_causes": []}).json()
        assert o["human_causes"] == [] and any(h["txt"].endswith("원인 없음") for h in o["history"])

        o = c.post(url, json={"status": "confirmed", "human_causes": ["차량"]}).json()
        assert o["human_causes"] is None  # 확인은 원인을 고치지 않음
        assert (
            c.post(
                url, json={"status": "corrected", "human_passability": "blocked", "human_causes": ["고양이"]}
            ).status_code
            == 422
        )


def test_edit_note_records_edited_at():
    with TestClient(app) as c:
        s = _site(c, "메모 수정 구간")
        oid = _post(c, seed=8, site_id=s["id"]).json()["observation_id"]
        other = _post(c, seed=9, site_id=s["id"]).json()["observation_id"]
        drain()
        o = c.post(f"{API}/observations/{oid}/notes", json={"content": "접수번호 1234", "author": "교통행정과"}).json()
        note = next(h for h in o["history"] if h["id"])
        assert note["id"].startswith("note_") and note["edited_at"] is None
        assert note["content"] == "접수번호 1234" and note["author"] == "교통행정과"
        assert note["created_at"].endswith("+09:00")
        assert all(h["id"] is None and h["edited_at"] is None for h in o["history"] if h is not note)

        url = f"{API}/observations/{oid}/notes/{note['id']}"
        r = c.patch(url, json={"content": " 접수번호 1235 "})
        assert r.status_code == 200
        edited = next(h for h in r.json()["history"] if h["id"] == note["id"])
        assert edited["txt"] == "메모: 접수번호 1235" and edited["content"] == "접수번호 1235"
        assert edited["edited_at"].endswith("+09:00")
        assert edited["created_at"] == note["created_at"] and edited["t"] == note["t"]
        assert edited["author"] == "교통행정과"
        # 목록 응답에도 같은 값
        listed = next(x for x in c.get(f"{API}/observations").json() if x["observation_id"] == oid)
        assert next(h for h in listed["history"] if h["id"] == note["id"])["edited_at"] == edited["edited_at"]

        assert c.patch(url, json={"content": "  "}).status_code == 422
        assert c.patch(f"{API}/observations/{other}/notes/{note['id']}", json={"content": "x"}).status_code == 404
        assert c.patch(f"{API}/observations/{oid}/notes/note_none", json={"content": "x"}).status_code == 404


def test_action_and_before_after_compare():
    with TestClient(app) as c:
        s = _site(c, "전후 비교 구간")
        before = _post(c, seed=3, site_id=s["id"], captured_at="2026-10-20T09:00:00+09:00").json()["observation_id"]
        drain()
        o = c.post(f"{API}/observations/{before}/action", json={"state": "inspection_requested", "dept": "주차관리과"})
        assert o.json()["action"]["label"] == "현장점검 요청"
        aid = o.json()["action"]["action_id"]
        c.post(f"{API}/observations/{before}/action", json={"state": "done"})

        after = _post(c, seed=4, site_id=s["id"], captured_at="2026-10-21T09:00:00+09:00").json()["observation_id"]
        drain()
        assert c.patch(f"{API}/actions/{aid}", json={"after_observation_id": before}).status_code == 422
        act = c.patch(f"{API}/actions/{aid}", json={"after_observation_id": after}).json()
        assert act["state"] == "done" and act["dept"] == "주차관리과" and len(act["logs"]) == 3

        cmp = c.get(f"{API}/sites/{s['id']}/compare", params={"before": before, "after": after}).json()
        assert cmp["comparable"] is True and cmp["delta"] is not None
        assert cmp["actions"][0]["after_observation_id"] == after
        assert c.get(f"{API}/sites/{s['id']}/compare", params={"before": after, "after": before}).status_code == 422


def test_map_uses_latest_capture_and_reference():
    with TestClient(app) as c:
        s = _site(c, "지도 구간")
        old = _post(c, seed=5, site_id=s["id"], captured_at="2026-10-19T09:00:00+09:00").json()["observation_id"]
        drain()
        # 늦게 등록됐지만 더 오래전에 찍은 사진은 최신이 아니다
        _post(c, seed=6, site_id=s["id"], captured_at="2026-10-01T09:00:00+09:00")
        drain()
        item = next(m for m in c.get(f"{API}/map").json() if m["site_id"] == s["id"])
        assert item["latest"]["observation_id"] == old and item["reference"] is None

        # 최신 촬영이 아직 처리 전이면 이전 완료 결과를 reference로 구분해 준다
        new = _post(c, seed=7, site_id=s["id"], captured_at="2026-10-21T09:00:00+09:00").json()["observation_id"]
        item = next(m for m in c.get(f"{API}/map").json() if m["site_id"] == s["id"])
        assert item["latest"]["observation_id"] == new and item["latest"]["processing_status"] == "pending"
        assert item["reference"]["observation_id"] == old
        drain()


def test_list_filters_for_review_table():
    with TestClient(app) as c:
        s = _site(c, "검수 표 구간")
        _post(c, seed=8, site_id=s["id"])
        drain()
        rows = c.get(f"{API}/observations", params={"site_id": s["id"], "review_status": "pending"}).json()
        assert len(rows) == 1
        assert c.get(f"{API}/observations", params={"site_id": s["id"], "processing_status": "failed"}).json() == []


def test_corrupt_image_rejected_and_input_errors_not_retried():
    with TestClient(app) as c:
        s = _site(c)
        r = c.post(
            f"{API}/observations",
            data={"captured_at": "2026-10-18T14:32:00+09:00", "site_id": s["id"]},
            files={"file": ("a.jpg", b"not an image", "image/jpeg")},
        )
        assert r.status_code == 422
        r = c.post(
            f"{API}/observations",
            data={"captured_at": "2026-10-18T14:32:00+09:00", "site_id": s["id"]},
            files={"file": ("a.txt", b"hi", "text/plain")},
        )
        assert r.status_code == 415
        assert _post(c, site_id=s["id"], captured_at="2026-10-18T14:32:00").status_code == 422

        # 깨진 영상은 입력 오류 → 재시도 없이 실패
        r = c.post(
            f"{API}/observations",
            data={"captured_at": "2026-10-18T14:32:00+09:00", "site_id": s["id"]},
            files={"file": ("v.mp4", b"broken", "video/mp4")},
        )
        oid = r.json()["observation_id"]
        drain()
        got = c.get(f"{API}/observations/{oid}").json()
        assert got["processing_status"] == "failed" and got["attempts"] == 1 and got["passability"] is None


def test_inspect_reads_exif():
    img = Image.new("RGB", (64, 48), (120, 120, 120))
    exif = Image.Exif()
    exif[0x0132] = "2026:10:20 14:30:00"
    gps = exif.get_ifd(0x8825)
    gps.update({1: "N", 2: (37.0, 22.0, 30.0), 3: "E", 4: (127.0, 7.0, 30.0)})
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    with TestClient(app) as c:
        r = c.post(f"{API}/observations/inspect", files={"file": ("e.jpg", buf.getvalue(), "image/jpeg")}).json()
        assert abs(r["lat"] - 37.375) < 1e-6 and abs(r["lng"] - 127.125) < 1e-6
        assert r["captured_at"].startswith("2026-10-20T05:30:00")
        assert r["candidates"] is not None


def test_root_redirects_to_docs():
    with TestClient(app) as c:
        r = c.get("/", follow_redirects=False)
        assert r.status_code == 307 and r.headers["location"] == "/docs"
        assert c.get("/docs").status_code == 200


def test_location_is_site_point_so_frontend_groups_by_site():
    with TestClient(app) as c:
        s = _site(c, "지점 묶음 구간")
        a = _post(c, seed=8, site_id=s["id"], lat=LAT + 0.00003, lng=LNG - 0.00002).json()
        b = _post(c, seed=9, site_id=s["id"], lat=LAT - 0.00004, lng=LNG + 0.00001).json()
        assert a["location"] == b["location"] == {"latitude": s["lat"], "longitude": s["lng"]}
        assert a["capture_location"] == {"latitude": LAT + 0.00003, "longitude": LNG - 0.00002}
        nogps = _post(c, seed=10, site_id=s["id"], lat=None, lng=None).json()
        assert nogps["location"] == a["location"] and nogps["capture_location"] is None


def test_created_at_is_registration_time_not_capture_time():
    with TestClient(app) as c:
        s = _site(c, "등록 시각 구간")
        before = datetime.now(UTC)
        a = _post(c, seed=11, site_id=s["id"]).json()
        assert a["captured_at"] == "2026-10-18T14:32:00+09:00"
        assert a["created_at"].endswith("+09:00")
        assert datetime.fromisoformat(a["created_at"]) >= before - timedelta(seconds=1)
        listed = next(o for o in c.get(f"{API}/observations").json() if o["observation_id"] == a["observation_id"])
        assert listed["created_at"] == a["created_at"]


VWORLD_OK = {
    "response": {
        "status": "OK",
        "result": [
            {
                "text": "경기도 성남시 분당구 이매동 129-1",
                "structure": {"level2": "성남시 분당구", "level4L": "이매동", "level4A": "이매1동"},
            }
        ],
    }
}


def test_worker_fills_area_and_addr(monkeypatch):
    from app.config import get_settings
    from app.services import geocode

    monkeypatch.setattr(get_settings(), "vworld_key", "test-key")
    calls = []
    monkeypatch.setattr(geocode, "_fetch", lambda lat, lng: calls.append((lat, lng)) or VWORLD_OK)
    geocode.reverse.cache_clear()
    with TestClient(app) as c:
        s = _site(c, "역지오코딩 구간")
        oid = _post(c, seed=11, site_id=s["id"]).json()["observation_id"]
        kept = _post(c, seed=12, site_id=s["id"], area="직접 입력", addr="직접 주소").json()["observation_id"]
        drain()
        o = c.get(f"{API}/observations/{oid}").json()
        assert o["area"] == "분당구 이매동"
        assert o["addr"] == "경기도 성남시 분당구 이매동 129-1 (행정동 이매1동)"
        k = c.get(f"{API}/observations/{kept}").json()
        assert (k["area"], k["addr"]) == ("직접 입력", "직접 주소")  # 등록 때 넣은 값은 그대로
        assert calls.count((LAT, LNG)) == 1  # 같은 좌표는 한 번만 조회 (다른 테스트의 대기 관측도 함께 처리됨)

    # 조회 실패해도 분석은 완료된다
    monkeypatch.setattr(geocode, "_fetch", lambda lat, lng: {"response": {"status": "ERROR"}})
    geocode.reverse.cache_clear()
    with TestClient(app) as c:
        s = _site(c, "역지오코딩 실패 구간")
        oid = _post(c, seed=13, site_id=s["id"], lat=LAT + 0.001, lng=LNG).json()["observation_id"]
        drain()
        o = c.get(f"{API}/observations/{oid}").json()
        assert o["processing_status"] == "completed" and o["area"] is None


def test_analysis_records_timings():
    with TestClient(app) as c:
        oid = _post(c, seed=21, new_site_name="처리 시간 구간").json()["observation_id"]
        drain()
        ana = c.get(f"{API}/observations/{oid}/analyses").json()[0]
        assert ana["status"] == "completed" and ana["started_at"] is not None
        t = ana["timings"]
        assert {"queue_wait", "address", "ai", "save_files", "risk", "total"} <= set(t)
        assert t["total"] >= t["ai"] >= 0 and t["queue_wait"] >= 0
        assert ana["evidence"]["timings"] == t


def test_worker_survives_errors(monkeypatch):
    """DB 연결이 잠깐 끊겨도(예: DNS 실패) 워커 스레드가 죽지 않고 다시 돈다"""
    import threading

    monkeypatch.setattr(worker, "MAX_BACKOFF_SEC", 0.01)
    monkeypatch.setattr(worker.get_settings(), "worker_poll_sec", 0.001)
    stop, calls = threading.Event(), []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("failed to resolve host")
        stop.set()
        return False

    monkeypatch.setattr(worker, "run_once", flaky)
    worker.loop(stop)
    assert len(calls) == 3 and "failed to resolve host" in worker._state["last_error"]


def test_health_reports_worker():
    with TestClient(app) as c:
        w = c.get(f"{API}/health").json()["worker"]
        assert w["alive"] is None  # 테스트는 API 안에서 워커를 돌리지 않음
        assert isinstance(w["pending"], int) and isinstance(w["processing"], int)

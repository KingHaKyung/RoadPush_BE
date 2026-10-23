"""배포용 지도·CCTV 프록시: 외부 호출은 가짜 _http로 바꿔 경로·형식·키 비노출만 확인한다"""

import json
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.api import proxy
from app.config import get_settings
from app.main import app


@pytest.fixture
def keys(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "vworld_key", "VW-KEY")
    monkeypatch.setattr(s, "utic_api_key", "UTIC-KEY")
    monkeypatch.setattr(s, "its_api_key", "ITS-KEY")
    monkeypatch.setattr(proxy.utic, "info", {})
    monkeypatch.setattr(proxy.utic, "cookie", "")
    monkeypatch.setattr(proxy.utic, "last", 0.0)
    monkeypatch.setitem(proxy._its_cache, "body", None)
    calls: list[tuple[str, dict]] = []

    def fake(url, headers=None, timeout=15):
        calls.append((url, headers or {}))
        if "vworld" in url:
            return 200, {"content-type": "image/png"}, b"PNG"
        if "openDataCctvStream" in url:
            return 200, {"set-cookie": ["JSESSIONID=abc; Path=/"]}, b"<html>"
        if "getCctvInfoById" in url:
            info = {"CCTVID": "L010", "CCTVNAME": "서현역", "KIND": "KB", "CCTVIP": "1.2.3.4", "CH": "", "PORT": None}
            return 200, {}, json.dumps(info).encode()
        if "its.go.kr" in url:
            data = [
                {"cctvname": "[수도권] 판교", "coordx": "127.1", "coordy": "37.4", "cctvurl": "http://hls/a.m3u8"},
                {"cctvname": "[수도권] 서현", "coordx": "127.12", "coordy": "37.38", "cctvurl": "http://hls/b.m3u8"},
            ]
            return 200, {}, json.dumps({"response": {"data": data}}).encode()
        raise AssertionError(url)

    monkeypatch.setattr(proxy, "_http", fake)
    return calls


def test_vworld_tile_adds_key_and_referer(keys):
    with TestClient(app) as c:
        r = c.get("/vworld/wmts/Base/16/25000/55000.png")
        assert r.status_code == 200 and r.content == b"PNG" and r.headers["content-type"] == "image/png"
        url, headers = keys[0]
        assert url == "https://api.vworld.kr/req/wmts/1.0.0/VW-KEY/Base/16/25000/55000.png"
        assert headers["Referer"].endswith("/")
        assert c.get("/vworld/wmts/Base/..%2F..%2Fx").status_code in (400, 404)


def test_utic_player_redirect_with_session_and_cache(keys):
    with TestClient(app) as c:
        r = c.get("/api/utic/player", params={"cctvid": "L010"}, follow_redirects=False)
        assert r.status_code == 302
        loc = urlparse(r.headers["location"])
        q = parse_qs(loc.query)
        assert loc.path == "/jsp/map/openDataCctvStream.jsp" and q["kind"] == ["KB"] and q["cctvch"] == ["undefined"]
        assert keys[1][1]["Cookie"] == "JSESSIONID=abc"  # 세션을 연 뒤 카메라 정보를 받는다
        n = len(keys)
        assert c.get("/api/utic/player", params={"cctvid": "L010"}, follow_redirects=False).status_code == 302
        assert len(keys) == n  # 같은 카메라는 다시 부르지 않는다 (UTIC IP 차단 방지)
        assert c.get("/api/utic/player", params={"cctvid": "<x>"}).status_code == 400


def test_its_list_hides_stream_url_and_key(keys):
    with TestClient(app) as c:
        j = c.get("/api/its/cctvs").json()
        assert j["source"] == "ITS 국가교통정보센터" and len(j["cctvs"]) == 4  # ex·its 각 2
        cam = j["cctvs"][0]
        assert cam["name"] == "판교" and cam["center"] == "수도권" and cam["road"] == "고속도로"
        assert "ITS-KEY" not in json.dumps(j) and "m3u8" not in json.dumps(j)
        n = len(keys)
        c.get("/api/its/cctvs")
        assert len(keys) == n  # 캐시

        s = c.get("/api/its/stream", params={"type": "its", "name": "[수도권] 서현", "lat": 37.38, "lng": 127.12})
        assert s.json() == {"hls": "http://hls/b.m3u8"}
        assert c.get("/api/its/stream", params={"type": "x", "lat": 1, "lng": 2}).status_code == 400


def test_missing_keys_fail_clearly(monkeypatch):
    s = get_settings()
    for k in ("vworld_key", "utic_api_key", "its_api_key"):
        monkeypatch.setattr(s, k, "")
    with TestClient(app) as c:
        assert c.get("/vworld/wmts/Base/1/1/1.png").status_code == 503
        r = c.get("/api/utic/player", params={"cctvid": "L010"})
        assert r.status_code == 502 and "UTIC_API_KEY" in r.text
        assert "ITS_API_KEY" in c.get("/api/its/cctvs").json()["error"]

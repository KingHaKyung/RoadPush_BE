"""배포 설정: HTTPS 주소로 사진 URL 만들기, Vercel 미리보기 주소 CORS"""

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app

API = "/api/v1"
ORIGIN = "https://road-push-fe-git-feat-x-kinghakyung.vercel.app"


def test_public_base_url_makes_https_image_urls(monkeypatch):
    monkeypatch.setattr(get_settings(), "public_base_url", "https://1-2-3-4.sslip.io/")
    with TestClient(app) as c:
        site = c.post(f"{API}/sites", json={"name": "배포 확인", "lat": 37.3868, "lng": 127.1273}).json()
        r = c.post(
            f"{API}/observations",
            data={"captured_at": "2026-10-25T10:00:00+09:00", "site_id": site["id"], "source": "upload"},
            files={"file": ("a.jpg", _jpg(), "image/jpeg")},
        )
        assert r.status_code == 202, r.text
        assert r.json()["images"]["original"].startswith("https://1-2-3-4.sslip.io/files/")


def test_vercel_preview_origin_allowed():
    with TestClient(app) as c:
        ok = c.get(f"{API}/health", headers={"Origin": ORIGIN})
        assert ok.headers.get("access-control-allow-origin") == ORIGIN
        bad = c.get(f"{API}/health", headers={"Origin": "https://evil.example.com"})
        assert "access-control-allow-origin" not in bad.headers


def _jpg() -> bytes:
    import cv2
    import numpy as np

    return cv2.imencode(".jpg", np.full((120, 160, 3), 128, np.uint8))[1].tobytes()

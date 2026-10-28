"""배포 설정: HTTPS 주소로 사진 URL 만들기, Vercel 미리보기 주소 CORS"""

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app

API = "/api/v1"
ORIGIN = "https://road-push-fe-git-feat-x-kinghakyung.vercel.app"


def test_public_base_url_makes_https_image_urls(monkeypatch):
    monkeypatch.setattr(get_settings(), "public_base_url", "https://desktop-han01.tail5a740d.ts.net/")
    with TestClient(app) as c:
        site = c.post(f"{API}/sites", json={"name": "배포 확인", "lat": 37.3868, "lng": 127.1273}).json()
        r = c.post(
            f"{API}/observations",
            data={"captured_at": "2026-10-25T10:00:00+09:00", "site_id": site["id"], "source": "upload"},
            files={"file": ("a.jpg", _jpg(), "image/jpeg")},
        )
        assert r.status_code == 202, r.text
        assert r.json()["images"]["original"].startswith("https://desktop-han01.tail5a740d.ts.net/files/")


def test_vercel_preview_origin_allowed():
    with TestClient(app) as c:
        ok = c.get(f"{API}/health", headers={"Origin": ORIGIN})
        assert ok.headers.get("access-control-allow-origin") == ORIGIN
        bad = c.get(f"{API}/health", headers={"Origin": "https://evil.example.com"})
        assert "access-control-allow-origin" not in bad.headers


def test_health_shows_backend_and_ai_commit(monkeypatch, tmp_path):
    """백엔드·AI 저장소의 git 커밋을 보여 주고, AI 폴더가 없거나 git이 아니면 commit=None"""
    from app.services import versions

    ai = tmp_path / "RoadPush_AI"
    ai.mkdir()
    monkeypatch.setattr(get_settings(), "roadpush_ai_root", ai)
    versions.ai_version.cache_clear()
    try:
        with TestClient(app) as c:
            h = c.get(f"{API}/health").json()
        assert h["ai"]["commit"] is None and "git" in h["ai"]["error"]
        assert len(h["backend"]["commit"]) == 40 and isinstance(h["backend"]["dirty"], bool)

        monkeypatch.setattr(get_settings(), "roadpush_ai_root", tmp_path / "없음")
        versions.ai_version.cache_clear()
        assert versions.ai_version()["commit"] is None
    finally:
        versions.ai_version.cache_clear()


def _jpg() -> bytes:
    import cv2
    import numpy as np

    return cv2.imencode(".jpg", np.full((120, 160, 3), 128, np.uint8))[1].tobytes()

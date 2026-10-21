"""한국어 Windows(cp949)에서 터진 문제의 재발 방지 (docs/TROUBLESHOOTING.md)"""

from pathlib import Path

import cv2
import numpy as np

from app.ai_engine import imageio, pipeline
from app.config import Settings

ROOT = Path(__file__).resolve().parents[1]


def test_alembic_ini_is_ascii():
    """Alembic은 alembic.ini를 OS 기본 인코딩으로 읽는다 → 한글이 있으면 cp949에서 UnicodeDecodeError"""
    assert (ROOT / "alembic.ini").read_bytes().isascii()


def test_server_does_not_read_alembic_ini():
    from app.db import alembic_config

    assert alembic_config().config_file_name is None


def test_env_file_is_read_as_utf8(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# 한국어 주석 · 구간 후보 반경\nSITE_SEARCH_RADIUS_M=77\n", encoding="utf-8")
    monkeypatch.delenv("SITE_SEARCH_RADIUS_M", raising=False)
    assert Settings.model_config["env_file_encoding"] == "utf-8"
    assert Settings(_env_file=env).site_search_radius_m == 77


def test_image_io_with_korean_path(tmp_path):
    d = tmp_path / "바탕 화면" / "도로 사진"
    d.mkdir(parents=True)
    img = np.full((40, 60, 3), 128, np.uint8)
    p = d / "보도.jpg"
    assert imageio.imwrite(p, img)
    assert imageio.imread(p).shape == (40, 60, 3)
    assert pipeline.read_frames(str(p), "image")[0].shape == (40, 60, 3)
    assert imageio.imread(d / "없는파일.jpg") is None


def test_video_path_prefers_relative_ascii(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "storage").mkdir()
    v = tmp_path / "storage" / "a.mp4"
    cv2.VideoWriter(str(v), cv2.VideoWriter_fourcc(*"mp4v"), 5, (8, 8)).release()
    assert imageio.video_path(v) in (str(v), "storage/a.mp4", "storage\\a.mp4")

"""파일 저장소. MVP는 로컬 폴더, 나중에 MinIO/S3로 바꿔도 키 형식은 그대로 둔다."""

import shutil
from pathlib import Path

import cv2
import numpy as np

from ..ai_engine import imageio
from ..config import get_settings


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError(f"잘못된 저장 키: {key}")
        return p

    def put_bytes(self, key: str, data: bytes) -> str:
        p = self.path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return key

    def put_image(self, key: str, img: np.ndarray) -> str:
        ok, buf = cv2.imencode(Path(key).suffix or ".png", img)
        if not ok:
            raise RuntimeError(f"이미지 인코딩 실패: {key}")
        return self.put_bytes(key, buf.tobytes())  # 바이트로 저장 → 한글 경로에서도 동작

    def delete_dir(self, prefix: str) -> None:
        """키 접두사 폴더를 통째로 지운다 (예: observations/obs_x). 없으면 무시"""
        shutil.rmtree(self.path(prefix), ignore_errors=True)

    def read_image(self, key: str) -> np.ndarray:
        img = imageio.imread(self.path(key), cv2.IMREAD_UNCHANGED)
        if img is None:
            raise FileNotFoundError(key)
        return img


_storage: LocalStorage | None = None


def get_storage() -> LocalStorage:
    global _storage
    if _storage is None:
        _storage = LocalStorage(get_settings().storage_dir)
    return _storage

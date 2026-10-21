"""
보도·차도 semantic segmentation 어댑터. 모든 어댑터는 SurfaceResult(통합 라벨)를 돌려준다.
  dummy       모델 없이 API·프론트 연결 확인용 가짜 장면 (차량 포함)
  selectstar  SelectStar DeepLab v3+ (마스터 문서 8장 잠정 우선 후보) — TorchScript 가중치
  hf          HuggingFace semantic 모델 (Mask2Former·SegFormer 비교용)
"""

from functools import lru_cache
from typing import Protocol

import cv2
import numpy as np

from ..types import SurfaceResult


class SurfaceModel(Protocol):
    version: str

    def predict(self, image_bgr: np.ndarray) -> SurfaceResult: ...


def resize_short(img: np.ndarray, short: int) -> np.ndarray:
    h, w = img.shape[:2]
    s = short / min(h, w)
    if s >= 1:
        return img
    return cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)


@lru_cache
def load_surface(kind: str, weights: str = "", device: str = "cuda", short_side: int = 768) -> SurfaceModel:
    """모델은 워커에서 한 번만 올린다"""
    if kind == "dummy":
        from .dummy import DummySurface

        return DummySurface()
    if kind == "selectstar":
        from .selectstar import SelectStarDeepLab

        return SelectStarDeepLab(weights, device, short_side)
    if kind == "hf":
        from .hf import HFSemantic

        return HFSemantic(weights, device, short_side)
    raise ValueError(f"알 수 없는 SURFACE_MODEL: {kind}")

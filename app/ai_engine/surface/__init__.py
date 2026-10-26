"""
dummy 엔진의 가짜 노면 모델. 실제 분석은 roadpush_ai 엔진(app/ai_engine/roadpush.py)이 맡는다.
  dummy  모델 없이 API·워커·프론트 연결 확인용 가짜 장면 (차량 포함)
"""

from functools import lru_cache

import cv2
import numpy as np


def resize_short(img: np.ndarray, short: int) -> np.ndarray:
    h, w = img.shape[:2]
    s = short / min(h, w)
    if s >= 1:
        return img
    return cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)


@lru_cache
def load_dummy():
    from .dummy import DummySurface

    return DummySurface()

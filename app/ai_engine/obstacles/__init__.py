"""
차량·비차량 장애물 instance segmentation 어댑터.
  none  노면 모델만 사용 (dummy 노면은 차량을 직접 그린다)
  yolo  Ultralytics 모델 여러 개 (예: YOLO11n-seg COCO 차량 기본선 + YOLOE 비차량 텍스트 카테고리)

마스터 문서 8장: 포괄 텍스트 'obstacle' 하나가 아니라 구체 카테고리로 탐지한 뒤 유효 mask를 통합한다.
"""

from functools import lru_cache
from typing import Protocol

import numpy as np

from ..types import Instance


class ObstacleModel(Protocol):
    version: str

    def predict(self, image_bgr: np.ndarray) -> list[Instance]: ...


@lru_cache
def load_obstacles(kind: str, specs: tuple[str, ...] = (), device: str = "cuda") -> ObstacleModel | None:
    if kind == "none":
        return None
    if kind == "yolo":
        from .yolo import UltralyticsObstacles

        return UltralyticsObstacles(list(specs), device)
    raise ValueError(f"알 수 없는 OBSTACLE_MODEL: {kind}")

from dataclasses import dataclass, field

import numpy as np


@dataclass
class SurfaceResult:
    """노면 semantic segmentation 결과 (통합 라벨로 변환된 상태)"""

    class_map: np.ndarray  # H×W uint8, labels.L
    confidence: np.ndarray | None = None  # H×W float32 0~1
    image: np.ndarray | None = None  # 추론 크기의 BGR (overlay용)


@dataclass
class Instance:
    name: str  # 모델이 낸 클래스 이름 (예: car, traffic cone)
    mask: np.ndarray  # H×W bool, SurfaceResult와 같은 크기
    score: float


@dataclass
class SceneResult:
    """노면 + 장애물 instance를 합친 장면. 통과 판정 규칙의 입력"""

    class_map: np.ndarray
    confidence: np.ndarray | None
    image: np.ndarray | None
    instances: list[Instance] = field(default_factory=list)
    versions: dict[str, str] = field(default_factory=dict)

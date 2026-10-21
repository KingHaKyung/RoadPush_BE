import hashlib

import numpy as np

from ..labels import L
from ..types import SurfaceResult
from . import resize_short


class DummySurface:
    """
    모델 없이 API·워커·프론트 연결을 확인하기 위한 가짜 장면.
    화면 아래로 넓어지는 사다리꼴 보도와 연석·차도를 그리고, 이미지 해시에 따라 보도 위 차량 크기를 바꿔
    판정이 골고루 나오게 한다. 차량은 장애물 모델 없이도 보이도록 여기서 같이 그린다.
    """

    version = "dummy-0"

    def predict(self, image_bgr: np.ndarray) -> SurfaceResult:
        img = resize_short(image_bgr, 480)
        h, w = img.shape[:2]
        cm = np.full((h, w), L.BACKGROUND, np.uint8)
        top = h // 3
        for r in range(top, h):
            t = (r - top) / (h - top)
            curb = int(w * (0.55 - 0.25 * t))
            outer = int(w * (0.75 + 0.25 * t))
            cm[r, : curb - 3] = L.ROAD
            cm[r, curb - 3 : curb] = L.CURB
            cm[r, curb:outer] = L.SIDEWALK
        k = hashlib.md5(img.tobytes()).digest()[0] / 255  # 0~1
        y0, y1 = int(h * 0.55), int(h * 0.85)
        for r in range(y0, y1):
            cs = np.flatnonzero(cm[r] == L.SIDEWALK)
            if len(cs):
                cm[r, cs[0] : cs[0] + int(len(cs) * k * 0.9)] = L.OBSTACLE
        return SurfaceResult(cm, np.full((h, w), 0.85, np.float32), img)

"""
SelectStar DeepLab v3+ (https://github.com/selectstarofficial/segmentation-selectstar) 어댑터.

백엔드는 저자 코드를 직접 들고 오지 않고, 모델 팀이 내보낸 TorchScript 파일 하나를 받는다.
  model_weights/selectstar_deeplab.pt   입력 (1,3,H,W) float, 출력 (1,7,H,W) logits

모델 팀이 내보내는 예 (저자 저장소에서):
    model = ...  # 저자 코드로 checkpoint 로드, eval()
    ts = torch.jit.trace(model, torch.randn(1, 3, 512, 512))
    ts.save("selectstar_deeplab.pt")

전처리(정규화 평균·표준편차, 저자 입력 크기 vs 비율 유지)는 마스터 문서 8장의 '저자입력/비율유지' 비교에 맞춰
SELECTSTAR_MEAN·SELECTSTAR_STD·SELECTSTAR_INPUT 설정으로 맞춘다. 기본값은 ImageNet 정규화 + 비율 유지.
"""

import cv2
import numpy as np

from ...config import get_settings
from ..labels import SELECTSTAR_TO_L
from ..types import SurfaceResult
from . import resize_short


class SelectStarDeepLab:
    def __init__(self, weights: str, device: str = "cuda", short_side: int = 768):
        import torch

        self.torch = torch
        self.device = device if (device == "cpu" or torch.cuda.is_available()) else "cpu"
        self.model = torch.jit.load(weights, map_location=self.device).eval()
        s = get_settings()
        self.mean = np.array(s.selectstar_mean, np.float32)
        self.std = np.array(s.selectstar_std, np.float32)
        self.fixed = tuple(s.selectstar_input) if s.selectstar_input else None  # (W, H) 저자 입력 크기
        self.short_side = short_side
        self.lut = np.array(SELECTSTAR_TO_L, np.uint8)
        self.version = f"selectstar-deeplabv3plus:{weights.rsplit('/', 1)[-1]}"

    def predict(self, image_bgr: np.ndarray) -> SurfaceResult:
        torch = self.torch
        img = resize_short(image_bgr, self.short_side)
        h, w = img.shape[:2]
        inp = cv2.resize(img, self.fixed) if self.fixed else img
        x = (cv2.cvtColor(inp, cv2.COLOR_BGR2RGB).astype(np.float32) / 255 - self.mean) / self.std
        t = torch.from_numpy(x.transpose(2, 0, 1))[None].to(self.device)
        with torch.inference_mode():
            logits = self.model(t)
            if isinstance(logits, (tuple, list)):
                logits = logits[0]
            logits = torch.nn.functional.interpolate(logits.float(), size=(h, w), mode="bilinear", align_corners=False)
            conf, idx = logits.softmax(1).max(1)
        raw = idx[0].cpu().numpy()
        return SurfaceResult(self.lut[raw], conf[0].cpu().numpy().astype(np.float32), img)

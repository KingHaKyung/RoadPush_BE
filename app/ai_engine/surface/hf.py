"""
HuggingFace semantic segmentation 어댑터 (비교·대안용).
Mask2Former(예: facebook/mask2former-swin-tiny-cityscapes-semantic)와 SegFormer를 지원한다.
클래스 이름은 labels.NAME_TO_L로 통합 라벨에 매핑한다.
"""

import cv2
import numpy as np

from ..labels import name_label
from ..types import SurfaceResult
from . import resize_short


class HFSemantic:
    def __init__(self, model_id: str, device: str = "cuda", short_side: int = 768):
        import torch
        from transformers import AutoImageProcessor, AutoModelForSemanticSegmentation, AutoModelForUniversalSegmentation

        self.torch = torch
        self.device = device if (device == "cpu" or torch.cuda.is_available()) else "cpu"
        self.dtype = torch.float16 if self.device.startswith("cuda") else torch.float32
        self.processor = AutoImageProcessor.from_pretrained(model_id)
        self.universal = "mask2former" in model_id.lower()
        cls = AutoModelForUniversalSegmentation if self.universal else AutoModelForSemanticSegmentation
        self.model = cls.from_pretrained(model_id, dtype=self.dtype).to(self.device).eval()
        id2name = {int(k): v for k, v in self.model.config.id2label.items()}
        self.lut = np.array([name_label(id2name.get(i, "")) for i in range(max(id2name) + 1)], np.uint8)
        self.short_side = short_side
        self.version = f"hf:{model_id.rsplit('/', 1)[-1]}"

    def predict(self, image_bgr: np.ndarray) -> SurfaceResult:
        torch = self.torch
        img = resize_short(image_bgr, self.short_side)
        h, w = img.shape[:2]
        inputs = self.processor(images=cv2.cvtColor(img, cv2.COLOR_BGR2RGB), return_tensors="pt")
        pv = inputs["pixel_values"].to(self.device, self.dtype)
        with torch.inference_mode():
            out = self.model(pixel_values=pv)
            if self.universal:
                cls = out.class_queries_logits.softmax(-1)[..., :-1]  # 마지막은 '없음'
                seg = torch.einsum("bqc,bqhw->bchw", cls, out.masks_queries_logits.sigmoid())
            else:
                seg = out.logits.softmax(1)
            seg = torch.nn.functional.interpolate(seg.float(), size=(h, w), mode="bilinear", align_corners=False)
            prob = seg / seg.sum(1, keepdim=True).clamp_min(1e-6)
            conf, idx = prob.max(1)
        return SurfaceResult(self.lut[idx[0].cpu().numpy()], conf[0].cpu().numpy().astype(np.float32), img)

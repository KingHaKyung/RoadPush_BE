"""
Ultralytics 장애물 모델 (YOLO11n-seg, YOLOE).
OBSTACLE_WEIGHTS 항목 형식: "가중치 경로" 또는 "가중치 경로|카테고리1,카테고리2,…" (YOLOE 텍스트 카테고리)
  예: ["model_weights/yolo11n-seg.pt", "model_weights/yoloe-26s-seg.pt|traffic cone,barricade,kick scooter,box"]

라이선스: Ultralytics는 AGPL-3.0(또는 Enterprise). 공개·배포 형태에 맞는지 팀이 확인한다 (마스터 문서 12장).
"""

import cv2
import numpy as np

from ..labels import CAUSE_OF, PERSON_NAMES
from ..types import Instance

KEEP = set(CAUSE_OF) | PERSON_NAMES  # 이 이름만 남긴다. 상시 고정시설·기타 COCO 클래스는 버림


class UltralyticsObstacles:
    def __init__(self, specs: list[str], device: str = "cuda", conf: float = 0.25):
        from ultralytics import YOLO

        self.models = []
        for spec in specs:
            path, _, prompt = spec.partition("|")
            m = YOLO(path)
            if prompt:  # YOLOE: 구체 텍스트 카테고리
                names = [p.strip() for p in prompt.split(",") if p.strip()]
                m.set_classes(names, m.get_text_pe(names))
            self.models.append(m)
        self.device = device
        self.conf = conf
        self.version = "+".join(spec.split("|")[0].rsplit("/", 1)[-1] for spec in specs)

    def predict(self, image_bgr: np.ndarray) -> list[Instance]:
        h, w = image_bgr.shape[:2]
        out: list[Instance] = []
        for m in self.models:
            res = m.predict(image_bgr, conf=self.conf, device=self.device, verbose=False, retina_masks=True)[0]
            if res.masks is None:
                continue
            masks = res.masks.data.cpu().numpy()
            for i, cls_id in enumerate(res.boxes.cls.cpu().numpy().astype(int)):
                name = str(res.names[cls_id]).lower()
                if name not in KEEP:
                    continue
                mk = masks[i]
                if mk.shape != (h, w):
                    mk = cv2.resize(mk, (w, h), interpolation=cv2.INTER_NEAREST)
                out.append(Instance(name, mk > 0.5, float(res.boxes.conf[i])))
        return out

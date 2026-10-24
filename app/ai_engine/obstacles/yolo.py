"""
Ultralytics 장애물 모델 (YOLO11n-seg, YOLOE).
OBSTACLE_WEIGHTS 항목 형식: "가중치 경로" 또는 "가중치 경로|카테고리1,카테고리2,…" (YOLOE 텍스트 카테고리)
  예: ["model_weights/yolo11n-seg.pt", "model_weights/yoloe-26s-seg.pt|traffic cone,barricade,kick scooter,box"]

segmentation 모델(-seg)이면 mask를, 박스만 내는 detection 모델이면 박스 사각형을 영역으로 쓴다.
박스는 물체보다 넓어(바닥·배경 포함) 점유율이 높게 나오므로 version에 '(bbox)'를 붙여 결과에 남긴다.
클래스 이름은 labels.CAUSE_OF에 있는 것(+사람·동물)만 쓰고, 모르는 이름은 처음 한 번 경고하고 버린다.

라이선스: Ultralytics는 AGPL-3.0(또는 Enterprise). 공개·배포 형태에 맞는지 팀이 확인한다 (마스터 문서 12장).
"""

import logging

import cv2
import numpy as np

from ..labels import CAUSE_OF, PERSON_NAMES
from ..types import Instance

KEEP = set(CAUSE_OF) | PERSON_NAMES  # 이 이름만 남긴다. 상시 고정시설·기타 COCO 클래스는 버림
log = logging.getLogger("roadpush.obstacles")


class UltralyticsObstacles:
    def __init__(self, specs: list[str], device: str = "cuda", conf: float = 0.25):
        from ultralytics import YOLO

        self.models = []
        self.dropped: set[str] = set()
        for spec in specs:
            path, _, prompt = spec.partition("|")
            m = YOLO(path)
            if prompt:  # YOLOE: 구체 텍스트 카테고리
                names = [p.strip() for p in prompt.split(",") if p.strip()]
                m.set_classes(names, m.get_text_pe(names))
            self.models.append(m)
        self.device = device
        self.conf = conf
        self.version = "+".join(
            spec.split("|")[0].rsplit("/", 1)[-1] + ("" if m.task == "segment" else "(bbox)")
            for spec, m in zip(specs, self.models, strict=True)
        )

    def predict(self, image_bgr: np.ndarray) -> list[Instance]:
        h, w = image_bgr.shape[:2]
        out: list[Instance] = []
        for m in self.models:
            res = m.predict(image_bgr, conf=self.conf, device=self.device, verbose=False, retina_masks=True)[0]
            if res.boxes is None or len(res.boxes) == 0:
                continue
            masks = res.masks.data.cpu().numpy() if res.masks is not None else None
            boxes = res.boxes.xyxy.cpu().numpy()
            for i, cls_id in enumerate(res.boxes.cls.cpu().numpy().astype(int)):
                name = str(res.names[cls_id]).strip().lower()
                if name not in KEEP:
                    if name not in self.dropped:
                        self.dropped.add(name)
                        log.warning("장애물 모델 클래스 '%s'는 labels.CAUSE_OF에 없어 버립니다", name)
                    continue
                if masks is not None:
                    mk = masks[i]
                    if mk.shape != (h, w):
                        mk = cv2.resize(mk, (w, h), interpolation=cv2.INTER_NEAREST)
                    mk = mk > 0.5
                else:  # detection 모델: 박스 사각형
                    x1, y1, x2, y2 = np.round(boxes[i]).astype(int)
                    mk = np.zeros((h, w), bool)
                    mk[max(0, y1) : min(h, y2), max(0, x1) : min(w, x2)] = True
                out.append(Instance(name, mk, float(res.boxes.conf[i])))
        return out

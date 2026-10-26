"""
장애물 원인 (무엇이 막았나 → 처리 유형 A~D). RoadPush_AI/obstacle_yolo 모델 팀 코드를 그대로 불러 쓴다.
  모델·임계값·처리 유형  obstacle_yolo/service/config.yaml (클래스별 conf, imgsz, 가중치 SHA-256)
  판정 규칙              obstacle_yolo/service/attach_cause.py (phase6_blocker_overlap_v1)
                         Phase 6 선택 surface의 blocker 덩어리와 겹치는 탐지 박스만 원인으로 인정
                         (덩어리의 30%가 박스 안 또는 박스의 10%가 덩어리 위). 길을 막지 않는 탐지는 무시
규칙·임계값을 바꿀 때는 모델 팀 폴더만 고친다 (백엔드에 같은 규칙을 따로 두지 않는다).

원인 배지 이름은 labels.CAUSE_OF(vehicle → 차량 …) = 프론트 처리 경로 키.
Ultralytics(YOLO11)는 AGPL-3.0 → 공개 서비스 전 라이선스 확인 (obstacle_yolo/README.md).
"""

import importlib.util
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

import numpy as np

from ..config import get_settings
from .labels import cause_of
from .roadpush import ModelMissing, resolve_device

_lock = threading.Lock()
_state: dict = {}


@dataclass
class CauseResult:
    causes: list[str]  # 원인 배지 (겹친 면적 큰 순)
    # attach_cause 상태: attributed | partially_attributed | unclassified_only | no_blocker (+ no_selected_surface)
    status: str
    types: list[str] = field(default_factory=list)  # 처리 유형 A~D
    # 민원 자동 전달 가능: 원인이 있고 7-B 결과가 authoritative일 때만 (attach_cause와 같은 기준)
    auto_routable: bool = False
    components: list[dict] = field(default_factory=list)
    detections: list[dict] = field(default_factory=list)
    overlay: np.ndarray | None = None  # blocker 덩어리(원인 있음 빨강·미분류 노랑) + 탐지 박스
    version: str = ""
    policy: str = ""


def _service_module(root: Path) -> ModuleType:
    service = root / "service"
    path = service / "attach_cause.py"
    if not path.is_file():
        raise ModelMissing(f"장애물 원인 코드가 없습니다: {path} (OBSTACLE_YOLO_ROOT)")
    if str(service) not in sys.path:
        sys.path.insert(0, str(service))  # attach_cause.py가 같은 폴더의 predict.py를 import한다
    spec = importlib.util.spec_from_file_location("roadpush_obstacle_attach_cause", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def badges(components: list[dict]) -> list[str]:
    """attach_cause 덩어리별 원인 → 원인 배지 (겹친 면적 큰 순)"""
    overlap: dict[str, int] = {}
    for comp in components:
        for c in comp["causes"]:
            badge = cause_of(c["class"])
            if badge:
                overlap[badge] = overlap.get(badge, 0) + int(c["overlap_px"])
    causes = sorted(overlap, key=lambda k: -overlap[k])
    if not causes and components:
        causes = ["장애물"]  # 길을 막은 덩어리는 있지만 모델 팀 클래스에 없는 물체 (쓰레기봉투·볼라드 등)
    return causes


def load() -> dict | None:
    """모델 팀 설정·가중치를 한 번 올린다. OBSTACLE_YOLO_ROOT가 비어 있으면 None (원인 배지 없이 분석)"""
    root = get_settings().obstacle_yolo_root
    if root is None or str(root) in ("", "."):
        return None
    with _lock:
        if "model" in _state:
            return _state
        root = Path(root).resolve()
        ac = _service_module(root)
        try:
            cfg = ac.load_config()  # 가중치가 없으면 SystemExit를 낸다 (명령행 도구용)
        except SystemExit as e:
            raise ModelMissing(str(e)) from None
        weights = cfg["model"]["path"]
        expected = str(cfg["model"].get("sha256") or "").lower()
        actual = ac.sha256_file(weights)
        if expected and actual != expected:
            raise ModelMissing(
                f"장애물 가중치 SHA-256이 config.yaml과 다릅니다: {weights} ({actual[:12]}… ≠ {expected[:12]}…)"
            )
        from ultralytics import YOLO

        cls_cfg = cfg["classes"]
        _state.update(
            module=ac,
            cfg=cfg,
            cls_cfg=cls_cfg,
            model=YOLO(weights),
            device=resolve_device(get_settings().device),
            min_conf=min(c["conf"] for c in cls_cfg.values()),
            version=str(cfg["model"]["version"]),
            sha256=actual,
        )
        return _state


def attribute(image_path: Path, blocker_path: Path | None, *, authoritative: bool) -> CauseResult | None:
    """
    image_path: 원본 사진 (attach_cause.detect가 EXIF 보정 후 Phase 6과 같은 분석 크기로 박스를 바꾼다)
    blocker_path: Phase 6 선택 surface의 blocker 마스크. None이면 Phase 6이 surface를 정하지 못한 것
    """
    st = load()
    if st is None:
        return None
    ac = st["module"]
    if blocker_path is None:
        return CauseResult([], "no_selected_surface", version=st["version"], policy=ac.POLICY)
    with _lock:
        blocker = ac.read_mask(str(blocker_path))
        dets, size, bgr = ac.detect(
            st["model"], str(image_path), st["cls_cfg"], st["cfg"]["model"]["imgsz"], st["device"], st["min_conf"]
        )
    if blocker.shape != (size[1], size[0]):
        raise ValueError(f"blocker 크기 {blocker.shape}와 분석 크기 {size[::-1]}가 다릅니다")
    status, comps, labels, used = ac.attribute(blocker, dets)
    overlay = ac.draw(bgr, size, labels, comps, dets, used)
    for c in comps:
        c.pop("_label", None)

    causes = badges(comps)
    types = sorted({c["type"] for comp in comps for c in comp["causes"]})
    return CauseResult(
        causes=causes,
        status=status,
        types=types,
        auto_routable=bool(types) and authoritative,
        components=comps,
        detections=dets,
        overlay=overlay,
        version=st["version"],
        policy=ac.POLICY,
    )

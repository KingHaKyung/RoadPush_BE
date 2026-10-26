"""
RoadPush_AI 통합 파이프라인(roadpush_ai.RoadPushAnalyzer) 어댑터.
사진 1장 → Surface(Mask2Former → Phase 6 보도/차도 선택 → 7-A 경계 → 7-B 연결 통로 폭)
        → 장애물 YOLO11s(같은 EXIF 보정 원본) → 최종 7-B 점유와 겹치는 탐지만 원인 (v5)
        → roadpush_analysis_response_v1 (AI docs/PRODUCTION_SYSTEM.md 1·6.1·12장)

판정 규칙·임계값·접점 규칙은 AI 저장소가 소유한다. 백엔드는 응답을 소비만 한다
(7-B → 판정: passability/phase7.py, 원인 → 배지: causes.py).

AI 공개 응답에는 mask 배열이 없다. overlay를 그리려고 Surface 분석기를 감싸
같은 실행의 in-memory 근거(SurfaceAnalysisBundle)를 함께 받는다 (다시 계산하지 않는다).

설치: Python 3.12, AI 저장소 requirements (docs/SETUP.md 8장). 모델은 AI 저장소 models/ (Git 제외)
  models/surface_passability/semantic/                       노면 semantic (Mask2Former Mapillary)
  models/surface_passability/yolo11n-seg.pt                  차량 instance (7-B 점유 보강, CPU)
  models/obstacle_detection/obstacle_yolo11s_full_os4.pt     장애물 원인 (configs/obstacle_detection.yaml)
"""

import hashlib
import json
import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..config import get_settings

RESPONSE_SCHEMA = "roadpush_analysis_response_v1"

_lock = threading.Lock()
_analyzer = None  # RoadPushAnalyzer. 워커에서 한 번만 만들고 모델을 재사용한다 (AI 문서 13장)
_capture = None  # 마지막 Surface 실행의 in-memory 근거
_ready = False


class ModelMissing(RuntimeError):
    """AI 모델 파일이 없음 (AI 저장소 models/, Git 제외)"""


@dataclass
class RoadPushRun:
    payload: dict[str, Any]  # roadpush_analysis_response_v1. 로컬 절대 경로는 저장소 기준 상대 경로로 바꾼 것
    image_bgr: np.ndarray  # 분석 크기 (EXIF 보정, 긴 변 700px) — overlay 좌표계
    labels: np.ndarray  # Mapillary semantic (분석 크기)
    structural: np.ndarray | None  # 7-A 최종 선택 surface structural (선택·경계 실패면 None)
    # 7-B 폭 계산에 실제 쓴 점유 = 장애물 접점 기준 (phase7b_occupancy_overlap_v2). 측정 못 하면 None
    occupancy: np.ndarray | None
    image_sha256: str
    idempotency_key: str


def resolve_device(device: str) -> str:
    """auto → GPU가 있으면 cuda, 없으면 cpu"""
    if device != "auto":
        return device
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def ai_root() -> Path:
    return get_settings().roadpush_ai_root.resolve()


def model_root() -> Path:
    s = get_settings()
    return (s.roadpush_model_root or ai_root() / "models").resolve()


def prepare_runtime() -> None:
    """AI 패키지를 처음 import하기 전에 코드 위치·장치를 정한다 (프로세스에서 한 번)"""
    global _ready
    if _ready:
        return
    s = get_settings()
    root = ai_root()
    # pip install -e 하지 않았으면 저장소의 src 두 곳을 쓴다 (roadpush_ai, surface_passability)
    for src in (root / "surface_passability" / "src", root / "src"):
        if src.is_dir() and str(src) not in sys.path:
            sys.path.insert(0, str(src))
    # Mask2Former 장치. AI 팀 결과 재현 기준은 cpu
    os.environ.setdefault("SURFACE_PASSABILITY_DEVICE", s.semantic_device)
    # 7-B 차량 instance YOLO11n-seg 장치. AI 기본값은 GPU("0")지만 production 검증은 CPU (AI 문서 13장)
    os.environ.setdefault("ROADPUSH_YOLO_DEVICE", "cpu")
    _ready = True


def model_files() -> dict[str, Path]:
    """check_model.py 점검용: AI 팀 모델 파일 위치"""
    m = model_root()
    return {
        "semantic": m / "surface_passability" / "semantic",
        "vehicle_instances": m / "surface_passability" / "yolo11n-seg.pt",
        # 승인된 7-B 점유 근거 (사진·mask hash가 모두 맞을 때만 AI가 복원). 없으면 AI가 쓰지 않고 진행
        "occupancy_evidence": m / "surface_passability" / "occupancy_evidence_v2" / "manifest.json",
        "obstacle": m / "obstacle_detection" / "obstacle_yolo11s_full_os4.pt",
    }


class _CapturingSurface:
    """SurfacePassabilityAnalyzer를 감싸 같은 실행의 in-memory 근거(mask)를 남긴다"""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.last = None

    def analyze_photo_with_evidence(self, request: Any) -> Any:
        self.last = None
        self.last = self.inner.analyze_photo_with_evidence(request)
        return self.last


def get_analyzer() -> Any:
    """RoadPushAnalyzer를 한 번 만든다. 모델 파일이 없으면 ModelMissing (재시도해도 해결되지 않음)"""
    global _analyzer, _capture
    if _analyzer is not None:
        return _analyzer
    prepare_runtime()
    missing = [f"{k}: {p}" for k, p in model_files().items() if not p.exists()]
    if missing:
        raise ModelMissing("AI 모델 파일이 없습니다 (AI 저장소 models/, Git 제외) — " + " · ".join(missing))

    from roadpush_ai import RoadPushAnalyzer
    from roadpush_ai.obstacle import ObstacleDetector, ObstacleSettings
    from surface_passability import SurfacePassabilityAnalyzer
    from surface_passability.config.settings import RuntimeSettings

    s = get_settings()
    root, models = ai_root(), model_root()
    cache = (s.storage_dir / "ai" / "semantic_cache").resolve()
    settings = RuntimeSettings(
        runtime_root=root,
        data_root=root / "data",
        model_root=models / "surface_passability",
        # semantic 캐시: 사진 hash·모델 identity가 key라 재분석 때 추론을 다시 하지 않는다
        cache_root=cache,
        artifact_root=cache.parent / "artifacts",  # production 경로는 artifact를 쓰지 않는다
        report_root=cache.parent / "reports",
    )
    # requested_device=None: semantic은 SURFACE_PASSABILITY_DEVICE, 차량 YOLO는 ROADPUSH_YOLO_DEVICE를 따른다
    _capture = _CapturingSurface(SurfacePassabilityAnalyzer(settings=settings))
    obstacle = ObstacleDetector(
        settings=ObstacleSettings.load(runtime_root=root, model_root=models),
        requested_device=resolve_device(s.device),
    )
    _analyzer = RoadPushAnalyzer(surface_analyzer=_capture, obstacle_detector=obstacle)
    return _analyzer


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _relative(value: Any, base: Path) -> Any:
    """
    DB(JSON)에 넣을 수 있게 정리한다: numpy 값 → 파이썬 값, 로컬 절대 경로 → 저장소 기준 상대 경로
    (외부 응답에 로컬 절대경로를 내지 않는다: AI 문서 12장)
    """
    if isinstance(value, dict):
        return {str(k): _relative(v, base) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_relative(v, base) for v in value]
    if isinstance(value, np.ndarray):
        return _relative(value.tolist(), base)
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        value = str(value)
    if isinstance(value, str) and len(value) < 1024:
        try:
            p = Path(value)
            if p.is_absolute():
                return p.resolve().relative_to(base).as_posix()
        except (ValueError, OSError):
            return Path(value).name if Path(value).is_absolute() else value
    return value


def idempotency_key(payload: dict[str, Any]) -> str:
    """
    AI 문서 12장 멱등 key: 사진 hash, 입력 프로필, 실측 입력, semantic 모델 identity,
    7-A 정책, 7-B schema, 점유 근거 버전 (+ 원인: 장애물 모델·접점 정책)
    """
    surface = payload["surface"]
    record = surface["passability"]
    model = surface["semantic"]["model"]
    obstacle = (payload.get("diagnostics") or {}).get("obstacle_model") or {}
    key = {
        "image_content_sha256": surface["image"]["content_sha256"],
        "input_profile": surface["surface_selection"].get("input_profile"),
        "surface_width_m": record.get("surface_width_m"),
        "agent_width_m": record.get("agent_width_m"),
        "safety_factor": record.get("safety_factor"),
        "semantic_model": [model.get("profile_id"), model.get("model_tree_sha256")],
        "phase7a_policy_id": surface["boundary"]["policy_id"],
        "phase7b_schema_version": record.get("schema_version"),
        "occupancy_evidence_version": ((record.get("diagnostics") or {}).get("occupancy") or {}).get("evidence_source"),
        "obstacle_model_sha256": obstacle.get("sha256"),
        "attribution_policy_id": payload["surface_obstacles"].get("policy_id"),
    }
    canonical = json.dumps(key, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def run_photo(
    image_path: Path,
    *,
    sample_id: str,
    storage_root: Path,
    input_profile: str = "image_upload",
    surface_width_m: float | None = None,
    agent_width_m: float | None = None,
    safety_factor: float | None = None,
) -> RoadPushRun:
    with _lock:  # 모델·AI 내부 상태는 한 번에 한 사진만 (AI 계약: 동시성 1)
        analyzer = get_analyzer()
        from surface_passability import AnalysisRequest

        image_path = Path(image_path).resolve()
        image_sha = _sha256(image_path)
        request = AnalysisRequest(
            request_id=sample_id,
            sample_id=sample_id,
            image_uri=image_path,
            image_content_sha256=image_sha,
            input_profile=input_profile,
            surface_width_m=surface_width_m,
            agent_width_m=agent_width_m,
            safety_factor=safety_factor,
        )
        response = analyzer.analyze_photo(request)
        bundle = _capture.last
        if response.status != "complete":
            # 장애물 모델 실행·접점 판정 실패. 원인 없이 결과를 내지 않고 실행 오류로 재시도한다
            err = response.surface_obstacles.error or {}
            raise RuntimeError(f"장애물 원인 단계 실패 ({response.surface_obstacles.status}): {err.get('message')}")
        payload = response.to_dict()
        # strict JSON: NaN·Infinity는 계약 위반 (AI 문서 12장) → 여기서 ValueError
        json.dumps(payload, allow_nan=False)
        return _to_run(payload, bundle, Path(storage_root).resolve(), image_sha)


def _to_run(payload: dict[str, Any], bundle: Any, storage_root: Path, image_sha: str) -> RoadPushRun:
    if payload.get("schema_version") != RESPONSE_SCHEMA:
        raise ValueError(f"알 수 없는 AI 응답 버전: {payload.get('schema_version')}")
    surface = bundle.response
    evidence = bundle.evidence  # v5: 최종 7-A structural · 7-B occupancy만 공개 (Phase 6 후보 mask는 없음)
    occupancy = evidence.phase7b_occupancy_mask
    structural = evidence.phase7a_structural_mask
    return RoadPushRun(
        payload=_relative(payload, storage_root),
        image_bgr=cv2.cvtColor(np.ascontiguousarray(evidence.prepared_image.rgb), cv2.COLOR_RGB2BGR),
        labels=np.asarray(surface.semantic.class_map),
        structural=None if structural is None else np.asarray(structural, dtype=bool),
        occupancy=None if occupancy is None else np.asarray(occupancy, dtype=bool),
        image_sha256=image_sha,
        idempotency_key=idempotency_key(payload),
    )

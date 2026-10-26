"""
RoadPush_AI 팀 파이프라인(roadpush_ai 패키지) 어댑터.
사진 1장 → Phase 6 → Phase 7-A → Phase 7-B 결과(phase7_b_result_v2).

AI 저장소의 배치 실행기(phase6/runner.py, phase7b/batch_runner.py)는 고정된 160장 결과 파일을 읽으므로,
새 사진은 같은 단계 함수를 이 순서로 직접 잇는다 (AI docs/PHASE7B_PRODUCTION_LOGIC.md 1장
"새 사진 경로는 현재 메모리의 Phase 6/7-A 결과를 다음 단계로 넘긴다").
  Phase 6   phase6/runner.py main()의 사진 1장 부분과 같다 (review 그림 없이 마스크만 저장)
  Phase 7-A phase7b/handoff.py Phase7AAdapter.reconstruct()와 같은 정책 순서
            V6 경계 → S1-A 연장 → V4c(+Banner 가림) 검증 → V4 probe 등급.
            저장된 결정과의 hash 대조만 빠진다 (새 사진에는 저장된 결정이 없다)
  Phase 7-B compose_phase7_b_result(). 점유 = Phase 6 blocker + 같은 실행의 instance ownership

새 사진의 V6 장애물 실루엣은 _obstacle_silhouette에서 만든다 (아래 설명)

설치: Python 3.12, AI 저장소 requirements (docs/SETUP.md 8장). 모델은 AI 저장소 models/ 폴더 (Git 제외)
  models/mask2former_swin_large_mapillary/   노면 semantic (Mapillary Vistas)
  models/yolo11n-seg.pt                      차량 instance (Phase 6 조건부)
"""

import hashlib
import json
import os
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..config import get_settings

SCHEMA = "phase7_b_result_v2"
SEMANTIC_MODEL = "mask2former_mapillary_swin_l"  # roadpush_ai Runner 키
SEMANTIC_ASSET = "mask2former_swin_large_mapillary"
PHASE6_POLICY = "surface_generalization_phase6_production_selector_v1"
# Phase 6 선택 surface별 마스크 파일 (phase7a/reference_catalog.py _branch_paths와 같다)
BRANCH_FILES = {
    "sidewalk": ("07_proposed_pruned_boundary.png", "08_applied_blocker.png"),
    "road": ("08_structural_road.png", "09_road_blocker.png"),
}
# S1-A 생성 정책 (phase7b/handoff.py와 같은 값)
S1A_KWARGS = {
    "stop_before_side_frame_contact": True,
    "minimum_resolvable_width_px": 12,
    "minimum_width_uncertainty_multiplier": 2.0,
}

_lock = threading.Lock()
_models: dict = {}  # roadpush_ai model_holder (Mask2Former, YOLO11n-seg). 워커에서 한 번만 올린다
_ready = False


class ModelMissing(RuntimeError):
    """AI 모델 파일이 없음 (AI 저장소 models/·weights/, Git 제외)"""


@dataclass
class Phase7Run:
    record: dict[str, Any]  # phase7_b_result_v2. 로컬 절대 경로는 저장소 기준 상대 경로로 바꾼 것
    image_bgr: np.ndarray  # 분석 크기 (EXIF 보정, 긴 변 700px) — overlay·원인 판정 좌표계
    labels: np.ndarray  # Mapillary semantic (분석 크기)
    selection: dict[str, Any]  # Phase 6 surface 선택
    v6: dict[str, Any]  # V6 처리 결과 요약 (disposition·subclassification)
    phase7a: dict[str, Any]  # S1 결정 요약
    structural: np.ndarray | None  # 7-A effective structural (측정 허용일 때만)
    occupancy: np.ndarray | None  # 7-B 측정 점유
    phase6_structural: np.ndarray  # Phase 6 선택 surface (선택 못 하면 빈 마스크) — 판단 유보 때 overlay용
    phase6_blocker: np.ndarray
    blocker_path: Path | None  # Phase 6 선택 surface blocker 파일 (원인 판정 입력)
    image_sha256: str
    idempotency_key: str
    versions: dict[str, str] = field(default_factory=dict)
    semantic_inference_run: bool = False


def resolve_device(device: str) -> str:
    """auto → GPU가 있으면 cuda:0, 없으면 cpu"""
    if device != "auto":
        return device
    try:
        import torch

        return "cuda:0" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def prepare_runtime() -> None:
    """roadpush_ai를 처음 import하기 전에 위치·장치를 정한다 (프로세스에서 한 번)"""
    global _ready
    if _ready:
        return
    s = get_settings()
    root = s.roadpush_ai_root.resolve()
    # roadpush_ai.config.settings가 모델·캐시 위치를 이 값으로 정한다 (import 시점에 한 번 읽음)
    os.environ.setdefault("ROADPUSH_AI_ROOT", str(root))
    # Phase 6 YOLO11n-seg 장치. 기본값이 GPU("0")라 AI 팀 기준(cpu, occupancy/blockers.py)을
    # import 순서와 무관하게 정해 둔다
    os.environ.setdefault("ROADPUSH_YOLO_DEVICE", "cpu")
    src = root / "src"
    if (src / "roadpush_ai").is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))
    # roadpush_ai.occupancy.blockers는 import할 때 CUDA_VISIBLE_DEVICES=""로 GPU를 숨긴다 (AI 팀 CPU 기준).
    # 장애물 원인 모델이나 Mask2Former를 GPU로 쓰려면 그 전에 CUDA를 먼저 초기화해 둔다
    if s.semantic_device.startswith("cuda") or resolve_device(s.device).startswith("cuda"):
        import torch

        if torch.cuda.is_available():
            torch.cuda.init()
    _ready = True


def _ai_root() -> Path:
    from roadpush_ai.config.settings import get_settings as ai_settings

    return ai_settings().project_root


def _load_models() -> None:
    if "mask2former_cpu" in _models:
        return
    model_dir = _ai_root() / "models" / SEMANTIC_ASSET
    if not model_dir.is_dir():
        raise ModelMissing(f"Mask2Former 모델 폴더가 없습니다: {model_dir} (AI 저장소 models/, Git 제외)")
    from roadpush_ai.inference.semantic_model import Runner

    # load_or_infer_inputs는 이 키가 있으면 그 runner를 쓴다. 없으면 CPU runner를 만들며 GPU를 숨기므로 미리 넣어 둔다
    _models["mask2former_cpu"] = Runner(SEMANTIC_MODEL, get_settings().semantic_device)


def model_files() -> dict[str, Path]:
    """check_model.py 점검용: AI 팀 모델 파일 위치"""
    prepare_runtime()
    root = _ai_root()
    return {"semantic": root / "models" / SEMANTIC_ASSET, "vehicle_instances": root / "models" / "yolo11n-seg.pt"}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_mask(path: Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    value = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE) if data.size else None
    if value is None:
        raise FileNotFoundError(path)
    return value > 0


def _obstacle_silhouette(labels: np.ndarray) -> np.ndarray:
    """
    V6 경계 접촉 판정용 장애물 실루엣. 고정 160장에서는 과거 실행기가 만든 obstacle_silhouette.png를 읽는데
    그 실행기는 production 패키지에 없다. retry_v4.py에 기록된 출처
    ("cached_semantic_map_occupancy_and_hazard_classes", vegetation 제외)대로 Mapillary OCCUPANCY + HAZARD로 만든다.
    """
    from roadpush_ai.legacy_policy.base_surface import HAZARD, OCCUPANCY

    return np.isin(labels, (*OCCUPANCY, *HAZARD))


def _relative(value: Any, base: Path) -> Any:
    """
    DB(JSON)에 넣을 수 있게 정리한다: numpy 값 → 파이썬 값, 로컬 절대 경로 → 저장소 기준 상대 경로
    (외부 응답에 로컬 경로를 내지 않는다: AI 계약 5장)
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


def idempotency_key(
    image_sha256: str, input_profile: str, widths: dict, occupancy_source: str, phase7a_policy: str
) -> str:
    """AI 계약 5장 멱등 key: 같은 사진·입력·정책이면 같은 결과"""
    payload = {
        "image_content_sha256": image_sha256,
        "input_profile": input_profile,
        **widths,
        "semantic_model_version": f"{SEMANTIC_ASSET}@{get_settings().semantic_device}",
        "phase6_policy_version": PHASE6_POLICY,
        "phase7a_policy_id": phase7a_policy,
        "phase7b_schema_version": SCHEMA,
        "occupancy_evidence_version": occupancy_source,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def run_photo(
    image_path: Path,
    work_dir: Path,
    *,
    sample_id: str,
    cache_dir: Path,
    storage_root: Path,
    input_profile: str = "image_upload",
    surface_width_m: float | None = None,
    agent_width_m: float | None = None,
    safety_factor: float | None = None,
) -> Phase7Run:
    """
    work_dir: 이번 실행의 새 빈 폴더 (AI 계약: 기존 output은 덮어쓰지 않는다 → 재시도마다 새 폴더)
    cache_dir: Mapillary semantic 캐시 (사진 hash·장치별. 재분석 때 추론을 다시 하지 않는다)
    """
    with _lock:  # 모델·roadpush_ai 내부 상태는 한 번에 한 사진만
        prepare_runtime()
        _load_models()
        return _run(
            Path(image_path),
            Path(work_dir),
            sample_id=sample_id,
            cache_dir=Path(cache_dir),
            storage_root=Path(storage_root).resolve(),
            input_profile=input_profile,
            widths={"surface_width_m": surface_width_m, "agent_width_m": agent_width_m, "safety_factor": safety_factor},
        )


def _run(
    image_path: Path,
    work_dir: Path,
    *,
    sample_id: str,
    cache_dir: Path,
    storage_root: Path,
    input_profile: str,
    widths: dict,
) -> Phase7Run:
    from roadpush_ai.inference.semantic_inputs import load_or_infer_inputs
    from roadpush_ai.occupancy.obstacle_geometry import extract_obstacle_geometry, prepare_obstacle_geometry
    from roadpush_ai.phase6.selector import SurfaceSelectorPolicy, select_surface_for_input_profile
    from roadpush_ai.road.enhanced_context import ENHANCED_ROAD_POLICY_ID, build_enhanced_road_context
    from roadpush_ai.road.pipeline import process_road_from_prepared
    from roadpush_ai.surface.context import build_surface_pipeline_context
    from roadpush_ai.surface.sidewalk_pipeline import process_sidewalk_from_prepared

    image_sha = _sha256(image_path)
    work_dir.mkdir(parents=True, exist_ok=False)
    cache_dir.mkdir(parents=True, exist_ok=True)
    device_tag = get_settings().semantic_device.replace(":", "")
    item = {
        "sample_id": sample_id,
        "relative_path": str(image_path.resolve()),  # roadpush_ai ROOT / 절대 경로 = 절대 경로
        "class_map_path": str((cache_dir / f"{SEMANTIC_ASSET}-{device_tag}-{image_sha}.png").resolve()),
    }

    # ── Phase 6: Sidewalk·Road 후처리 + 대표 surface 선택 (phase6/runner.py main과 같은 순서)
    inputs = load_or_infer_inputs(item, _models)
    sidewalk_ctx = build_surface_pipeline_context(inputs.image, inputs.labels)
    enhanced = build_enhanced_road_context(inputs.labels, sidewalk_ctx.surface)
    road = enhanced.road
    geometry = extract_obstacle_geometry(inputs.image, inputs.labels)
    geometry = prepare_obstacle_geometry(geometry, [sidewalk_ctx.surface, road.surface], _models)
    phase6_dir = work_dir / "phase6"
    phase6_dir.mkdir()
    runtime: dict[str, dict] = {"sidewalk": {}, "road": {}}
    process_sidewalk_from_prepared(
        item,
        1,
        phase6_dir / "sidewalk",
        inputs,
        sidewalk_ctx,
        geometry,
        render_review=False,
        runtime_capture=runtime["sidewalk"],
    )
    process_road_from_prepared(
        item,
        1,
        phase6_dir / "road",
        sidewalk_ctx,
        road,
        geometry,
        render_review=False,
        runtime_capture=runtime["road"],
    )
    selection = select_surface_for_input_profile(
        input_profile,
        sidewalk_ctx.surface,
        road,
        runtime["sidewalk"]["structural_mask"],
        runtime["road"]["structural_mask"],
        inputs.labels,
        SurfaceSelectorPolicy(),
    ).to_dict()

    m = measure(
        sample_id,
        selection,
        inputs.labels,
        phase6_dir,
        {k: v.get("passability_instances") for k, v in runtime.items()},
        widths,
        image_sha=image_sha,
    )
    image_bgr = cv2.cvtColor(np.ascontiguousarray(inputs.image), cv2.COLOR_RGB2BGR)
    return Phase7Run(
        record=_relative(m.record, storage_root),
        image_bgr=image_bgr,
        labels=inputs.labels,
        selection=_relative(
            {k: selection.get(k) for k in ("input_profile", "state", "selected_surface", "fallback", "reasons")},
            storage_root,
        ),
        v6=m.v6,
        phase7a=_relative(m.phase7a, storage_root),
        structural=m.structural,
        occupancy=m.occupancy,
        phase6_structural=m.phase6_structural,
        phase6_blocker=m.phase6_blocker,
        blocker_path=m.blocker_path,
        image_sha256=image_sha,
        idempotency_key=idempotency_key(image_sha, input_profile, widths, m.occupancy_source, m.phase7a["policy_id"]),
        versions={
            "semantic": f"{SEMANTIC_ASSET}@{get_settings().semantic_device}",
            "phase6": f"{PHASE6_POLICY}+{ENHANCED_ROAD_POLICY_ID}",
            "phase7a": m.phase7a["policy_id"],
            "phase7b": SCHEMA,
        },
        semantic_inference_run=bool(inputs.inference_run),
    )


@dataclass
class Measurement:
    record: dict[str, Any]  # phase7_b_result_v2 원본 (로컬 경로 포함)
    v6: dict[str, Any]
    phase7a: dict[str, Any]
    structural: np.ndarray | None
    occupancy: np.ndarray | None
    occupancy_source: str
    phase6_structural: np.ndarray
    phase6_blocker: np.ndarray
    blocker_path: Path | None


def measure(
    sample_id: str,
    selection: dict,
    labels: np.ndarray,
    phase6_dir: Path,
    instances: dict[str, list | None],
    widths: dict,
    *,
    image_sha: str = "",
) -> Measurement:
    """
    Phase 6 결과(선택·저장된 마스크·instance ownership) → Phase 7-A → Phase 7-B.
    모델을 쓰지 않으므로 합성 마스크로도 시험할 수 있다 (tests/test_roadpush_ai.py)
    """
    from roadpush_ai.phase7a.extension_s1 import (
        S1AGenerationPolicy,
        evaluate_s1_targeted_v4c,
        freeze_v6_observation,
        generate_s1a_proposal,
        grade_s1_v4_probe_result,
        mask_sha256,
    )
    from roadpush_ai.phase7a.reference_reconstruction import _disposition
    from roadpush_ai.phase7a.retry_v6 import POLICY_ID as V6_POLICY_ID
    from roadpush_ai.phase7a.retry_v6 import fit_trapezoid_boundary_retry_v6
    from roadpush_ai.phase7a.validation_policy import BANNER_CLASS_ID, V4B_OCCLUDER_CLASS_IDS, _v6_subclassification
    from roadpush_ai.phase7b.handoff import Phase7BHandoff, _row_ids, _typed_null_result, route_phase7a_status
    from roadpush_ai.phase7b.occupancy_builder import build_measurement_occupancy
    from roadpush_ai.phase7b.pipeline import compose_phase7_b_result

    selected = selection.get("selected_surface")
    shape = labels.shape[:2]

    # ── Phase 7-A: V6 → S1-A → V4c+Banner → V4 probe 등급 (handoff.Phase7AAdapter.reconstruct와 같은 순서)
    blocker_path: Path | None = None
    structural_path: Path | None = None
    if selected is None:
        v6, _ = fit_trapezoid_boundary_retry_v6(None, selection["state"], None, None)
        structural = np.zeros(shape, dtype=bool)
        blocker = np.zeros(shape, dtype=bool)
        occluder = np.zeros(shape, dtype=bool)
    else:
        s_name, b_name = BRANCH_FILES[selected]
        structural_path, blocker_path = phase6_dir / selected / s_name, phase6_dir / selected / b_name
        structural, blocker = _read_mask(structural_path), _read_mask(blocker_path)
        v6, _ = fit_trapezoid_boundary_retry_v6(
            selected,
            selection["state"],
            structural,
            blocker,
            semantic_labels=labels,
            obstacle_silhouette_mask=_obstacle_silhouette(labels),
            raw_structural_mask=None,  # 진단 전용 (retry_v2 _raw_pruned)
        )
        occluder = np.isin(labels, (*V4B_OCCLUDER_CLASS_IDS, BANNER_CLASS_ID))
    v6["final_disposition"] = _disposition(v6)
    subclass, subclass_evidence = _v6_subclassification(
        v6["final_disposition"], v6, {"selector_reasons": selection.get("reasons", [])}
    )
    package = freeze_v6_observation(sample_id, v6, shape)
    diagnostic, generation_masks = generate_s1a_proposal(
        package, structural, blocker, S1AGenerationPolicy(**S1A_KWARGS)
    )
    decision, selected_masks = evaluate_s1_targeted_v4c(package, diagnostic, generation_masks, occluder)
    decision["policy_id"] = f"{decision['policy_id']}_banner32"
    decision["banner_occluder_enabled"] = True
    decision["occluder_class_ids"] = [*V4B_OCCLUDER_CLASS_IDS, BANNER_CLASS_ID]
    decision, selected_masks = grade_s1_v4_probe_result(
        package,
        decision,
        selected_masks,
        generation_masks,
        top_bottom_width_ratio=v6.get("envelope_fit", {}).get("top_bottom_width_ratio"),
    )

    status = str(decision["s1_status"])
    route = route_phase7a_status(status)
    effective = np.asarray(selected_masks["effective_selected"], dtype=bool)
    observed = effective & np.asarray(generation_masks["observed_seed"], dtype=bool)
    generated = effective & np.asarray(
        selected_masks.get("validated_extension", generation_masks["extension_only"]), dtype=bool
    )
    effective_rows = _row_ids(effective)
    null_result = (
        None
        if route.calculation_allowed
        else _typed_null_result(
            {"sample_id": sample_id, "s1_decision": decision}, route, selected_surface=package.selected_surface
        )
    )
    handoff = Phase7BHandoff(
        sample_id=sample_id,
        selected_surface=package.selected_surface,
        phase7a_policy_id=str(decision["policy_id"]),
        phase7a_status=status,
        measurement_scope=route.measurement_scope,
        measurement_authority=route.measurement_authority,
        result_authority=route.result_authority,
        human_review_required=route.human_review_required,
        authorized_row_range=(
            (effective_rows[0], effective_rows[-1]) if route.calculation_allowed and effective_rows else None
        ),
        observed_rows=_row_ids(observed),
        generated_rows=_row_ids(generated),
        structural_mask_sha256=mask_sha256(effective),
        structural_area_px=int(effective.sum()),
        provenance={
            "source_image_sha256": image_sha,
            "phase7a_policy_id": decision["policy_id"],
            "phase7a_selected_model": decision.get("s1_selected_model"),
            "phase7a_failure_reasons": decision.get("s1_failure_reasons", []),
            "phase7a_quality_grading_reasons": decision.get("quality_grading_reasons", []),
            "phase7a_termination": {
                "far_upper": diagnostic.get("far_upper"),
                "near_lower": diagnostic.get("near_lower"),
                "v4c_truncation": decision.get("v4c_truncation"),
            },
            "v6_policy_id": V6_POLICY_ID,
            "v6_final_disposition": v6["final_disposition"],
            "v6_subclassification": subclass,
            # occupancy_builder가 이 경로의 blocker를 읽고 실행 전후 hash를 대조한다
            "phase6_blocker_path": str(blocker_path) if blocker_path else None,
            "phase6_structural_path": str(structural_path) if structural_path else None,
            "reconstruction": "runtime_phase6_to_v6_to_v4c_banner32_to_v4_probe_grading",
        },
        structural_mask=effective.copy() if route.calculation_allowed else None,
        typed_null_result=null_result,
    )

    # ── Phase 7-B: 측정 점유 → 연결 통로 → 3행 병목 → (선택) 실폭
    selected_instances = instances.get(selected or "") or None
    occupancy = build_measurement_occupancy(
        handoff,
        v6_final_disposition=v6["final_disposition"],
        v6_subclassification=subclass,
        evidence_index=None,  # 고정 160장 증거 파일은 새 사진에 쓰지 않는다
        passability_instances=selected_instances,
    )
    bundle = compose_phase7_b_result(handoff, occupancy, **widths)

    return Measurement(
        record=bundle.record,
        v6={"final_disposition": v6["final_disposition"], "subclassification": subclass, "evidence": subclass_evidence},
        phase7a={
            "status": status,
            "policy_id": str(decision["policy_id"]),
            "failure_reasons": decision.get("s1_failure_reasons", []),
            "quality_grading_reasons": decision.get("quality_grading_reasons", []),
        },
        structural=handoff.structural_mask,
        occupancy=occupancy.occupancy_mask,
        occupancy_source=occupancy.evidence_source,
        phase6_structural=structural,
        phase6_blocker=blocker,
        blocker_path=blocker_path,
    )

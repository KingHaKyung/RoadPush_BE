"""
AI 분석 모듈 진입점. 입력(사진·영상·설정) → 결과(판정·지표·근거 이미지)만 만든다.
DB·파일 저장·구간 연결은 백엔드 서비스(app/services)가 맡는다 (마스터 문서 3장).

ANALYSIS_ENGINE
  roadpush_ai  RoadPush_AI 통합 파이프라인 RoadPushAnalyzer (심사·서비스용, roadpush.py)
               사진 → Mask2Former semantic → Phase 6 보도/차도 선택 → Phase 7-A 경계 → Phase 7-B 연결 통로 폭
               → 장애물 YOLO11s 원인 (선택 surface 접점만) → 백엔드 판정 (passability/phase7.py)
  dummy        모델 없이 등록 → 분석 → 지도 → 검수 흐름 확인용 가짜 장면 + 백엔드 임시 규칙 (passability/rules.py)
"""

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from ..config import get_settings
from . import imageio
from .passability.overlay import (
    cause_overlay,
    image_quality,
    passage_overlay,
    phase7_passage_overlay,
    phase7_surface_overlay,
    surface_overlay,
)
from .passability.risk import risk_score
from .passability.rules import RULE_VERSION, PassabilityResult, Policy, analyze_passability
from .scene import causes_from, compose, fuse_frames
from .surface import load_dummy
from .types import SceneResult

NIGHT_BRIGHTNESS = 50
# 근거(evidence.selection)에 남길 Phase 6 선택 요약
SELECTION_KEYS = ("input_profile", "state", "selected_surface", "fallback", "reasons")


class InputError(Exception):
    """손상 파일·지원하지 않는 입력처럼 다시 시도해도 해결되지 않는 오류 (재시도하지 않음)"""


@dataclass
class EngineOutput:
    result: PassabilityResult
    risk: int | None
    risk_detail: dict
    causes: list[str]
    quality: dict
    model_version: str
    rule_version: str
    images: dict[str, np.ndarray] = field(default_factory=dict)  # class_map, surface_overlay, obstacle_overlay
    evidence: dict = field(default_factory=dict)  # 엔진별 근거 (roadpush_ai: 7-B 기록·원인·버전·멱등 key)
    frames: int = 1


def read_frames(path: str, media_type: str) -> list[np.ndarray]:
    s = get_settings()
    if media_type == "image":
        img = imageio.imread(path)
        if img is None:
            raise InputError("이미지를 읽을 수 없습니다 (손상 또는 지원하지 않는 형식)")
        return [img]
    cap = cv2.VideoCapture(imageio.video_path(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    step = max(1, int(round(fps * s.video_frame_interval_sec)))
    frames, i = [], 0
    while len(frames) < s.video_max_frames and cap.grab():
        if i % step == 0:
            ok, f = cap.retrieve()
            if ok:
                frames.append(f)
        i += 1
    cap.release()
    if not frames:
        raise InputError("영상에서 프레임을 읽지 못했습니다")
    return frames


def analyze(
    path: Path,
    media_type: str,
    *,
    work_dir: Path,
    storage_root: Path,
    sample_id: str,
    reference_width_m: float | None = None,
    input_profile: str = "image_upload",
    precomputed_mask: np.ndarray | None = None,
    policy: Policy | None = None,
) -> EngineOutput:
    """
    분석 1건. work_dir는 이번 실행 전용 폴더 (영상이면 분석할 가운데 프레임을 여기에 쓴다)
    precomputed_mask(통합 라벨 PNG)는 dummy 규칙으로만 분석한다 (모델 없이 규칙 확인용)
    """
    engine = get_settings().analysis_engine
    if engine == "dummy" or precomputed_mask is not None:
        return run(read_frames(str(path), media_type), reference_width_m, precomputed_mask, policy)
    if engine != "roadpush_ai":
        raise ValueError(f"알 수 없는 ANALYSIS_ENGINE: {engine}")
    return run_roadpush(
        Path(path),
        media_type,
        work_dir=Path(work_dir),
        storage_root=Path(storage_root),
        sample_id=sample_id,
        reference_width_m=reference_width_m,
        input_profile=input_profile,
        policy=policy,
    )


def run_roadpush(
    path: Path,
    media_type: str,
    *,
    work_dir: Path,
    storage_root: Path,
    sample_id: str,
    reference_width_m: float | None = None,
    input_profile: str = "image_upload",
    policy: Policy | None = None,
) -> EngineOutput:
    from . import causes as cause_model
    from . import roadpush
    from .passability import phase7

    s = get_settings()
    frames = read_frames(str(path), media_type)  # 읽을 수 없는 파일은 여기서 InputError
    photo = path
    if media_type != "image":
        # AI 파이프라인은 사진 1장 단위다. 영상은 가운데 프레임을 사진으로 분석한다
        work_dir.mkdir(parents=True, exist_ok=True)
        photo = work_dir / "frame.png"
        imageio.imwrite(photo, frames[len(frames) // 2])

    run7 = roadpush.run_photo(
        photo,
        sample_id=sample_id,
        storage_root=storage_root,
        input_profile=input_profile,
        # AI 요청 계약: 실폭은 양수만 (0·음수는 입력 오류). 없으면 상대폭만
        surface_width_m=reference_width_m if reference_width_m and reference_width_m > 0 else None,
        agent_width_m=s.agent_width_m,
        safety_factor=s.safety_factor,
    )
    payload = run7.payload
    surface = payload["surface"]
    record = surface["passability"]
    selection = {k: surface["surface_selection"].get(k) for k in SELECTION_KEYS}
    res = phase7.to_result(record, policy, selection)

    quality = image_quality(run7.image_bgr)
    if res.verdict == "undetermined" and quality["brightness"] < NIGHT_BRIGHTNESS:
        res.uncertainty_code = "night"
        res.reasons.insert(0, f"저조도 영상 (평균 밝기 {quality['brightness']})")
    score, risk_detail = risk_score(res)

    cause = cause_model.from_response(payload["surface_obstacles"])

    img = run7.image_bgr
    shape = img.shape[:2]
    images = {
        "class_map": run7.labels.astype(np.uint8),
        "surface_overlay": phase7_surface_overlay(img, _fit(run7.structural, shape), _fit(run7.occupancy, shape)),
    }
    if res.verdict != "undetermined":
        # 원인 덩어리(components)는 7-B 점유 기준이라 같은 mask 위에 그린다
        base = cause_overlay(img, _fit(run7.occupancy, shape), cause.components, cause.obstacles)
        images["obstacle_overlay"] = phase7_passage_overlay(base, record)

    diagnostics = payload.get("diagnostics") or {}
    obstacle_model = diagnostics.get("obstacle_model") or {}
    semantic = surface["semantic"]
    versions = {
        "response": payload["schema_version"],
        "semantic": f"{semantic['model'].get('profile_id')}@{semantic.get('requested_device')}",
        "phase7a": surface["boundary"]["policy_id"],
        "phase7b": record.get("schema_version"),
        "obstacle_cause": f"{obstacle_model.get('version')}/{cause.policy}",
    }
    evidence = {
        "engine": "roadpush_ai",
        # AI 원본 응답 response.to_dict() 전체 (surface·surface_obstacles·diagnostics). 아래 키는 자주 쓰는 부분
        "response": payload,
        "phase7b": record,
        "selection": selection,
        "versions": versions,
        "image_sha256": run7.image_sha256,
        "idempotency_key": run7.idempotency_key,
        "input_profile": input_profile,
        "semantic_inference_run": bool(semantic.get("inference_run")),
        "cause": {
            "status": cause.status,
            "types": cause.types,
            "auto_routable": cause.auto_routable,
            "components": cause.components,
            "obstacles": cause.obstacles,
            "policy": cause.policy,
        },
    }
    return EngineOutput(
        result=res,
        risk=score,
        risk_detail=risk_detail,
        causes=cause.causes,
        quality=quality,
        model_version=f"{versions['semantic']} + yolo11n-seg + {obstacle_model.get('version')}",
        rule_version=f"{record.get('schema_version')}/{phase7.POLICY_VERSION}",
        images=images,
        evidence=evidence,
        frames=len(frames),
    )


def _fit(mask: np.ndarray | None, shape: tuple[int, int]) -> np.ndarray | None:
    """overlay용 mask. 분석 크기와 다르면(선택 surface 없음 등) 그리지 않는다"""
    if mask is None or mask.shape != shape:
        return None
    return np.asarray(mask, dtype=bool)


# ── dummy 엔진 (모델 없이 흐름 확인) ──────────────────────────────────────────


def _scene(frame: np.ndarray, precomputed: np.ndarray | None) -> SceneResult:
    if precomputed is not None:
        cm = precomputed[..., 0] if precomputed.ndim == 3 else precomputed
        img = cv2.resize(frame, (cm.shape[1], cm.shape[0]))
        return SceneResult(cm.astype(np.uint8), None, img, [], {"surface": "precomputed"})
    surface = load_dummy()
    sr = surface.predict(frame)
    return compose(sr, list(sr.instances), {"surface": surface.version})


def run(
    frames: list[np.ndarray],
    reference_width_m: float | None = None,
    precomputed_mask: np.ndarray | None = None,
    policy: Policy | None = None,
) -> EngineOutput:
    policy = policy or Policy()
    scene = fuse_frames([_scene(f, precomputed_mask) for f in frames])
    res = analyze_passability(scene.class_map, policy, scene.confidence, reference_width_m=reference_width_m)

    quality = image_quality(frames[len(frames) // 2])
    if res.verdict == "undetermined" and quality["brightness"] < NIGHT_BRIGHTNESS:
        res.uncertainty_code = "night"
        res.reasons.insert(0, f"저조도 영상 (평균 밝기 {quality['brightness']})")
    score, risk_detail = risk_score(res)

    img = scene.image if scene.image is not None else cv2.resize(frames[0], scene.class_map.shape[::-1])
    images = {"class_map": scene.class_map, "surface_overlay": surface_overlay(img, scene.class_map)}
    if res.verdict != "undetermined":
        images["obstacle_overlay"] = passage_overlay(img, res)
    model_version = " + ".join(f"{k}={v}" for k, v in scene.versions.items())
    return EngineOutput(
        result=res,
        risk=score,
        risk_detail=risk_detail,
        causes=causes_from(scene, res.occupied_mask) if scene.instances else _causes_without_instances(res),
        quality=quality,
        model_version=model_version,
        rule_version=f"{RULE_VERSION}/{policy.version}",
        images=images,
        evidence={"engine": "dummy"},
        frames=len(frames),
    )


def _causes_without_instances(res: PassabilityResult) -> list[str]:
    """instance 정보가 없으면(precomputed) 종류는 모르고 '장애물'로만 표시"""
    return ["장애물"] if res.occupied_mask is not None and res.occupied_mask.any() else []

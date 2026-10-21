"""
AI 분석 모듈 진입점. 입력(프레임·설정) → 결과(판정·지표·근거 이미지)만 만든다.
DB·파일 저장·구간 연결은 백엔드 서비스(app/services)가 맡는다 (마스터 문서 3장).

  프레임 → 노면 semantic + 장애물 instance → 장면 합성 → (영상) 프레임 융합
        → 보행공간 W 추정 · 점유 · 남은 개별 통로 · 연속성 → 통과 상태 · 유보 · 상대 위험도 · 근거
"""

from dataclasses import dataclass, field

import cv2
import numpy as np

from ..config import get_settings
from .obstacles import load_obstacles
from .passability.overlay import image_quality, passage_overlay, surface_overlay
from .passability.risk import risk_score
from .passability.rules import RULE_VERSION, PassabilityResult, Policy, analyze_passability
from .scene import causes_from, compose, fuse_frames
from .surface import load_surface
from .types import SceneResult, SurfaceResult

NIGHT_BRIGHTNESS = 50


class InputError(Exception):
    """손상 파일·지원하지 않는 입력처럼 다시 시도해도 해결되지 않는 오류 (재시도하지 않음)"""


@dataclass
class EngineOutput:
    result: PassabilityResult
    risk: int | None
    risk_detail: dict
    causes: list[str]
    scene: SceneResult
    quality: dict
    model_version: str
    rule_version: str
    images: dict[str, np.ndarray] = field(default_factory=dict)  # class_map, surface_overlay, obstacle_overlay


def read_frames(path: str, media_type: str) -> list[np.ndarray]:
    s = get_settings()
    if media_type == "image":
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is None:
            raise InputError("이미지를 읽을 수 없습니다 (손상 또는 지원하지 않는 형식)")
        return [img]
    cap = cv2.VideoCapture(path)
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


def _scene(frame: np.ndarray, precomputed: np.ndarray | None) -> SceneResult:
    s = get_settings()
    if precomputed is not None:
        cm = precomputed[..., 0] if precomputed.ndim == 3 else precomputed
        img = cv2.resize(frame, (cm.shape[1], cm.shape[0]))
        return SceneResult(cm.astype(np.uint8), None, img, [], {"surface": "precomputed"})
    surface = load_surface(s.surface_model, s.surface_weights, s.device, s.infer_short_side)
    sr: SurfaceResult = surface.predict(frame)
    versions = {"surface": surface.version}
    obstacles = load_obstacles(s.obstacle_model, tuple(s.obstacle_weights), s.device)
    insts = []
    if obstacles is not None:
        img = sr.image if sr.image is not None else frame
        insts = obstacles.predict(img)
        versions["obstacles"] = obstacles.version
    return compose(sr, insts, versions)


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
        causes=causes_from(scene, res.occupied_mask) if scene.instances else _causes_without_instances(res, scene),
        scene=scene,
        quality=quality,
        model_version=model_version,
        rule_version=f"{RULE_VERSION}/{policy.version}",
        images=images,
    )


def _causes_without_instances(res: PassabilityResult, scene: SceneResult) -> list[str]:
    """instance 정보가 없으면(precomputed·dummy) 종류는 모르고 '장애물'로만 표시"""
    return ["장애물"] if res.occupied_mask is not None and res.occupied_mask.any() else []

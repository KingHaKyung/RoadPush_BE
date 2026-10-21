"""노면 결과와 장애물 instance를 한 장면으로 합치고, 영상이면 여러 프레임을 융합한다."""

import cv2
import numpy as np

from .labels import CAUSE_OF, PERSON_NAMES, L
from .types import Instance, SceneResult, SurfaceResult


def compose(surface: SurfaceResult, instances: list[Instance], versions: dict[str, str]) -> SceneResult:
    """
    장애물 실루엣 전체를 OBSTACLE로 덮는다 (마스터 문서 9장: 문·창문·지붕 포함 전체 실루엣이 초기 점유 후보).
    차도 위 물체도 일단 덮지만, 보도 위 점유인지는 규칙이 경계(연석·차도 너머)로 따로 판단하고 애매하면 유보한다.
    """
    cm = surface.class_map.copy()
    for inst in instances:
        if inst.name in PERSON_NAMES:
            cm[inst.mask & (cm != L.OBSTACLE)] = L.TRANSIENT
    for inst in instances:
        if inst.name in CAUSE_OF:
            cm[inst.mask] = L.OBSTACLE
    return SceneResult(cm, surface.confidence, surface.image, instances, versions)


def causes_from(scene: SceneResult, occupied: np.ndarray | None, min_share: float = 0.1) -> list[str]:
    """보도 위 점유 픽셀에 겹친 장애물 → 원인 배지 (점유 면적의 min_share 이상만)"""
    if occupied is None or not occupied.any():
        return []
    total = occupied.sum()
    share: dict[str, float] = {}
    for inst in scene.instances:
        cause = CAUSE_OF.get(inst.name)
        if cause:
            share[cause] = share.get(cause, 0) + (inst.mask & occupied).sum() / total
    return [k for k, v in sorted(share.items(), key=lambda kv: -kv[1]) if v >= min_share]


def fuse_frames(scenes: list[SceneResult], persist: float = 0.6) -> SceneResult:
    """
    영상 프레임 여러 장을 한 장의 라벨 지도로 합친다 (영상은 실시간 처리가 아니라 프레임 추출 보조).
    - 장애물: persist 비율 이상 프레임에서 보이면 머무는 점유로 남긴다.
    - 보행자는 프레임마다 위치가 달라 다수결에서 빠지고, 그 밑의 노면이 드러난다.
    """
    if len(scenes) == 1:
        return scenes[0]
    h, w = scenes[0].class_map.shape
    rs = lambda a: cv2.resize(a, (w, h), interpolation=cv2.INTER_NEAREST)  # noqa: E731
    stack = np.stack([rs(s.class_map) for s in scenes])
    n = len(scenes)
    counts = np.stack([(stack == lab).sum(0) for lab in range(len(L))])
    counts[L.TRANSIENT] = 0
    fused = counts.argmax(0).astype(np.uint8)
    obstacle_share = (stack == L.OBSTACLE).sum(0) / n
    fused[(fused == L.OBSTACLE) & (obstacle_share < persist)] = L.UNKNOWN
    fused[obstacle_share >= persist] = L.OBSTACLE
    fused[counts.sum(0) == 0] = L.TRANSIENT  # 모든 프레임에서 사람에 가려진 곳

    mid = scenes[n // 2]
    confs = [s.confidence for s in scenes]
    conf = np.mean([cv2.resize(c, (w, h)) for c in confs], 0) if all(c is not None for c in confs) else None
    insts = [Instance(i.name, rs(i.mask.astype(np.uint8)) > 0, i.score) for s in scenes for i in s.instances]
    return SceneResult(fused, conf, mid.image, insts, mid.versions)

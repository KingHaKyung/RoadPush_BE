"""
통행 판정 규칙 엔진. 입력은 통합 라벨 지도(H×W, labels.L), 출력은 판정과 근거.

핵심 질문은 '장애물이 몇 개인가'가 아니라 '보도를 따라 끊기지 않고 지나갈 수 있는 통로가
얼마나 남았는가'다. 그래서 행(보도 진행 방향에 수직인 단면)마다 남은 빈 구간을 구하고,
이웃 행끼리 겹치는 구간을 이어 처음부터 끝까지 가는 경로 중 가장 넓은 경로의 병목 폭을 잰다.

판정은 상대 단면 폭으로 한다 (마스터 문서 9장 팀 합의, 답변서3: 비율 중심).
같은 행 안에서는 원근 축척이 거의 같으므로 '남은 연속 통로 폭 / 그 행의 추정 전체 보행폭 W'는 보정 없이 의미가 있다.
구간의 실측 보도 폭이 있으면 비율 × 실측 폭을 '추정 절대폭'으로 덧붙이지만 판정에는 쓰지 않는다.

가려진 전체 보도는 추정 대상이다: 물체 양옆과 인접 단면의 보도 경계로 W를 추정하고, 경계가 가려져 추정이
갈리면 낙관(차도 쪽 물체 = 보도 밖) / 보수(보도 위 점유) 두 번 계산해 판정이 다르면 판단 유보한다.
보행자는 점유가 아니라 지면을 가리는 것으로 보고 같은 방식으로 처리한다.
"""

import itertools
from dataclasses import asdict, dataclass, field

import cv2
import numpy as np

from ..labels import L

RULE_VERSION = "rules-2026.10-b"

# 픽셀 상태
FREE, OCC, HID, STOP = 0, 1, 2, 3


@dataclass
class Policy:
    """판정 기준값. 정책 버전과 함께 기록되며, 현장 실측으로 보정한다."""

    version: str = "policy-2026.10-draft"
    # 상대 유효폭 = 남은 연속 통로 / 추정 전체 보행폭. 초기 가정값이며 보정용 장소 실측으로 확정한다
    passable_min_ratio: float = 0.6
    blocked_below_ratio: float = 0.3

    # 품질 게이트 (판단 유보)
    min_rows: int = 30  # 보도가 잡힌 행 수
    max_unknown_ratio: float = 0.25  # 보도 범위 안 미분류 비율
    max_bike_ratio: float = 0.3  # 보도 범위 안 자전거도로 비율 → 보도 구분 불명확
    min_confidence: float = 0.45

    # 장애물 연결요소 중 아래쪽 이 비율을 점유로, 나머지를 가림으로 본다.
    # 팀 합의(마스터 문서 9장)는 전체 실루엣 = 점유 후보(1.0). 원근 과대 차감은 현장 비교로 확인 후 조정
    footprint_frac: float = 1.0
    min_row_sidewalk_px: int = 3
    max_row_gap: int = 3  # 보도가 안 잡힌 행을 이 수까지는 건너 잇는다


@dataclass
class ModeResult:
    bottleneck: float  # 병목 상대 유효폭
    bottleneck_row: int | None
    narrow_row_fraction: float  # 경로 중 통과 기준 미만 픽셀 행 비율 (실제 길이 비율 아님)
    path: list[tuple[int, int, int]]  # (행, 시작 열, 끝 열) 경로가 지나는 빈 구간


@dataclass
class PassabilityResult:
    verdict: str  # passable | narrowed | blocked | undetermined
    bottleneck_ratio: float | None  # 상대 유효폭 (남은 연속 통로 / 추정 전체 보행폭)
    bottleneck_m_est: float | None  # 비율 × 구간 실측 보도폭 (실측이 있을 때만, 판정에 안 씀)
    occupancy: int | None  # 병목 단면의 점유율(%) = 100 - 남은 폭 비율
    confidence: float | None  # roadpush_ai 엔진은 수치 대신 등급(stats.measurement_confidence)을 낸다
    uncertainty_code: str | None
    reasons: list[str]
    stats: dict = field(default_factory=dict)
    path: list[tuple[int, int, int]] = field(default_factory=list)
    transposed: bool = False
    # 보도 범위 안 '지면 점유' 픽셀 (원래 방향). 원인 집계·overlay용, 저장하지 않음
    occupied_mask: np.ndarray | None = field(default=None, repr=False)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("occupied_mask")
        return d


def _states(cls: np.ndarray, footprint_frac: float) -> np.ndarray:
    """라벨 → 픽셀 상태. 장애물은 연결요소별로 아래쪽 띠만 점유, 위는 가림."""
    st = np.full(cls.shape, STOP, np.uint8)
    st[(cls == L.SIDEWALK) | (cls == L.BIKE_LANE)] = FREE
    st[(cls == L.TRANSIENT) | (cls == L.UNKNOWN)] = HID

    obs = (cls == L.OBSTACLE).astype(np.uint8)
    n, comp, boxes, _ = cv2.connectedComponentsWithStats(obs, connectivity=8)
    rows = np.arange(cls.shape[0])[:, None]
    for i in range(1, n):
        top, h = boxes[i, cv2.CC_STAT_TOP], boxes[i, cv2.CC_STAT_HEIGHT]
        cut = top + h - max(1, int(round(h * footprint_frac)))
        m = comp == i
        st[m & (rows >= cut)] = OCC
        st[m & (rows < cut)] = HID
    return st


def _row_hull(st_row: np.ndarray, cls_row: np.ndarray, min_px: int):
    """
    한 행에서 보도 범위를 찾는다: FREE/OCC/HID가 이어진 구간 중 FREE가 가장 많은 것.
    끝이 장애물·가림이고 그 너머가 연석·벽이 아니면(차도·화면 끝) 경계가 불확실하다.
    돌려주는 값: (범위, 경계가 애매한 끝 구간 목록) 또는 None
    """
    spanable = st_row != STOP
    if not spanable.any():
        return None
    edges = np.diff(np.concatenate(([0], spanable.view(np.int8), [0])))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)  # [s, e)
    free = (st_row == FREE).astype(np.int32)
    csum = np.concatenate(([0], np.cumsum(free)))
    counts = csum[ends] - csum[starts]
    k = int(np.argmax(counts))
    if counts[k] < min_px:
        return None
    s, e = int(starts[k]), int(ends[k])
    w = len(st_row)

    def certain(end_idx: int, beyond: int) -> bool:
        if st_row[end_idx] == FREE:
            return True
        return 0 <= beyond < w and cls_row[beyond] in (L.CURB, L.BACKGROUND)

    # 애매한 끝의 비보도 덩어리: 보수 = 보도 위 점유, 낙관 = 차도 위 물체가 보도를 가린 것(빈 땅)
    amb: list[tuple[int, int]] = []
    if not certain(s, s - 1):
        i = s
        while i < e and st_row[i] != FREE:
            i += 1
        amb.append((s, i))
    if not certain(e - 1, e):
        i = e
        while i > s and st_row[i - 1] != FREE:
            i -= 1
        amb.append((i, e))
    return (s, e), amb


def _fill_covered_rows(st: np.ndarray, hulls: dict, min_cover: float = 0.7) -> int:
    """
    보도가 폭 전체로 덮인 행은 빈 보도 픽셀이 없어 범위를 못 찾는다. 위아래 보도 행의 경계를 이어(보간)
    그 사이가 대부분 장애물·가림이면 '덮인 보도'로 넣는다. 이걸 빼면 완전히 막힌 구간이 분석에서 사라진다.
    """
    rows = sorted(hulls)
    n = 0
    for r0, r1 in itertools.pairwise(rows):
        if r1 - r0 <= 1:
            continue
        (a0, b0), (a1, b1) = hulls[r0], hulls[r1]
        for r in range(r0 + 1, r1):
            t = (r - r0) / (r1 - r0)
            a, b = int(round(a0 + (a1 - a0) * t)), int(round(b0 + (b1 - b0) * t))
            if b <= a or (st[r, a:b] != STOP).mean() < min_cover:
                continue
            hulls[r] = (a, b)
            n += 1
    return n


def _free_runs(mask: np.ndarray, offset: int) -> list[tuple[int, int]]:
    edges = np.diff(np.concatenate(([0], mask.view(np.int8), [0])))
    return [
        (int(a) + offset, int(b) + offset)
        for a, b in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1), strict=True)
    ]


def _widest_path(
    rows: list[int],
    runs: dict[int, list[tuple[int, int]]],
    scale: dict[int, float],
    pass_value: float,
) -> ModeResult:
    """
    행을 따라 겹치는 빈 구간을 잇는 경로 중 병목이 가장 넓은 경로 (max-min 경로, 행 단위 DP).
    구간 값 = 폭 / 그 행의 추정 보행폭. 행 사이 이동은 두 구간이 겹친 폭으로 제한된다.
    왼쪽 40 + 상자 30 + 오른쪽 30이면 통로는 40과 30 두 개이고, 70으로 합치지 않는다.
    """
    best: dict[tuple[int, int], tuple[float, tuple | None, int | None]] = {}
    prev_row = None
    for r in rows:
        for iv in runs[r]:
            v = (iv[1] - iv[0]) * scale[r]
            cand = (v, None, r) if prev_row is None else (-1.0, None, None)
            if prev_row is not None:
                for piv in runs[prev_row]:
                    ov = min(iv[1], piv[1]) - max(iv[0], piv[0])
                    if ov <= 0 or (prev_row, piv) not in best:
                        continue
                    pv, _, prow_min = best[(prev_row, piv)]
                    ovv = ov * min(scale[r], scale[prev_row])
                    val = min(pv, v, ovv)
                    # 병목 위치: 값이 가장 작아진 행
                    loc = prow_min if val == pv else r
                    if val > cand[0]:
                        cand = (val, (prev_row, piv), loc)
            if cand[0] >= 0:
                best[(r, iv)] = cand
        prev_row = r

    last = rows[-1]
    ends = [(best[(last, iv)], iv) for iv in runs[last] if (last, iv) in best]
    if not ends:
        return ModeResult(0.0, None, 1.0, [])
    (val, back, loc), iv = max(ends, key=lambda x: x[0][0])

    path = [(last, *iv)]
    while back is not None:
        path.append((back[0], *back[1]))
        back = best[back][1]
    path.reverse()
    narrow = sum(1 for r, a, b in path if (b - a) * scale[r] < pass_value)
    return ModeResult(val, loc, narrow / len(path), path)


def _sections(rows: list[int], max_gap: int) -> list[list[int]]:
    secs: list[list[int]] = []
    for r in rows:
        if secs and r - secs[-1][-1] <= max_gap + 1:
            secs[-1].append(r)
        else:
            secs.append([r])
    return secs


def _verdict(value: float, passable: float, blocked: float) -> str:
    if value >= passable:
        return "passable"
    return "narrowed" if value >= blocked else "blocked"


def analyze_passability(
    class_map: np.ndarray,
    policy: Policy | None = None,
    confidence_map: np.ndarray | None = None,
    reference_width_m: float | None = None,
) -> PassabilityResult:
    """
    class_map: 통합 라벨 지도. 보도 진행 방향이 화면 세로(멀어지는 방향)라고 가정하고,
               보도가 가로로 놓였으면 자동으로 전치한다.
    reference_width_m: 구간 실측 보도 폭. 있으면 추정 절대폭을 덧붙인다 (판정에는 쓰지 않음).
    """
    p = policy or Policy()
    cls = class_map.astype(np.uint8)
    conf_map = confidence_map
    if conf_map is not None:
        cls = cls.copy()
        cls[conf_map < 0.3] = L.UNKNOWN

    transposed = _is_horizontal(cls)
    if transposed:
        cls = cls.T.copy()
        conf_map = conf_map.T if conf_map is not None else None

    st = _states(cls, p.footprint_frac)
    H = cls.shape[0]

    hull: dict[int, tuple[int, int]] = {}
    amb = np.zeros(cls.shape, bool)  # 경계가 애매한 끝의 장애물 픽셀
    uncertain_rows = 0
    for r in range(H):
        h = _row_hull(st[r], cls[r], p.min_row_sidewalk_px)
        if h is None:
            continue
        hull[r], edges = h
        for a, b in edges:
            amb[r, a:b] = True
        uncertain_rows += bool(edges)
    filled = _fill_covered_rows(st, hull)

    reasons: list[str] = []
    if len(hull) < p.min_rows:
        return _undetermined(
            "low_coverage", "보도 영역이 충분히 잡히지 않아 통로를 판단할 수 없음", {"rows": len(hull)}, transposed
        )

    # 가장 긴 연속 보도 구간만 분석 (멀리 떨어진 다른 보도 조각 제외)
    section = max(_sections(sorted(hull), p.max_row_gap), key=len)
    if len(section) < p.min_rows:
        return _undetermined(
            "low_coverage", "보도가 짧게 끊겨 잡혀 연속 통로를 판단할 수 없음", {"rows": len(section)}, transposed
        )

    # 품질 지표 (보수 범위 기준)
    tot = unknown = bike = hidden = occ = 0
    for r in section:
        a, b = hull[r]
        seg_cls, seg_st = cls[r, a:b], st[r, a:b]
        tot += b - a
        unknown += int((seg_cls == L.UNKNOWN).sum())
        bike += int((seg_cls == L.BIKE_LANE).sum())
        hidden += int((seg_st == HID).sum())
        occ += int((seg_st == OCC).sum())
    unknown_ratio, bike_ratio = unknown / tot, bike / tot
    stats = {
        "rows": len(section),
        "row_range": [section[0], section[-1]],
        "unknown_ratio": round(unknown_ratio, 3),
        "bike_ratio": round(bike_ratio, 3),
        "hidden_ratio": round(hidden / tot, 3),
        "occupied_ratio": round(occ / tot, 3),
        "edge_uncertain_ratio": round(uncertain_rows / max(1, len(hull)), 3),
        "covered_rows": filled,
        "policy": p.version,
    }
    if bike_ratio > p.max_bike_ratio:
        return _undetermined("mixed", "보도와 자전거도로 구분이 불명확함", stats, transposed)
    if unknown_ratio > p.max_unknown_ratio:
        return _undetermined("low_confidence", "보도 범위 안에 판별하지 못한 영역이 많음", stats, transposed)

    pass_v, block_v = p.passable_min_ratio, p.blocked_below_ratio

    def run_mode(optimistic: bool) -> ModeResult:
        runs, scale = {}, {}
        for r in section:
            a, b = hull[r]
            free = st[r, a:b] == FREE
            if optimistic:
                free |= (st[r, a:b] == HID) | amb[r, a:b]
            runs[r] = _free_runs(free, a) if b > a else []
            scale[r] = 1.0 / max(1, b - a)
        return _widest_path(section, runs, scale, pass_v)

    opt = run_mode(optimistic=True)
    pes = run_mode(optimistic=False)
    v_opt, v_pes = _verdict(opt.bottleneck, pass_v, block_v), _verdict(pes.bottleneck, pass_v, block_v)
    stats["optimistic"] = {"verdict": v_opt, "bottleneck": round(opt.bottleneck, 3)}
    stats["pessimistic"] = {"verdict": v_pes, "bottleneck": round(pes.bottleneck, 3)}

    # 신뢰도: 모델 확신도 × 미분류·가림·경계 불확실이 적을수록 높게
    model_conf = float(conf_map[section[0] : section[-1] + 1].mean()) if conf_map is not None else 0.8
    confidence = (
        model_conf * (1 - unknown_ratio) * (1 - 0.5 * stats["hidden_ratio"]) * (1 - 0.3 * stats["edge_uncertain_ratio"])
    )
    if transposed:
        # 보도가 화면을 가로지르는 옆 방향 사진: 행이 같은 깊이가 아니라 상대폭 근사가 약해진다
        confidence *= 0.8
        stats["view"] = "side"
    confidence = round(max(0.0, min(1.0, confidence)), 3)
    stats["model_confidence"] = round(model_conf, 3)

    if v_opt != v_pes:
        return _undetermined(
            "occluded",
            f"가림 때문에 판정이 갈림 (가린 곳을 빈 땅으로 보면 {_KO[v_opt]}, 막힘으로 보면 {_KO[v_pes]})",
            stats,
            transposed,
            confidence,
        )
    if confidence < p.min_confidence:
        return _undetermined("low_confidence", f"분석 신뢰도가 낮음 ({confidence:.2f})", stats, transposed, confidence)

    res = pes  # 두 판정이 같으므로 보수 쪽 수치를 근거로 쓴다
    occ_mask = np.zeros(cls.shape, bool)
    for r in section:
        a, b = hull[r]
        occ_mask[r, a:b] = st[r, a:b] == OCC
    est_m = round(res.bottleneck * reference_width_m, 2) if reference_width_m else None
    if res.path:
        reasons.append(
            f"남은 연속 통로가 가장 좁은 단면에서 보행폭의 {res.bottleneck:.0%} "
            f"(기준: 통과 ≥ {pass_v:.0%}, 단절 < {block_v:.0%})"
            + (f" · 실측 보도폭 기준 약 {est_m:.2f}m" if est_m is not None else "")
        )
    else:
        reasons.append("보도를 따라 처음부터 끝까지 이어지는 빈 통로가 없음")
    if v_pes == "blocked" and stats["occupied_ratio"] > 0:
        reasons.append("남은 보도로는 지나기 어려워 차도 우회가 필요할 가능성이 높음")

    return PassabilityResult(
        verdict=v_pes,
        bottleneck_ratio=round(res.bottleneck, 3),
        bottleneck_m_est=est_m,
        occupancy=int(round(100 * (1 - res.bottleneck))),
        confidence=confidence,
        uncertainty_code=None,
        reasons=reasons,
        stats={
            **stats,
            "bottleneck_row": res.bottleneck_row,
            "narrow_row_fraction": round(res.narrow_row_fraction, 3),
        },
        path=res.path,
        transposed=transposed,
        occupied_mask=occ_mask.T if transposed else occ_mask,
    )


_KO = {"passable": "통과", "narrowed": "좁아짐", "blocked": "단절"}


def _undetermined(code: str, reason: str, stats: dict, transposed: bool, confidence: float = 0.0) -> PassabilityResult:
    return PassabilityResult("undetermined", None, None, None, confidence, code, [reason], stats, [], transposed)


def _is_horizontal(cls: np.ndarray) -> bool:
    """보도 픽셀 분포의 주축이 가로에 가까우면 True (PCA)"""
    ys, xs = np.nonzero(cls == L.SIDEWALK)
    if len(xs) < 50:
        return False
    cov = np.cov(np.stack([xs, ys]).astype(np.float64))
    vals, vecs = np.linalg.eigh(cov)
    vx, vy = vecs[:, int(np.argmax(vals))]
    return abs(vx) > abs(vy) * 1.7  # 약 60° 이상 기울었을 때만

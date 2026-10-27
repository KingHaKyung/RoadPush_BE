"""
상대 위험도 (0~100). 원 제안서 식 (마스터 문서 9장)을 뼈대로, 백엔드가 세운 임시 기준을 더했다.
기준값은 RiskParams 하나에 모여 있고, 운영 중에는 DB(risk_policies)의 현재 버전을 쓴다 → 프론트에서 바꿀 수 있게.

    raw = 100 × Σ(가중치 × 항) / Σ(측정된 항의 가중치)   → 판정 구간 안에 비례로 넣기

항 (각 0~1, 클수록 위험). 측정할 수 없는 항은 0점으로 넣지 않고 빼며, 그러면 complete=False
- width     통로 폭: 1 − 유효보행폭비율 (rules.bottleneck_ratio). 원 제안서 w1
- length    단절 길이 / 전체 분석 길이. 원 제안서 w2. 단일 사진은 원근 때문에 잴 수 없어 지금은 늘 빠진다
- absolute  남은 통로의 추정 절대폭 (상대폭 × 구간 보도 폭). 휠체어 폭 이하면 1, 보도 유효폭 기준 이상이면 0
- detour    차도 우회 위험: 막혀서 차도로 내려설 때 그 도로가 넓을수록(차로 많을수록) 위험. 이미 보차혼용 도로면 1
- cause     원인 종류 (차량은 움직이고 크다 → 1, 적치물 0.6 …)

판정 구간: 판정과 점수 순서가 어긋나지 않게 통과·좁아짐·단절별 범위를 둔다.
  scale(기본) 점수 = 최소 + raw/100 × (최대 − 최소). 같은 판정 안에서 raw 순서가 그대로 남는다
  clamp      raw를 범위로 자른다 (v1 방식. 경계에 점수가 몰려 순위를 가리기 어려움)
판단 유보는 점수를 내지 않는다 (None). 측정 불가를 0점으로 표시하지 않는다.
기본값은 현장 보정 전 임시값이다 (docs/PASSABILITY.md 위험도 기준).
"""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .rules import PassabilityResult

RISK_VERSION = "risk-2026.10-v2"
TERMS = ("width", "length", "absolute", "detour", "cause")


class Band(BaseModel):
    min: int = Field(ge=0, le=100)
    max: int = Field(ge=0, le=100)


class Grade(BaseModel):
    min: int = Field(ge=0, le=100, description="이 점수 이상이면 이 등급")
    code: str = Field(min_length=1, max_length=16)
    label: str = Field(min_length=1, max_length=16)


class RiskParams(BaseModel):
    """위험도 기준값. 프론트 설정 화면이 이 형태 그대로 보내고 받는다"""

    weights: dict[str, float] = Field(
        default_factory=lambda: {"width": 0.5, "length": 0.2, "absolute": 0.1, "detour": 0.1, "cause": 0.1},
        description=f"항별 가중치 (0 이상, 0이면 그 항을 쓰지 않음). 항: {', '.join(TERMS)}",
    )
    # absolute: 남은 통로 추정 폭(m)
    min_pass_width_m: float = Field(0.9, gt=0, description="이 폭 이하면 absolute 항 1 (휠체어 통행 폭)")
    comfortable_width_m: float = Field(2.0, gt=0, description="이 폭 이상이면 absolute 항 0 (보도 유효폭 기준)")
    # detour: 구간 도로폭(m)·차로 수
    detour_narrow_road_m: float = Field(6.0, gt=0, description="도로폭이 이 이하면 detour 항 = detour_min")
    detour_wide_road_m: float = Field(20.0, gt=0, description="도로폭이 이 이상이면 detour 항 1")
    detour_min: float = Field(0.3, ge=0, le=1, description="좁은 길로 우회할 때의 최소 위험")
    detour_wide_lanes: int = Field(4, ge=1, description="왕복 차로가 이 이상이면 도로폭과 상관없이 detour 항 1")
    shared_road: float = Field(1.0, ge=0, le=1, description="보행면이 보차혼용 도로일 때 detour 항")
    cause_weights: dict[str, float] = Field(
        default_factory=lambda: {"차량": 1.0, "공사시설": 0.8, "PM": 0.6, "적치물": 0.6, "장애물": 0.6},
        description="원인별 위험 (0~1). 원인이 여럿이면 가장 큰 값. 목록에 없는 원인은 cause_default",
    )
    cause_default: float = Field(0.6, ge=0, le=1)
    bands: dict[str, Band] = Field(
        default_factory=lambda: {
            "passable": Band(min=0, max=29),
            "narrowed": Band(min=30, max=69),
            "blocked": Band(min=70, max=100),
        },
        description="판정별 점수 범위 (판정과 점수 순서를 맞춤)",
    )
    band_mode: Literal["scale", "clamp"] = Field(
        "scale", description="scale: 0~100 계산값을 범위 안에 비례로 넣음 / clamp: 범위 밖 값을 자름"
    )
    grades: list[Grade] = Field(
        default_factory=lambda: [
            Grade(min=70, code="high", label="높음"),
            Grade(min=40, code="medium", label="보통"),
            Grade(min=0, code="low", label="낮음"),
        ],
        description="화면 등급. min이 큰 것부터 처음 맞는 등급",
    )

    @model_validator(mode="after")
    def _check(self):
        unknown = set(self.weights) - set(TERMS)
        if unknown:
            raise ValueError(f"알 수 없는 항: {', '.join(sorted(unknown))} (쓸 수 있는 항: {', '.join(TERMS)})")
        if any(v < 0 for v in self.weights.values()) or sum(self.weights.values()) <= 0:
            raise ValueError("가중치는 0 이상이고 합이 0보다 커야 합니다")
        if self.min_pass_width_m >= self.comfortable_width_m:
            raise ValueError("min_pass_width_m은 comfortable_width_m보다 작아야 합니다")
        if self.detour_narrow_road_m >= self.detour_wide_road_m:
            raise ValueError("detour_narrow_road_m은 detour_wide_road_m보다 작아야 합니다")
        if any(not 0 <= v <= 1 for v in self.cause_weights.values()):
            raise ValueError("원인별 위험은 0~1입니다")
        if set(self.bands) != {"passable", "narrowed", "blocked"}:
            raise ValueError("bands에는 passable·narrowed·blocked 세 판정이 모두 있어야 합니다")
        if any(b.min > b.max for b in self.bands.values()):
            raise ValueError("bands의 min은 max 이하여야 합니다")
        if not self.grades or min(g.min for g in self.grades) != 0:
            raise ValueError("grades에는 min=0인 등급이 있어야 합니다 (모든 점수가 등급을 가짐)")
        return self


@dataclass
class RiskContext:
    """분석 결과 밖에서 오는 입력 (구간·원인). 없으면 그 항을 뺀다"""

    causes: list[str] | None = None
    road_width_m: float | None = None
    road_lanes: int | None = None
    selected_surface: str | None = None  # sidewalk | road(보차혼용) — AI Phase 6 선택
    length_ratio: float | None = None  # 단절 길이 / 전체 분석 길이 (정의되면)


def _ramp(x: float, lo: float, hi: float) -> float:
    """x ≤ lo → 0, x ≥ hi → 1, 사이는 직선"""
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def terms(res: PassabilityResult, ctx: RiskContext, p: RiskParams) -> dict[str, float | None]:
    out: dict[str, float | None] = dict.fromkeys(TERMS)
    out["width"] = 1 - max(0.0, min(1.0, res.bottleneck_ratio))
    if ctx.length_ratio is not None:
        out["length"] = max(0.0, min(1.0, ctx.length_ratio))
    if res.bottleneck_m_est is not None:
        out["absolute"] = 1 - _ramp(res.bottleneck_m_est, p.min_pass_width_m, p.comfortable_width_m)
    if ctx.selected_surface == "road":
        out["detour"] = p.shared_road
    elif ctx.road_lanes is not None and ctx.road_lanes >= p.detour_wide_lanes:
        out["detour"] = 1.0
    elif ctx.road_width_m is not None:
        r = _ramp(ctx.road_width_m, p.detour_narrow_road_m, p.detour_wide_road_m)
        out["detour"] = p.detour_min + (1 - p.detour_min) * r
    if ctx.causes:
        out["cause"] = max(p.cause_weights.get(c, p.cause_default) for c in ctx.causes)
    return out


def grade_of(score: int | None, p: RiskParams) -> Grade | None:
    if score is None:
        return None
    return next(g for g in sorted(p.grades, key=lambda g: -g.min) if score >= g.min)


def risk_score(
    res: PassabilityResult,
    params: RiskParams | None = None,
    ctx: RiskContext | None = None,
    policy_version: int | None = None,
) -> tuple[int | None, dict]:
    p = params or RiskParams()
    base = {"version": RISK_VERSION, "policy_version": policy_version}
    if res.verdict == "undetermined" or res.bottleneck_ratio is None:
        return None, {**base, "terms": None, "complete": False}

    t = terms(res, ctx or RiskContext(), p)
    used = {k: v for k, v in t.items() if v is not None and p.weights.get(k, 0) > 0}
    wsum = sum(p.weights[k] for k in used)
    raw = 100 * sum(p.weights[k] * v for k, v in used.items()) / wsum
    band = p.bands[res.verdict]
    if p.band_mode == "scale":
        score = int(round(band.min + raw / 100 * (band.max - band.min)))
    else:
        score = int(round(max(band.min, min(band.max, raw))))
    missing = [k for k, w in p.weights.items() if w > 0 and t[k] is None]
    g = grade_of(score, p)
    return score, {
        **base,
        "formula": "R = Σ(가중치×항)/Σ(측정된 항의 가중치) × 100 → 판정 구간",
        "terms": {k: None if v is None else round(v, 3) for k, v in t.items()},
        "weights": {k: p.weights.get(k, 0) for k in TERMS},
        "raw": round(raw, 1),
        "band": band.model_dump() | {"mode": p.band_mode},
        "grade": g.code,
        "grade_label": g.label,
        "complete": not missing,
        "missing": missing,
        "note": f"측정하지 못한 항({', '.join(missing)})은 빼고 계산한 부분 지표" if missing else None,
    }

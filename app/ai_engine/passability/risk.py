"""
상대 위험도. 원 제안서 식 (마스터 문서 9장):
    R = w1 × (1 − 유효보행폭비율) + w2 × (단절길이 / 전체 분석길이)     → 0~100

- 유효보행폭비율 = 남은 연속 통로의 병목 상대폭 (rules.bottleneck_ratio)
- 길이 항: 단일 사진의 픽셀 행 수는 원근이 달라 실제 길이 비율이 아니다. 길이를 정의할 수 없으면
  '측정 불가'(None)로 남기고, 이때 점수는 폭 항만으로 낸 **부분 상대 지표**임을 complete=False로 표시한다.
- 판단 유보는 점수를 내지 않는다 (None). 측정 불가를 0점으로 표시하지 않는다.
- 가중치는 현장 보정 대상이다.
"""

from dataclasses import dataclass

from .rules import PassabilityResult

RISK_VERSION = "risk-2026.10-draft"


@dataclass
class RiskWeights:
    w1: float = 0.7
    w2: float = 0.3


def risk_score(
    res: PassabilityResult, weights: RiskWeights | None = None, length_ratio: float | None = None
) -> tuple[int | None, dict]:
    w = weights or RiskWeights()
    if res.verdict == "undetermined" or res.bottleneck_ratio is None:
        return None, {"version": RISK_VERSION, "width_term": None, "length_term": None, "complete": False}

    width_term = 1 - max(0.0, min(1.0, res.bottleneck_ratio))
    if length_ratio is None:
        score = 100 * width_term
    else:
        score = 100 * (w.w1 * width_term + w.w2 * length_ratio) / (w.w1 + w.w2)
    return int(round(score)), {
        "version": RISK_VERSION,
        "formula": "R = w1(1-유효보행폭비율) + w2(단절길이/전체분석길이)",
        "weights": {"w1": w.w1, "w2": w.w2},
        "width_term": round(width_term, 3),
        "length_term": None if length_ratio is None else round(length_ratio, 3),
        "complete": length_ratio is not None,
        "note": None if length_ratio is not None else "단일 사진은 길이 항 측정 불가 → 폭 항만 반영한 부분 상대 지표",
    }

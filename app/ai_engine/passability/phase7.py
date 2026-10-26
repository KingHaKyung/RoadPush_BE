"""
AI 팀 Phase 7-B 결과(phase7_b_result_v2) → 백엔드 판정 (통과 가능 / 좁아짐 / 단절·우회 필요 / 판단 유보).

AI 계약(RoadPush_AI docs/PHASE7B_PRODUCTION_LOGIC.md 4·5·7장)의 소비 규칙을 그대로 따른다.
- 자동 판정은 result_authority=authoritative 이고 대표 비율(minimum_passable_ratio)이 있을 때만
- provisional_review(ambiguous_hold)는 판정·위험도·집계에 쓰지 않는다 → 판단 유보(사람 검토), 가계산은 근거에만
- raw_minimum_passable_ratio로 상태를 정하지 않는다. null을 0으로 바꾸지 않는다
- effective_blocked_width_m는 장애물 실폭이 아니다 → 화면에 장애물 폭으로 쓰지 않는다

passability_state가 relative_only(보도 실폭·통행 폭 입력이 없음)면 상대폭 기준(rules.Policy)으로 나눈다.
이 기준은 백엔드 정책값(마스터 문서 9장 비율 중심 합의)이며 현장 실측으로 보정한다.
"""

from .rules import PassabilityResult, Policy

POLICY_VERSION = "phase7-consumer-2026.10"

# AI 사유 → (uncertainty_code ≤20자, 화면 사유)
# 프론트 Condition(night·rain·occluded·mixed) 밖의 코드는 화면에 사유 문장으로 보인다
ABSTENTION = {
    "phase7a_failed": ("boundary_failed", "사진에서 보행면의 양쪽 경계를 확정하지 못해 통로 폭을 잴 수 없음"),
    "occupancy_coverage_unverified": (
        "occupancy_unverified",
        "장애물이 차지한 범위를 확인할 수 없어 통로 폭을 잴 수 없음",
    ),
    "insufficient_consecutive_eligible_rows": ("low_coverage", "측정할 수 있는 보행면 단면이 연속으로 충분하지 않음"),
    "measurement_run_coverage_below_minimum": ("low_coverage", "측정할 수 있는 보행면 단면이 연속으로 충분하지 않음"),
    "no_structural_support_in_authorized_scope": ("low_coverage", "확정된 경계 안에 보행면이 거의 잡히지 않음"),
    "unresolved_boundary_support": ("low_coverage", "보행면 경계의 지지 근거가 부족함"),
}
SURFACE_KO = {"sidewalk": "보도", "road": "보차혼용 도로"}
_KO = {"passable": "통과 가능", "narrowed": "좁아짐", "blocked": "단절·우회 필요"}


def _relative_verdict(ratio: float, p: Policy) -> str:
    if ratio <= 0:
        return "blocked"
    if ratio >= p.passable_min_ratio:
        return "passable"
    return "narrowed" if ratio >= p.blocked_below_ratio else "blocked"


def _stats(record: dict, selection: dict | None) -> dict:
    """근거(evidence.stats)에 남길 요약. 전체 7-B 기록은 evidence.phase7b에 따로 둔다"""
    rows = (record.get("diagnostics") or {}).get("rows") or {}
    return {
        "selected_surface": record.get("selected_surface"),
        "selector_state": (selection or {}).get("state"),
        "phase7a_status": record.get("phase7a_status"),
        "result_authority": record.get("result_authority"),
        "measurement_scope": record.get("measurement_scope"),
        "measurement_confidence": record.get("measurement_confidence"),
        "human_review_required": record.get("human_review_required"),
        "passability_state": record.get("passability_state"),
        "minimum_passable_ratio": record.get("minimum_passable_ratio"),
        "maximum_obstructed_width_ratio": record.get("maximum_obstructed_width_ratio"),
        "obstacle_area_ratio": record.get("obstacle_area_ratio"),
        "measurement_run_coverage": rows.get("measurement_run_coverage"),
        "abstention_reasons": list(record.get("abstention_reasons") or []),
        "flags": list(record.get("flags") or []),
        "policy": POLICY_VERSION,
    }


def _undetermined(code: str, reason: str, stats: dict, extra: list[str] | None = None) -> PassabilityResult:
    return PassabilityResult("undetermined", None, None, None, None, code, [reason, *(extra or [])], stats)


def to_result(record: dict, policy: Policy | None = None, selection: dict | None = None) -> PassabilityResult:
    p = policy or Policy()
    stats = _stats(record, selection)
    ratio = record.get("minimum_passable_ratio")
    authority = record.get("result_authority")
    surface = SURFACE_KO.get(record.get("selected_surface") or "", "보행면")

    if record.get("selected_surface") is None:
        return _undetermined("surface_uncertain", "사진에서 보도·차도 중 걸어갈 면을 정하지 못함", stats)

    if authority == "provisional_review":
        extra = []
        if ratio is not None:
            # 검토용 가계산: 판정·위험도에 쓰지 않고 검토자에게만 보인다
            extra.append(f"검토용 가계산: 남은 연속 통로가 가장 좁은 곳에서 {surface} 폭의 {ratio:.0%}")
        return _undetermined(
            "boundary_review", f"{surface} 경계를 자동으로 확정하지 못해 사람 검토가 필요함", stats, extra
        )

    if authority != "authoritative" or ratio is None:
        reasons = list(record.get("abstention_reasons") or [])
        code, text = next((ABSTENTION[r] for r in reasons if r in ABSTENTION), ("undetermined", None))
        if text is None:
            text = "통로 폭을 측정할 근거가 부족함" + (f" ({', '.join(reasons)})" if reasons else "")
        return _undetermined(code, text, stats)

    state = record.get("passability_state")
    if state in _KO:  # AI가 판정: 연결 통로 없음(비율 0) 또는 실폭·통행 폭 입력으로 m 단위 판정
        verdict = state
        if record.get("agent_width_m"):
            basis = f"필요 폭 {record['required_safe_width_m'] or record['agent_width_m']:.2f}m 기준"
        else:
            basis = "연결된 통로 없음"
    else:  # relative_only
        verdict = _relative_verdict(ratio, p)
        basis = f"상대폭 기준: 통과 ≥ {p.passable_min_ratio:.0%}, 단절 < {p.blocked_below_ratio:.0%}"
    absolute = record.get("absolute_passable_width_m")
    reasons = [
        f"남은 연속 통로가 가장 좁은 곳에서 {surface} 폭의 {ratio:.0%} ({basis})"
        + (f" · 보도 실폭 기준 약 {absolute:.2f}m" if absolute is not None else "")
    ]
    if ratio <= 0:
        reasons.append(f"{surface}를 따라 처음부터 끝까지 이어지는 빈 통로가 없음")
    if verdict == "blocked":
        reasons.append("남은 폭으로는 지나기 어려워 차도 우회가 필요할 가능성이 높음")
    if record.get("measurement_scope") == "partial":
        reasons.append("경계가 확정된 앞부분만 측정함 (뒤쪽은 검토 권장)")
    bottleneck = record.get("bottleneck") or {}
    if bottleneck.get("cause") == "structural_or_unrecognized":
        reasons.append("가장 좁은 곳은 인식된 장애물이 아니라 보행면 형태·미인식 물체 때문일 수 있음")

    return PassabilityResult(
        verdict=verdict,
        bottleneck_ratio=round(float(ratio), 3),
        bottleneck_m_est=round(float(absolute), 2) if absolute is not None else None,
        occupancy=int(round(100 * (1 - float(ratio)))),
        confidence=None,  # AI는 수치 신뢰도가 아니라 measurement_confidence 등급을 낸다 (stats에 기록)
        uncertainty_code=None,
        reasons=reasons,
        stats=stats,
    )

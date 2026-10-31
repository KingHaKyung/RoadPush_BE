"""DB 행 → API 응답. 최신 관측·참고 관측 선택과 '분석에 붙은 검수' 규칙이 여기 있다."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..ai_engine.passability.phase7 import provisional_estimate
from ..api.schemas import (
    ACTION_LABEL,
    PASS_LABEL,
    ActionBrief,
    ActionLogOut,
    ActionOut,
    HistoryItem,
    Images,
    Location,
    ObservationOut,
    ReviewOut,
)
from ..config import get_settings
from ..db.models import Action, Analysis, Observation

FileUrl = Callable[[str], str]


def utc(dt: datetime) -> datetime:
    """SQLite는 시간대를 버리고 돌려주므로 UTC로 본다"""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def local(dt: datetime) -> datetime:
    """응답 표시용: 4.4 예시처럼 +09:00으로 돌려준다"""
    return utc(dt).astimezone(timezone(timedelta(hours=get_settings().display_utc_offset_hours)))


def obs_query():
    return select(Observation).options(
        selectinload(Observation.site),
        selectinload(Observation.analyses).selectinload(Analysis.reviews),
        selectinload(Observation.notes),
    )


def latest_analysis(obs: Observation) -> Analysis | None:
    """취소한 재분석은 건너뛴다 → 취소하면 이전 분석 결과로 돌아간다"""
    return next((a for a in reversed(obs.analyses) if a.status != "cancelled"), None)


def latest_completed(obs: Observation) -> Analysis | None:
    return next((a for a in reversed(obs.analyses) if a.status == "completed"), None)


def actions_by_before(db: Session, obs_ids: list[str]) -> dict[str, Action]:
    if not obs_ids:
        return {}
    rows = db.scalars(
        select(Action)
        .where(Action.before_observation_id.in_(obs_ids))
        .options(selectinload(Action.logs))
        .order_by(Action.created_at)
    )
    return {a.before_observation_id: a for a in rows}  # 관측당 최신 조치


def observation_out(obs: Observation, url: FileUrl, action: Action | None = None) -> ObservationOut:
    ana = latest_analysis(obs)
    done = ana is not None and ana.status == "completed"
    # 검수는 해당 분석에 붙은 것만. 재분석하면 새 분석은 미검수에서 시작한다. 최신 행이 취소면 미검수
    rev = ana.reviews[-1] if ana and ana.reviews else None
    if rev is not None and rev.status == "cancelled":
        rev = None
    files = ana.files if done else {}

    hist: list[HistoryItem] = []
    for a in obs.analyses:
        kind = "재분석" if a.trigger == "reanalysis" else "AI 분석"
        t = a.finished_at or a.created_at
        if a.status == "completed":
            txt = f"{kind} 완료 · {PASS_LABEL.get(a.passability or '', '')}"
            if a.risk_score is not None:
                txt += f" (상대 위험도 {a.risk_score})"
            hist.append(HistoryItem(t=t, txt=txt, ai=True))
        elif a.status == "failed":
            hist.append(HistoryItem(t=t, txt=f"{kind} 실패", ai=True))
        elif a.status == "cancelled":
            hist.append(HistoryItem(t=t, txt="재분석 요청 취소"))
        for r in a.reviews:
            if r.status == "confirmed":
                txt = "AI 판정 확인"
            elif r.status == "corrected":
                txt = f"판정 수정 → {PASS_LABEL.get(r.human_passability or '')}"
            else:
                txt = "검수 취소"
            hist.append(HistoryItem(t=r.created_at, txt=txt + (f" · {r.comment}" if r.comment else ""), by=r.reviewer))
    for n in obs.notes:
        hist.append(
            HistoryItem(
                t=n.created_at,
                txt=f"메모: {n.content}",
                by=n.author,
                id=n.id,
                content=n.content,
                author=n.author,
                created_at=local(n.created_at),
                edited_at=local(n.edited_at) if n.edited_at else None,
            )
        )
    if action:
        for lg in action.logs:
            txt = f"처리 상태: {ACTION_LABEL[lg.state]}" + (f" ({lg.dept})" if lg.dept else "")
            hist.append(HistoryItem(t=lg.created_at, txt=txt + (f" · {lg.note}" if lg.note else ""), by=lg.actor))
    for h in hist:
        h.t = local(h.t)
    hist.sort(key=lambda h: h.t)

    # location은 구간 대표 위치: 프론트가 좌표로 지점을 묶으므로(pinKey) 같은 구간 관측은 같은 좌표로 준다.
    # 사진의 실제 촬영 좌표는 capture_location
    cap = Location(latitude=obs.lat, longitude=obs.lng) if obs.lat is not None and obs.lng is not None else None
    loc = Location(latitude=obs.site.lat, longitude=obs.site.lng)
    risk = (ana.evidence or {}).get("risk", {}) if done else {}
    return ObservationOut(
        observation_id=obs.id,
        analysis_id=ana.id if ana else None,
        site_id=obs.site_id,
        location=loc,
        captured_at=local(obs.captured_at),
        processing_status=ana.status if ana else "pending",
        passability=ana.passability if done else None,
        risk_score=ana.risk_score if done else None,
        uncertainty_reason=ana.uncertainty_reason if done else None,
        images=Images(
            original=url(obs.original_key),
            surface_overlay=url(files["surface_overlay"]) if "surface_overlay" in files else None,
            obstacle_overlay=url(files["obstacle_overlay"]) if "obstacle_overlay" in files else None,
        ),
        review_status=rev.status if rev else "pending",
        human_passability=rev.human_passability if rev and rev.status == "corrected" else None,
        site_name=obs.site.name,
        link_id=obs.site.link_id,
        source=obs.source,
        device=obs.device,
        created_at=local(obs.created_at),
        capture_location=cap,
        location_source=obs.location_source,
        area=obs.area,
        addr=obs.addr,
        occupancy=ana.occupancy if done else None,
        bottleneck_ratio=ana.bottleneck_ratio if done else None,
        bottleneck_m_est=ana.bottleneck_m_est if done else None,
        risk_complete=bool(risk.get("complete")),
        risk_grade=risk.get("grade"),
        risk_grade_label=risk.get("grade_label"),
        risk_policy_version=risk.get("policy_version"),
        causes=ana.causes if done else [],
        uncertainty_code=ana.uncertainty_code if done else None,
        provisional=provisional_estimate((ana.evidence or {}).get("stats")) if done else None,
        confidence=ana.confidence if done else None,
        reasons=(ana.evidence or {}).get("reasons", []) if done else [],
        analysis_trigger=ana.trigger if ana else None,
        attempts=ana.attempts if ana else 0,
        failure_reason=(ana.error or "").splitlines()[0] if ana and ana.status == "failed" else None,
        review=ReviewOut(
            id=rev.id,
            analysis_id=rev.analysis_id,
            status=rev.status,
            human_passability=rev.human_passability,
            comment=rev.comment,
            reviewer=rev.reviewer,
            created_at=local(rev.created_at),
        )
        if rev
        else None,
        action=ActionBrief(
            action_id=action.id if action else None,
            state=action.state if action else "none",
            label=ACTION_LABEL[action.state if action else "none"],
            dept=action.dept if action else None,
        ),
        history=hist,
        model_version=ana.model_version if ana else None,
        rule_version=ana.rule_version if ana else None,
    )


def observations_out(db: Session, obs: list[Observation], url: FileUrl) -> list[ObservationOut]:
    acts = actions_by_before(db, [o.id for o in obs])
    return [observation_out(o, url, acts.get(o.id)) for o in obs]


def action_out(a: Action) -> ActionOut:
    return ActionOut(
        id=a.id,
        site_id=a.site_id,
        before_observation_id=a.before_observation_id,
        after_observation_id=a.after_observation_id,
        state=a.state,
        label=ACTION_LABEL[a.state],
        dept=a.dept,
        content=a.content,
        created_at=local(a.created_at),
        updated_at=local(a.updated_at),
        logs=[ActionLogOut.model_validate(lg).model_copy(update={"created_at": local(lg.created_at)}) for lg in a.logs],
    )


def latest_and_reference(observations: list[Observation]) -> tuple[Observation, Observation | None]:
    """
    촬영시각이 가장 늦은 관측이 최신이다 (등록·분석 순서가 아님).
    최신 관측이 대기·처리중·실패일 때만 이전 완료 관측을 참고로 붙인다.
    최신이 판단 유보로 완료됐으면 이전 정상 판정으로 대체하지 않는다.
    """
    ordered = sorted(observations, key=lambda o: (utc(o.captured_at), utc(o.created_at)))
    latest = ordered[-1]
    la = latest_analysis(latest)
    if la is not None and la.status == "completed":
        return latest, None
    ref = next((o for o in reversed(ordered[:-1]) if latest_completed(o) is not None), None)
    return latest, ref

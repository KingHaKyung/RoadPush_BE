"""
위험도 기준(risk_policies) 읽기·저장과 점수 다시 계산.
분석 결과(병목 비율·추정 폭·판정·원인)와 구간(도로폭·차로)은 DB에 남아 있으므로,
기준을 바꾸면 모델을 다시 돌리지 않고 점수만 다시 낸다.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..ai_engine.passability.risk import RiskContext, RiskParams, risk_score
from ..ai_engine.passability.rules import PassabilityResult
from ..db.models import Analysis, Observation, RiskPolicy, Site


def current(db: Session) -> RiskPolicy | None:
    return db.scalar(select(RiskPolicy).order_by(RiskPolicy.version.desc()).limit(1))


def current_params(db: Session) -> tuple[RiskParams, int | None]:
    """(기준, 버전). 저장한 기준이 없으면 (기본 기준, None)"""
    row = current(db)
    return (RiskParams.model_validate(row.params), row.version) if row else (RiskParams(), None)


def save(db: Session, params: RiskParams, note: str | None, actor: str | None) -> RiskPolicy:
    version = (db.scalar(select(func.max(RiskPolicy.version))) or 0) + 1
    row = RiskPolicy(version=version, params=params.model_dump(mode="json"), note=note, created_by=actor)
    db.add(row)
    db.flush()
    return row


def context(ana: Analysis, site: Site) -> RiskContext:
    stats = (ana.evidence or {}).get("stats") or {}
    return RiskContext(
        causes=list(ana.causes or []),
        road_width_m=site.road_width_m,
        road_lanes=site.road_lanes,
        selected_surface=stats.get("selected_surface"),
    )


def _result(ana: Analysis) -> PassabilityResult:
    return PassabilityResult(
        ana.passability, ana.bottleneck_ratio, ana.bottleneck_m_est, ana.occupancy, ana.confidence, None, [], {}
    )


def score(ana: Analysis, site: Site, params: RiskParams, version: int | None) -> tuple[int | None, dict]:
    return risk_score(_result(ana), params, context(ana, site), version)


def completed(db: Session, site_id: str | None = None) -> list[Analysis]:
    q = (
        select(Analysis)
        .where(Analysis.status == "completed")
        .options(selectinload(Analysis.observation).selectinload(Observation.site))
    )
    if site_id:
        q = q.join(Observation).where(Observation.site_id == site_id)
    return list(db.scalars(q))


def recompute(db: Session, params: RiskParams, version: int | None, site_id: str | None = None) -> int:
    """완료된 분석의 점수를 이 기준으로 다시 낸다 (site_id면 그 구간만). 바뀐 건수"""
    n = 0
    for ana in completed(db, site_id):
        new, detail = score(ana, ana.observation.site, params, version)
        if new != ana.risk_score or (ana.evidence or {}).get("risk") != detail:
            ana.risk_score = new
            ana.evidence = {**(ana.evidence or {}), "risk": detail}  # JSON 컬럼은 새 dict로 바꿔야 저장된다
            n += 1
    return n

"""
위험도 기준 (프론트 설정 화면용). 기준을 저장하면 새 버전이 되고, 완료된 분석 점수를 다시 낸다.
미리보기는 저장하지 않고 바뀔 점수만 보여 준다.
"""

from collections import Counter
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai_engine.passability.risk import RISK_VERSION, TERMS, RiskParams, grade_of
from ..db import get_db
from ..db.models import RiskPolicy
from ..services import risk_policy, views

router = APIRouter(prefix="/risk-policy", tags=["risk-policy"])


class RiskPolicyOut(BaseModel):
    version: int | None = Field(description="저장한 기준 버전. null이면 아직 저장한 기준이 없어 기본 기준을 씀")
    formula: str = RISK_VERSION
    params: RiskParams
    defaults: RiskParams = Field(default_factory=RiskParams, description="기본 기준 (되돌리기용)")
    terms: list[str] = list(TERMS)
    note: str | None = None
    created_by: str | None = None
    created_at: datetime | None = None


class RiskPolicyIn(BaseModel):
    params: RiskParams
    note: str | None = Field(None, description="바꾼 이유")
    actor: str | None = None
    apply_to_existing: bool = Field(True, description="완료된 분석의 점수도 새 기준으로 다시 계산")


class RiskPolicySaved(RiskPolicyOut):
    recomputed: int = Field(0, description="점수가 다시 계산된 분석 수")


class PreviewIn(BaseModel):
    params: RiskParams


def _out(row: RiskPolicy | None) -> RiskPolicyOut:
    if row is None:
        return RiskPolicyOut(version=None, params=RiskParams())
    return RiskPolicyOut(
        version=row.version,
        params=RiskParams.model_validate(row.params),
        note=row.note,
        created_by=row.created_by,
        created_at=views.local(row.created_at),
    )


@router.get("", response_model=RiskPolicyOut)
def get_policy(db: Session = Depends(get_db)):
    """현재 위험도 기준과 기본값, 각 항목 설명은 /docs의 RiskParams 스키마"""
    return _out(risk_policy.current(db))


@router.get("/history", response_model=list[RiskPolicyOut])
def history(db: Session = Depends(get_db)):
    return [_out(r) for r in db.scalars(select(RiskPolicy).order_by(RiskPolicy.version.desc()))]


@router.put("", response_model=RiskPolicySaved)
def put_policy(body: RiskPolicyIn, db: Session = Depends(get_db)):
    """새 기준 저장 (새 버전). 이전 기준으로 되돌리려면 history의 params를 다시 보낸다"""
    row = risk_policy.save(db, body.params, body.note, body.actor)
    n = risk_policy.recompute(db, body.params, row.version) if body.apply_to_existing else 0
    db.commit()
    return RiskPolicySaved(**_out(row).model_dump(), recomputed=n)


@router.post("/preview")
def preview(body: PreviewIn, db: Session = Depends(get_db)):
    """저장하지 않고, 이 기준이면 완료된 분석 점수·등급이 어떻게 바뀌는지"""
    cur, _ = risk_policy.current_params(db)
    items = []
    for ana in risk_policy.completed(db):
        new, detail = risk_policy.score(ana, ana.observation.site, body.params, None)
        old_grade, new_grade = grade_of(ana.risk_score, cur), grade_of(new, body.params)
        items.append({
            "observation_id": ana.observation_id,
            "analysis_id": ana.id,
            "passability": ana.passability,
            "current": {"risk_score": ana.risk_score, "grade": old_grade.code if old_grade else None},
            "preview": {"risk_score": new, "grade": new_grade.code if new_grade else None, "terms": detail["terms"]},
        })  # fmt: skip
    return {
        "count": len(items),
        "changed": sum(i["current"]["risk_score"] != i["preview"]["risk_score"] for i in items),
        "grades": {
            "current": Counter(i["current"]["grade"] for i in items if i["current"]["grade"]),
            "preview": Counter(i["preview"]["grade"] for i in items if i["preview"]["grade"]),
        },
        "items": items,
    }

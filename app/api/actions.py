"""F5 현장조치. 조치는 구간 단위이고 조치 전 관측(필수)과 재촬영 후 관측(선택)을 연결한다."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..db.models import Action, ActionLog, Observation, now
from ..services import views
from .deps import file_url_for
from .schemas import ActionIn, ActionOut, ActionPatch, BulkActionIn, ObservationActionIn, ObservationOut

router = APIRouter(tags=["actions"])


def _action(db: Session, action_id: str) -> Action:
    a = db.scalar(select(Action).where(Action.id == action_id).options(selectinload(Action.logs)))
    if a is None:
        raise HTTPException(404, "조치를 찾을 수 없습니다")
    return a


def _obs(db: Session, obs_id: str) -> Observation:
    o = db.get(Observation, obs_id)
    if o is None:
        raise HTTPException(404, f"관측을 찾을 수 없습니다: {obs_id}")
    return o


def _create(db: Session, before: Observation, state: str, dept, content, actor) -> Action:
    a = Action(site_id=before.site_id, before_observation_id=before.id, state=state, dept=dept, content=content)
    db.add(a)
    db.flush()
    db.add(ActionLog(action_id=a.id, state=state, dept=dept, note=content, actor=actor))
    return a


def _update(db: Session, a: Action, state=None, dept=None, content=None, after_id=None, actor=None, note=None):
    if after_id is not None:
        after = _obs(db, after_id)
        if after.site_id != a.site_id:
            raise HTTPException(422, "조치 후 관측은 같은 구간의 관측이어야 합니다")
        before = _obs(db, a.before_observation_id)
        if views.utc(after.captured_at) <= views.utc(before.captured_at):
            raise HTTPException(422, "조치 후 관측은 조치 전 관측보다 나중에 촬영돼야 합니다")
        a.after_observation_id = after_id
    if state is not None:
        a.state = state
    if dept is not None:
        a.dept = dept
    if content is not None:
        a.content = content
    a.updated_at = now()
    db.add(ActionLog(action_id=a.id, state=a.state, dept=a.dept, note=note, actor=actor))


@router.post("/actions", response_model=ActionOut, status_code=201)
def create_action(body: ActionIn, db: Session = Depends(get_db)):
    before = _obs(db, body.before_observation_id)
    a = _create(db, before, body.state, body.dept, body.content, body.actor)
    db.commit()
    return views.action_out(_action(db, a.id))


@router.patch("/actions/{action_id}", response_model=ActionOut)
def update_action(action_id: str, body: ActionPatch, db: Session = Depends(get_db)):
    a = _action(db, action_id)
    _update(db, a, body.state, body.dept, body.content, body.after_observation_id, body.actor, body.note)
    db.commit()
    db.expire_all()
    return views.action_out(_action(db, action_id))


def _upsert(db: Session, obs: Observation, body: ObservationActionIn) -> None:
    a = db.scalar(
        select(Action).where(Action.before_observation_id == obs.id).order_by(Action.created_at.desc()).limit(1)
    )
    if a is None:
        if body.state == "none":
            return
        _create(db, obs, body.state, body.dept, body.content, body.actor)
    else:
        _update(db, a, body.state, body.dept, body.content, actor=body.actor, note=body.content)


@router.post("/observations/{obs_id}/action", response_model=ObservationOut, tags=["observations"])
def set_observation_action(obs_id: str, body: ObservationActionIn, request: Request, db: Session = Depends(get_db)):
    """관측 목록·상세에서 처리 상태를 바꾼다. 이 관측을 '조치 전'으로 하는 조치가 없으면 만든다"""
    _upsert(db, _obs(db, obs_id), body)
    db.commit()
    db.expire_all()
    obs = db.scalar(views.obs_query().where(Observation.id == obs_id))
    return views.observations_out(db, [obs], file_url_for(request))[0]


@router.post("/observations/bulk-action", response_model=list[ObservationOut], tags=["observations"])
def bulk_action(body: BulkActionIn, request: Request, db: Session = Depends(get_db)):
    ids = list(dict.fromkeys(body.observation_ids))
    for i in ids:
        _upsert(db, _obs(db, i), body)
    db.commit()
    db.expire_all()
    rows = db.scalars(views.obs_query().where(Observation.id.in_(ids))).all()
    by_id = {o.id: o for o in rows}
    return views.observations_out(db, [by_id[i] for i in ids], file_url_for(request))

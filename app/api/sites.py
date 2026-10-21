"""구간 후보 제안·확인(4.2) · 구간 관리 · 구간 관측 시간순 · F6 전후 비교"""

import math

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import get_settings
from ..db import get_db
from ..db.models import Action, Observation, Site
from ..services import views
from ..services.nodelink import nearest_links
from .deps import file_url_for
from .schemas import ActionOut, CompareOut, ObservationOut, SiteIn, SiteOut, SitePatch

router = APIRouter(prefix="/sites", tags=["sites"])


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(a))


def site_candidates(db: Session, lat: float, lng: float, radius_m: float | None = None) -> dict:
    """
    검색 반경 안의 기존 구간 후보. 반경 안이라는 이유만으로 같은 구간으로 확정하지 않고 사용자가 고른다.
    GPS는 촬영자 위치라 장애물 위치·도로 방향과 다를 수 있다.
    """
    radius = radius_m if radius_m is not None else get_settings().site_search_radius_m
    out = []
    for s in db.scalars(select(Site)):
        d = haversine_m(lat, lng, s.lat, s.lng)
        if d <= radius:
            last = db.scalar(
                select(Observation).where(Observation.site_id == s.id).order_by(Observation.captured_at.desc()).limit(1)
            )
            out.append(
                {
                    "site": SiteOut.model_validate(s).model_dump(mode="json"),
                    "distance_m": round(d, 1),
                    "latest_captured_at": views.local(last.captured_at).isoformat() if last else None,
                    "latest_original_key": last.original_key if last else None,
                }
            )
    out.sort(key=lambda x: x["distance_m"])
    links = [{"link_id": ln.id, "road": ln.road, "distance_m": round(d, 1)} for ln, d in nearest_links(lat, lng, k=3)]
    return {"radius_m": radius, "sites": out, "nearby_links": links}


@router.get("/candidates")
def candidates(request: Request, lat: float, lng: float, radius_m: float | None = None, db: Session = Depends(get_db)):
    res = site_candidates(db, lat, lng, radius_m)
    url = file_url_for(request)
    for c in res["sites"]:
        key = c.pop("latest_original_key")
        c["latest_thumbnail"] = url(key) if key else None
    return res


@router.get("", response_model=list[SiteOut])
def list_sites(db: Session = Depends(get_db)):
    return db.scalars(select(Site).order_by(Site.created_at)).all()


@router.post("", response_model=SiteOut, status_code=201)
def create_site(body: SiteIn, db: Session = Depends(get_db)):
    data = body.model_dump()
    if not data["link_id"]:
        data["link_id"] = next((ln.id for ln, _ in nearest_links(body.lat, body.lng, k=1)), None)
    site = Site(**data)
    db.add(site)
    db.commit()
    return site


def _site(db: Session, site_id: str) -> Site:
    site = db.get(Site, site_id)
    if site is None:
        raise HTTPException(404, "구간을 찾을 수 없습니다")
    return site


@router.get("/{site_id}", response_model=SiteOut)
def get_site(site_id: str, db: Session = Depends(get_db)):
    return _site(db, site_id)


@router.patch("/{site_id}", response_model=SiteOut)
def update_site(site_id: str, body: SitePatch, db: Session = Depends(get_db)):
    site = _site(db, site_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(site, k, v)
    db.commit()
    return site


@router.get("/{site_id}/observations", response_model=list[ObservationOut])
def site_observations(site_id: str, request: Request, db: Session = Depends(get_db)):
    """같은 구간의 관측을 촬영시각 순으로 (전후 비교에서 두 관측을 고르는 화면용)"""
    _site(db, site_id)
    rows = db.scalars(views.obs_query().where(Observation.site_id == site_id).order_by(Observation.captured_at)).all()
    return views.observations_out(db, list(rows), file_url_for(request))


@router.get("/{site_id}/actions", response_model=list[ActionOut])
def site_actions(site_id: str, db: Session = Depends(get_db)):
    _site(db, site_id)
    rows = db.scalars(
        select(Action).where(Action.site_id == site_id).options(selectinload(Action.logs)).order_by(Action.created_at)
    )
    return [views.action_out(a) for a in rows]


@router.get("/{site_id}/compare", response_model=CompareOut)
def compare(
    site_id: str,
    request: Request,
    before: str = Query(..., description="조치 전 관측 ID"),
    after: str = Query(..., description="조치 후 관측 ID"),
    db: Session = Depends(get_db),
):
    """
    F6 전후 비교. 사용자가 고른 같은 구간의 두 관측과 관련 조치를 돌려준다.
    두 분석의 모델·규칙 버전이 같을 때만 지표 변화를 계산한다. 다르면 사진·상태 비교로 한정한다.
    촬영 위치·방향이 같은지는 자동으로 확인하지 못하므로 사진으로 확인해야 한다.
    위험도 변화는 실제 사고 감소가 아니다.
    """
    _site(db, site_id)
    obs = {o.id: o for o in db.scalars(views.obs_query().where(Observation.id.in_([before, after])))}
    if before not in obs or after not in obs:
        raise HTTPException(404, "관측을 찾을 수 없습니다")
    if any(o.site_id != site_id for o in obs.values()):
        raise HTTPException(422, "두 관측이 이 구간의 관측이 아닙니다")
    b, a = obs[before], obs[after]
    if views.utc(b.captured_at) > views.utc(a.captured_at):
        raise HTTPException(422, "조치 전 관측의 촬영시각이 조치 후보다 늦습니다")

    url = file_url_for(request)
    bo, ao = views.observations_out(db, [b, a], url)
    acts = db.scalars(
        select(Action)
        .where(Action.site_id == site_id, Action.before_observation_id == before)
        .options(selectinload(Action.logs))
    ).all()

    ba, aa = views.latest_completed(b), views.latest_completed(a)
    if ba is None or aa is None:
        comparable, note = False, "완료된 분석이 없는 관측이 있어 사진·상태만 비교합니다"
    elif (ba.model_version, ba.rule_version) != (aa.model_version, aa.rule_version):
        comparable, note = False, "모델·규칙 버전이 달라 지표를 직접 비교하지 않습니다. 같은 기준으로 재분석하세요"
    else:
        comparable, note = True, "같은 모델·규칙 기준. 촬영 위치·방향이 같은지는 사진으로 확인하세요"
    delta = None
    if comparable:
        delta = {
            "passability": [ba.passability, aa.passability],
            "risk_score": None if ba.risk_score is None or aa.risk_score is None else aa.risk_score - ba.risk_score,
            "bottleneck_ratio": None
            if ba.bottleneck_ratio is None or aa.bottleneck_ratio is None
            else round(aa.bottleneck_ratio - ba.bottleneck_ratio, 3),
        }
    return CompareOut(
        site_id=site_id,
        before=bo,
        after=ao,
        actions=[views.action_out(x) for x in acts],
        comparable=comparable,
        note=note,
        delta=delta,
    )

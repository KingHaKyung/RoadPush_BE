"""F3 최신 관측 위험지도. 지도 조회 때 AI를 다시 돌리지 않는다."""

from collections import defaultdict

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..db.models import Observation
from ..services import views
from .deps import file_url_for
from .schemas import Location, MapItem

router = APIRouter(tags=["map"])


@router.get("/map", response_model=list[MapItem])
def risk_map(
    request: Request,
    south: float | None = Query(None),
    west: float | None = Query(None),
    north: float | None = Query(None),
    east: float | None = Query(None),
    db: Session = Depends(get_db),
):
    """
    구간별 최신 촬영 관측. 최신이 대기·처리중·실패면 이전 완료 관측을 reference로 따로 준다.
    최신이 판단 유보면 그대로 유보로 보여주고 이전 정상 판정으로 대체하지 않는다.
    """
    by_site: dict[str, list[Observation]] = defaultdict(list)
    for o in db.scalars(views.obs_query()):
        by_site[o.site_id].append(o)

    url = file_url_for(request)
    items: list[MapItem] = []
    for obs in by_site.values():
        site = obs[0].site
        if None not in (south, west, north, east) and not (south <= site.lat <= north and west <= site.lng <= east):
            continue
        latest, ref = views.latest_and_reference(obs)
        outs = views.observations_out(db, [latest] + ([ref] if ref else []), url)
        lo = outs[0]
        shown = lo if lo.processing_status == "completed" else (outs[1] if ref else None)
        eff, src = None, None
        if shown is not None:
            eff, src = (shown.human_passability, "human") if shown.human_passability else (shown.passability, "ai")
        items.append(
            MapItem(
                site_id=site.id,
                site_name=site.name,
                location=Location(latitude=site.lat, longitude=site.lng),
                latest=lo,
                reference=outs[1] if ref else None,
                effective_passability=eff,
                effective_source=src,
            )
        )
    items.sort(key=lambda m: m.latest.captured_at, reverse=True)
    return items

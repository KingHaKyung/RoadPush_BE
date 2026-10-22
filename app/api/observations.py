"""
F1 관측 등록·구간 확인 · F2 분석 상태 · F4 상세 · F5 확인·수정·취소 · F7 검수 표 · 메모
"""

from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from sqlalchemy import update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..db.models import Analysis, Observation, ObservationNote, Review, Site
from ..services import views
from ..services.exif import read_exif
from ..services.nodelink import nearest_links
from ..services.storage import get_storage
from .deps import file_url_for
from .schemas import (
    AnalysisOut,
    InspectOut,
    NoteIn,
    Passability,
    ProcessingStatus,
    ReanalyzeIn,
    ReviewCancelIn,
    ReviewIn,
    ReviewStatus,
)
from .schemas import ObservationOut as Out
from .sites import site_candidates

router = APIRouter(prefix="/observations", tags=["observations"])

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".avi", ".mkv"}


def load(db: Session, obs_id: str) -> Observation:
    obs = db.scalar(views.obs_query().where(Observation.id == obs_id))
    if obs is None:
        raise HTTPException(404, "관측을 찾을 수 없습니다")
    return obs


def one(db: Session, obs_id: str, request: Request) -> Out:
    db.expire_all()
    return views.observations_out(db, [load(db, obs_id)], file_url_for(request))[0]


def _media_type(filename: str | None) -> tuple[str, str]:
    ext = Path(filename or "").suffix.lower()
    if ext in IMAGE_EXT:
        return "image", ext
    if ext in VIDEO_EXT:
        return "video", ext
    raise HTTPException(415, f"지원하지 않는 형식입니다: {ext or '확장자 없음'}")


@router.post("/inspect", response_model=InspectOut)
async def inspect(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """
    등록 전 단계: 사진 EXIF에서 촬영시각·GPS를 읽고, 위치가 있으면 주변 구간 후보를 함께 돌려준다.
    값이 없으면 프론트에서 사용자가 보완한다 (원본 전송 중 메타데이터가 지워질 수 있음).
    """
    _media_type(file.filename)
    meta = read_exif(await file.read(), get_settings().display_utc_offset_hours)
    cands = site_candidates(db, meta["lat"], meta["lng"]) if meta["lat"] is not None else None
    return InspectOut(**meta, candidates=cands)


@router.post("", response_model=Out, status_code=202)
async def create_observation(
    request: Request,
    file: UploadFile = File(..., description="사진(JPG/PNG/WebP). 영상(MP4 등)은 선택 기능"),
    captured_at: datetime = Form(..., description="촬영시각 ISO 8601, 시간대 포함"),
    captured_at_source: str = Form("manual", description="exif | manual | device"),
    lat: float | None = Form(None),
    lng: float | None = Form(None),
    location_source: str | None = Form(None, description="exif | manual | device"),
    site_id: str | None = Form(None, description="후보에서 사용자가 확인한 기존 구간"),
    new_site_name: str | None = Form(None, description="일치하는 후보가 없을 때 신규 구간 이름"),
    new_site_description: str | None = Form(None),
    source: str = Form("upload", description="upload | cctv | car | drone"),
    device: str | None = Form(None),
    area: str | None = Form(None),
    addr: str | None = Form(None),
    mask: UploadFile | None = File(None, description="개발용: 미리 만든 통합 라벨 PNG(값 0~7)"),
    db: Session = Depends(get_db),
):
    media, ext = _media_type(file.filename)
    if captured_at.tzinfo is None:
        raise HTTPException(422, "captured_at에 시간대를 넣어 주세요 (예: 2026-10-27T14:32:00+09:00)")
    if (lat is None) != (lng is None):
        raise HTTPException(422, "lat과 lng는 함께 보내 주세요")

    # 구간 확정: 기존 구간 연결 또는 신규 등록 (가까운 좌표만으로 자동 확정하지 않음)
    if site_id:
        site = db.get(Site, site_id)
        if site is None:
            raise HTTPException(404, "구간을 찾을 수 없습니다")
    elif new_site_name:
        if lat is None:
            raise HTTPException(422, "신규 구간은 위치가 필요합니다. 지도에서 위치를 지정해 주세요")
        link = next((ln.id for ln, _ in nearest_links(lat, lng, k=1)), None)
        site = Site(name=new_site_name, description=new_site_description, lat=lat, lng=lng, link_id=link)
        db.add(site)
        db.flush()
    else:
        raise HTTPException(422, "구간을 확인해 주세요: site_id(기존 구간) 또는 new_site_name(신규 구간)")

    data = await file.read()
    if media == "image" and cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) is None:
        raise HTTPException(422, "이미지를 읽을 수 없습니다 (손상 또는 지원하지 않는 형식)")

    obs = Observation(
        site_id=site.id,
        source=source,
        device=device,
        lat=lat,
        lng=lng,
        location_source=location_source if lat is not None else None,
        captured_at=captured_at.astimezone(UTC),  # DB는 UTC (SQLite는 시간대를 버린다)
        captured_at_source=captured_at_source,
        area=area,
        addr=addr,
        media_type=media,
        original_key="",
    )
    db.add(obs)
    db.flush()
    st = get_storage()
    obs.original_key = st.put_bytes(f"observations/{obs.id}/original{ext}", data)
    if mask is not None:
        st.put_bytes(f"observations/{obs.id}/mask.png", await mask.read())
    db.add(Analysis(observation_id=obs.id, trigger="initial"))
    db.commit()
    return one(db, obs.id, request)


@router.get("", response_model=list[Out])
def list_observations(
    request: Request,
    site_id: str | None = None,
    processing_status: ProcessingStatus | None = None,
    passability: Passability | None = Query(None, description="AI 판정"),
    review_status: ReviewStatus | None = None,
    source: str | None = None,
    since: datetime | None = Query(None, description="촬영시각 이후"),
    until: datetime | None = Query(None, description="촬영시각 이전"),
    limit: int = 500,
    db: Session = Depends(get_db),
):
    """관측 검수 표·목록. 촬영시각 최신순"""
    q = views.obs_query()
    if site_id:
        q = q.where(Observation.site_id == site_id)
    if source:
        q = q.where(Observation.source == source)
    if since:
        q = q.where(Observation.captured_at >= since.astimezone(UTC))
    if until:
        q = q.where(Observation.captured_at <= until.astimezone(UTC))
    rows = db.scalars(q.order_by(Observation.captured_at.desc()).limit(min(limit, 2000))).all()
    out = views.observations_out(db, list(rows), file_url_for(request))
    if processing_status:
        out = [o for o in out if o.processing_status == processing_status]
    if passability:
        out = [o for o in out if o.passability == passability]
    if review_status:
        out = [o for o in out if o.review_status == review_status]
    return out


@router.get("/{obs_id}", response_model=Out)
def get_observation(obs_id: str, request: Request, db: Session = Depends(get_db)):
    return one(db, obs_id, request)


@router.get("/{obs_id}/analyses", response_model=list[AnalysisOut])
def list_analyses(obs_id: str, db: Session = Depends(get_db)):
    """분석 이력. 재분석해도 이전 결과를 지우지 않는다"""
    return load(db, obs_id).analyses


@router.post("/{obs_id}/reanalyze", response_model=Out, status_code=202)
def reanalyze(obs_id: str, request: Request, body: ReanalyzeIn | None = None, db: Session = Depends(get_db)):
    """사용자의 새 재분석 요청 → 새 분석 행 (기존 결과·검수 보존, 사람 판단은 승계하지 않음)"""
    obs = load(db, obs_id)
    last = views.latest_analysis(obs)
    if last and last.status in ("pending", "processing"):
        raise HTTPException(409, "이미 분석 대기 중입니다")
    db.add(Analysis(observation_id=obs.id, trigger="reanalysis", reanalysis_note=body.note if body else None))
    db.commit()
    return one(db, obs_id, request)


@router.post("/{obs_id}/reanalyze/cancel", response_model=Out)
def cancel_reanalyze(obs_id: str, request: Request, db: Session = Depends(get_db)):
    """시작 전인 재분석 요청 취소 → 이전 분석 결과·검수로 돌아간다. 처음 분석과 이미 시작한 분석은 취소하지 않는다"""
    obs = load(db, obs_id)
    last = views.latest_analysis(obs)
    if last is None or last.trigger != "reanalysis" or last.status not in ("pending", "processing"):
        raise HTTPException(409, "취소할 재분석 요청이 없습니다")
    # 워커가 그사이 가져갔을 수 있으니 pending일 때만 바꾼다
    done = db.execute(
        update(Analysis)
        .where(Analysis.id == last.id, Analysis.status == "pending")
        .values(status="cancelled", finished_at=datetime.now(UTC))
    )
    db.commit()
    if done.rowcount == 0:
        raise HTTPException(409, "이미 분석이 시작돼 취소할 수 없습니다")
    return one(db, obs_id, request)


@router.post("/{obs_id}/reviews", response_model=Out)
def add_review(obs_id: str, body: ReviewIn, request: Request, db: Session = Depends(get_db)):
    """관리자 확인(의견) 또는 수정(사람 판정 + 이유). AI 결과는 그대로 둔다"""
    obs = load(db, obs_id)
    if body.analysis_id:
        ana = next((a for a in obs.analyses if a.id == body.analysis_id), None)
        if ana is None:
            raise HTTPException(404, "이 관측의 분석이 아닙니다")
    else:
        ana = views.latest_completed(obs)
    if ana is None or ana.status != "completed":
        raise HTTPException(409, "완료된 분석이 없어 검수할 수 없습니다")
    if body.status == "corrected" and body.human_passability is None:
        raise HTTPException(422, "수정할 때는 human_passability가 필요합니다")
    db.add(
        Review(
            analysis_id=ana.id,
            status=body.status,
            human_passability=body.human_passability if body.status == "corrected" else None,
            comment=body.comment,
            reviewer=body.reviewer,
        )
    )
    db.commit()
    return one(db, obs_id, request)


@router.post("/{obs_id}/reviews/cancel", response_model=Out)
def cancel_review(obs_id: str, request: Request, body: ReviewCancelIn | None = None, db: Session = Depends(get_db)):
    """확인·수정 취소 → 미검수. 이력은 지우지 않고 취소 행을 덧붙인다"""
    body = body or ReviewCancelIn()
    obs = load(db, obs_id)
    if body.analysis_id:
        ana = next((a for a in obs.analyses if a.id == body.analysis_id), None)
        if ana is None:
            raise HTTPException(404, "이 관측의 분석이 아닙니다")
    else:
        ana = views.latest_completed(obs)
    if ana is None or not ana.reviews or ana.reviews[-1].status == "cancelled":
        raise HTTPException(409, "취소할 검수가 없습니다")
    db.add(Review(analysis_id=ana.id, status="cancelled", comment=body.comment, reviewer=body.reviewer))
    db.commit()
    return one(db, obs_id, request)


@router.post("/{obs_id}/notes", response_model=Out, status_code=201)
def add_note(obs_id: str, body: NoteIn, request: Request, db: Session = Depends(get_db)):
    """메모 (예: 운영사 수거 요청 접수번호). 처리 상태·검수는 바꾸지 않고 이력(history)에 남는다"""
    load(db, obs_id)
    content = body.content.strip()
    if not content:
        raise HTTPException(422, "메모 내용이 비어 있습니다")
    db.add(ObservationNote(observation_id=obs_id, content=content, author=body.author))
    db.commit()
    return one(db, obs_id, request)

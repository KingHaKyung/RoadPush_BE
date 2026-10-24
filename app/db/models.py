"""
DB 테이블 (마스터 문서 5장 관계 구조).

구간(site)
 ├─ 관측(observation) A ─ 원본 사진
 │    └─ 분석(analysis) A1, A2 … ─ mask·overlay      재분석은 새 행, 이전 결과 보존
 │         └─ 관리자 확인(review)                       분석에 붙음 → 새 분석에 자동 승계되지 않음
 │    └─ 메모(note)                                      처리 상태와 별개로 남기는 기록
 ├─ 조치(action) ─ 전 관측 A / 후 관측 B 연결
 └─ 관측(observation) B ─ 조치 후 사진

- 최신성은 업로드·분석 시각이 아니라 촬영시각(captured_at) 기준. 시각은 UTC로 저장한다.
- 원본·mask·overlay는 파일 저장소, DB에는 저장 키만 둔다.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from . import Base


def _id(prefix: str):
    return lambda: f"{prefix}_{uuid.uuid4().hex[:12]}"


# PostgreSQL(Supabase)에서는 JSONB, SQLite에서는 JSON
Json = JSON().with_variant(JSONB(), "postgresql")


PASS_CODES = "'passable', 'narrowed', 'blocked', 'undetermined'"


def now() -> datetime:
    return datetime.now(UTC)


class Site(Base):
    """구간. 같은 장소의 관측·조치·전후 비교의 기준. 관측 등록 때 사용자가 확인해 확정한다"""

    __tablename__ = "sites"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id("site"))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    lat: Mapped[float] = mapped_column(Float)  # 지도 대표 위치
    lng: Mapped[float] = mapped_column(Float)
    # 참고: 국토교통부 표준노드링크 LINK_ID (프론트 지도 링크와 같은 키). 구간 확정을 대신하지 않는다
    link_id: Mapped[str | None] = mapped_column(String(20), index=True)
    # 선택: 현장 실측 보도 폭(m). 있으면 상대폭 × 실측폭을 추정 절대폭으로 덧붙인다 (판정에는 안 씀)
    sidewalk_width_m: Mapped[float | None] = mapped_column(Float)
    # 보도 폭 출처: field(현장 실측·사용자 입력) | ref_ngii(성남) | ref_building_no | ref_route_approx (참조 레이어)
    sidewalk_width_source: Mapped[str | None] = mapped_column(String(24))
    # 참고: 서울 보행 링크 ref_walk_link.link_id (도보 네트워크 LNKG_ID. 표준노드링크 link_id와 다른 키)
    ref_walk_link_id: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Observation(Base):
    """관측 1건 = 사진 1장(또는 영상 1개) + 촬영 위치·시각 + 확정 구간"""

    __tablename__ = "observations"
    __table_args__ = (
        Index("ix_observations_site_captured", "site_id", "captured_at"),  # 지도: 구간별 최신 촬영 관측
        CheckConstraint("media_type IN ('image', 'video')", name="ck_observations_media_type"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id("obs"))
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), index=True)
    source: Mapped[str] = mapped_column(String(16))  # upload | cctv | car | drone
    device: Mapped[str | None] = mapped_column(String(64))
    # 촬영 좌표. 없으면 저장은 하되 지도 표시를 보류한다
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    location_source: Mapped[str | None] = mapped_column(String(16))  # exif | manual | device
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    captured_at_source: Mapped[str] = mapped_column(String(16), default="manual")  # exif | manual | device
    area: Mapped[str | None] = mapped_column(String(100))
    addr: Mapped[str | None] = mapped_column(String(200))
    media_type: Mapped[str] = mapped_column(String(8))  # image | video
    original_key: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    site: Mapped[Site] = relationship()
    analyses: Mapped[list["Analysis"]] = relationship(back_populates="observation", order_by="Analysis.created_at")
    notes: Mapped[list["ObservationNote"]] = relationship(order_by="ObservationNote.created_at")


class Analysis(Base):
    """AI 분석 1회. status=pending 행이 작업 큐다 (별도 브로커 없이 DB 폴링). cancelled는 시작 전에 취소한 재분석"""

    __tablename__ = "analyses"
    __table_args__ = (
        CheckConstraint("trigger IN ('initial', 'reanalysis')", name="ck_analyses_trigger"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed', 'cancelled')", name="ck_analyses_status"
        ),
        CheckConstraint(f"passability IS NULL OR passability IN ({PASS_CODES})", name="ck_analyses_passability"),
        CheckConstraint("risk_score IS NULL OR risk_score BETWEEN 0 AND 100", name="ck_analyses_risk_score"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id("ana"))
    observation_id: Mapped[str] = mapped_column(ForeignKey("observations.id"), index=True)
    # initial: 등록 직후 / reanalysis: 사용자의 새 재분석 요청 (같은 작업의 자동 재시도와 구분)
    trigger: Mapped[str] = mapped_column(String(16), default="initial")
    reanalysis_note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), default="pending", index=True
    )  # pending|processing|completed|failed|cancelled
    attempts: Mapped[int] = mapped_column(Integer, default=0)  # 자동 재시도 포함 실행 횟수
    error: Mapped[str | None] = mapped_column(Text)
    error_kind: Mapped[str | None] = mapped_column(String(16))  # input(재시도 안 함) | runtime | exhausted

    model_version: Mapped[str | None] = mapped_column(String(200))
    rule_version: Mapped[str | None] = mapped_column(String(60))

    passability: Mapped[str | None] = mapped_column(String(16))  # passable|narrowed|blocked|undetermined
    risk_score: Mapped[int | None] = mapped_column(Integer)
    occupancy: Mapped[int | None] = mapped_column(Integer)  # 병목 단면 점유율(%)
    bottleneck_ratio: Mapped[float | None] = mapped_column(Float)  # 상대 유효폭
    bottleneck_m_est: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    uncertainty_code: Mapped[str | None] = mapped_column(String(20))
    uncertainty_reason: Mapped[str | None] = mapped_column(Text)
    causes: Mapped[list] = mapped_column(Json, default=list)
    evidence: Mapped[dict] = mapped_column(Json, default=dict)
    files: Mapped[dict] = mapped_column(Json, default=dict)  # {"surface_overlay": key, ...}

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    observation: Mapped[Observation] = relationship(back_populates="analyses")
    reviews: Mapped[list["Review"]] = relationship(order_by="Review.created_at")


class Review(Base):
    """
    관리자 확인·수정. 특정 분석에 붙고, AI 결과를 덮어쓰지 않는다 (이력 누적, 최신 행이 현재 상태).
    취소는 행을 지우지 않고 cancelled 행을 덧붙인다 → 최신 행이 cancelled면 미검수
    """

    __tablename__ = "reviews"
    __table_args__ = (
        CheckConstraint("status IN ('confirmed', 'corrected', 'cancelled')", name="ck_reviews_status"),
        CheckConstraint(
            f"human_passability IS NULL OR human_passability IN ({PASS_CODES})", name="ck_reviews_human_passability"
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id("rev"))
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id"), index=True)
    status: Mapped[str] = mapped_column(String(16))  # confirmed | corrected | cancelled
    human_passability: Mapped[str | None] = mapped_column(String(16))
    comment: Mapped[str | None] = mapped_column(Text)  # 확인 의견 또는 수정 이유
    reviewer: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Action(Base):
    """현장조치. 구간 단위이며 조치 전 관측과(재촬영 후) 조치 후 관측을 연결한다"""

    __tablename__ = "actions"
    __table_args__ = (
        CheckConstraint("state IN ('none', 'inspection_requested', 'in_progress', 'done')", name="ck_actions_state"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id("act"))
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), index=True)
    before_observation_id: Mapped[str] = mapped_column(ForeignKey("observations.id"), index=True)
    after_observation_id: Mapped[str | None] = mapped_column(ForeignKey("observations.id"))
    state: Mapped[str] = mapped_column(String(24))  # inspection_requested | in_progress | done
    dept: Mapped[str | None] = mapped_column(String(64))
    content: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    logs: Mapped[list["ActionLog"]] = relationship(order_by="ActionLog.created_at")


class ActionLog(Base):
    """조치 상태·내용 변경 이력"""

    __tablename__ = "action_logs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id("alog"))
    action_id: Mapped[str] = mapped_column(ForeignKey("actions.id"), index=True)
    state: Mapped[str] = mapped_column(String(24))
    dept: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ObservationNote(Base):
    """관측 메모 (예: 운영사 수거 요청 접수번호). 처리 상태·검수와 별개로 이력에만 남는다"""

    __tablename__ = "observation_notes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id("note"))
    observation_id: Mapped[str] = mapped_column(ForeignKey("observations.id"), index=True)
    content: Mapped[str] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

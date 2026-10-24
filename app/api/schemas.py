"""
API 형식. ObservationOut의 앞부분은 마스터 문서 4.4 '공통 분석 응답 형식'과 같은 이름·코드를 쓰고,
뒤에 화면에 필요한 값을 덧붙인다. 처리 상태·AI 판정·검수 상태는 하나로 합치지 않는다.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ProcessingStatus = Literal["pending", "processing", "completed", "failed"]
Passability = Literal["passable", "narrowed", "blocked", "undetermined"]
ReviewStatus = Literal["pending", "confirmed", "corrected"]
ActionState = Literal["none", "inspection_requested", "in_progress", "done"]

ACTION_LABEL = {
    "none": "미처리",
    "inspection_requested": "현장점검 요청",
    "in_progress": "조치 중",
    "done": "조치 완료",
}
PASS_LABEL = {"passable": "통과 가능", "narrowed": "좁아짐", "blocked": "단절/우회 필요", "undetermined": "판단 유보"}


class Location(BaseModel):
    latitude: float
    longitude: float


class Images(BaseModel):
    original: str
    surface_overlay: str | None = None
    obstacle_overlay: str | None = None


class ReviewOut(BaseModel):
    id: str
    analysis_id: str
    status: Literal["confirmed", "corrected"]
    human_passability: Passability | None
    comment: str | None
    reviewer: str | None
    created_at: datetime


class ActionBrief(BaseModel):
    action_id: str | None
    state: ActionState
    label: str
    dept: str | None


class HistoryItem(BaseModel):
    t: datetime
    txt: str
    ai: bool = False
    by: str | None = None


class ObservationOut(BaseModel):
    # ── 마스터 문서 4.4 공통 분석 응답 ──
    observation_id: str
    analysis_id: str | None
    site_id: str
    location: Location | None
    captured_at: datetime
    processing_status: ProcessingStatus
    passability: Passability | None
    risk_score: int | None
    uncertainty_reason: str | None
    images: Images
    review_status: ReviewStatus
    human_passability: Passability | None
    # ── 추가 정보 ──
    site_name: str
    link_id: str | None = None
    source: str
    device: str | None = None
    capture_location: Location | None = Field(None, description="사진 촬영 좌표 (location은 구간 대표 위치)")
    location_source: str | None = Field(None, description="capture_location의 출처: exif | manual | device")
    area: str | None = None
    addr: str | None = None
    occupancy: int | None = Field(None, description="병목 단면 점유율(%)")
    bottleneck_ratio: float | None = Field(None, description="상대 유효폭 = 남은 연속 통로 / 추정 전체 보행폭")
    bottleneck_m_est: float | None = Field(None, description="상대폭 × 구간 실측 보도폭 (실측이 있을 때만)")
    risk_complete: bool = Field(False, description="false면 길이 항 측정 불가 → 폭 항만 반영한 부분 상대 지표")
    causes: list[str] = []
    uncertainty_code: str | None = None
    confidence: float | None = None
    reasons: list[str] = []
    analysis_trigger: str | None = None
    attempts: int = 0
    failure_reason: str | None = None
    review: ReviewOut | None = None
    action: ActionBrief
    history: list[HistoryItem] = []
    model_version: str | None = None
    rule_version: str | None = None


class AnalysisOut(BaseModel):
    id: str
    trigger: str
    reanalysis_note: str | None
    status: str
    attempts: int
    error: str | None
    error_kind: str | None
    model_version: str | None
    rule_version: str | None
    passability: str | None
    risk_score: int | None
    occupancy: int | None
    bottleneck_ratio: float | None
    bottleneck_m_est: float | None
    confidence: float | None
    uncertainty_code: str | None
    uncertainty_reason: str | None
    causes: list
    evidence: dict
    files: dict
    created_at: datetime
    finished_at: datetime | None

    model_config = {"from_attributes": True, "protected_namespaces": ()}


class MapItem(BaseModel):
    """구간별 최신 촬영 관측. 최신이 대기·처리중·실패면 이전 완료 결과를 reference로 따로 준다"""

    site_id: str
    site_name: str
    location: Location
    latest: ObservationOut
    reference: ObservationOut | None = None
    effective_passability: Passability | None = Field(
        None, description="지도 색: 최신 완료 관측의 관리자 판정 > AI 판정. 출처는 effective_source"
    )
    effective_source: Literal["human", "ai"] | None = None


class InspectOut(BaseModel):
    captured_at: datetime | None
    lat: float | None
    lng: float | None
    candidates: dict | None


class ReviewIn(BaseModel):
    status: Literal["confirmed", "corrected"]
    human_passability: Passability | None = None
    comment: str | None = Field(None, description="확인 의견 또는 수정 이유")
    reviewer: str | None = None
    analysis_id: str | None = Field(None, description="비우면 최신 완료 분석")


class ReviewCancelIn(BaseModel):
    comment: str | None = Field(None, description="취소 이유")
    reviewer: str | None = None
    analysis_id: str | None = Field(None, description="비우면 최신 완료 분석")


class NoteIn(BaseModel):
    content: str = Field(..., min_length=1, description="메모 내용 (처리 상태는 바꾸지 않음)")
    author: str | None = None


class ReanalyzeIn(BaseModel):
    note: str | None = Field(None, description="입력·조건을 무엇을 보완했는지")


class ActionIn(BaseModel):
    before_observation_id: str
    state: ActionState = "inspection_requested"
    dept: str | None = None
    content: str | None = None
    actor: str | None = None


class ActionPatch(BaseModel):
    state: ActionState | None = None
    dept: str | None = None
    content: str | None = None
    after_observation_id: str | None = None
    actor: str | None = None
    note: str | None = None


class ObservationActionIn(BaseModel):
    """관측 목록에서 처리 상태를 바꿀 때 (조치가 없으면 만들고 있으면 갱신)"""

    state: ActionState
    dept: str | None = None
    content: str | None = None
    actor: str | None = None


class BulkActionIn(ObservationActionIn):
    observation_ids: list[str]


class ActionLogOut(BaseModel):
    state: str
    dept: str | None
    note: str | None
    actor: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ActionOut(BaseModel):
    id: str
    site_id: str
    before_observation_id: str
    after_observation_id: str | None
    state: ActionState
    label: str
    dept: str | None
    content: str | None
    created_at: datetime
    updated_at: datetime
    logs: list[ActionLogOut]


class SiteIn(BaseModel):
    name: str
    lat: float
    lng: float
    description: str | None = None
    link_id: str | None = None
    sidewalk_width_m: float | None = None


class SitePatch(BaseModel):
    name: str | None = None
    description: str | None = None
    lat: float | None = None
    lng: float | None = None
    link_id: str | None = None
    sidewalk_width_m: float | None = None


class SiteOut(BaseModel):
    id: str
    name: str
    description: str | None
    lat: float
    lng: float
    link_id: str | None
    sidewalk_width_m: float | None
    sidewalk_width_source: str | None = None  # field | ref_ngii | ref_building_no | ref_route_approx
    ref_walk_link_id: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class CompareOut(BaseModel):
    site_id: str
    before: ObservationOut
    after: ObservationOut
    actions: list[ActionOut]
    comparable: bool
    note: str
    delta: dict | None = None

"""
장애물 원인 (무엇이 막았나 → 처리 유형 A~D). RoadPush_AI 통합 응답의 surface_obstacles를 소비만 한다.
  모델·임계값·처리 유형  RoadPush_AI configs/obstacle_detection.yaml (클래스별 conf, imgsz, 가중치 SHA-256)
  접점 규칙              roadpush_ai/attribution.py (phase7b_occupancy_overlap_v2)
                         최종 7-B 폭 계산에 쓴 점유 덩어리와 겹치는 탐지 박스만 원인으로 인정
                         (덩어리의 30%가 박스 안 또는 박스의 10%가 덩어리 위). 길을 막지 않는 탐지는 무시
규칙·임계값을 바꿀 때는 AI 저장소만 고친다 (백엔드에 같은 규칙을 따로 두지 않는다).

원인 배지 이름은 labels.CAUSE_OF(vehicle → 차량 …) = 프론트 처리 경로 키.
Ultralytics(YOLO11)는 AGPL-3.0 → 공개 서비스 전 라이선스 확인 (AI 문서 15장).
"""

from dataclasses import dataclass, field

from .labels import cause_of


@dataclass
class CauseResult:
    causes: list[str]  # 원인 배지 (겹친 면적 큰 순)
    # AI 상태: attributed | partially_attributed | unclassified_only | no_blocker | no_selected_surface
    #          | no_measurement_occupancy (선택 surface는 있지만 7-B 점유를 못 만듦 → 원인 없음)
    status: str
    types: list[str] = field(default_factory=list)  # 처리 유형 A~D
    # 민원 자동 전달 가능: 원인이 있고 7-B 결과가 authoritative일 때만 (AI가 계산)
    auto_routable: bool = False
    components: list[dict] = field(default_factory=list)
    obstacles: list[dict] = field(default_factory=list)  # 선택 surface에 접점한 탐지 (box_analysis_xyxy 포함)
    policy: str = ""


def badges(components: list[dict]) -> list[str]:
    """7-B 점유 덩어리별 원인 → 원인 배지 (겹친 면적 큰 순)"""
    overlap: dict[str, int] = {}
    for comp in components:
        for c in comp["causes"]:
            badge = cause_of(c["class"])
            if badge:
                overlap[badge] = overlap.get(badge, 0) + int(c["overlap_px"])
    causes = sorted(overlap, key=lambda k: -overlap[k])
    if not causes and components:
        causes = ["장애물"]  # 길을 막은 덩어리는 있지만 모델 팀 클래스에 없는 물체 (쓰레기봉투·볼라드 등)
    return causes


def from_response(surface_obstacles: dict) -> CauseResult:
    comps = list(surface_obstacles.get("components") or [])
    return CauseResult(
        causes=badges(comps),
        status=str(surface_obstacles["status"]),
        types=list(surface_obstacles.get("types") or []),
        auto_routable=bool(surface_obstacles.get("auto_routable")),
        components=comps,
        obstacles=list(surface_obstacles.get("obstacles") or []),
        policy=str(surface_obstacles.get("policy_id") or ""),
    )

"""
원인 배지와 dummy 엔진용 통합 라벨.
- CAUSE_OF: 장애물 모델(RoadPush_AI configs/obstacle_detection.yaml)의 클래스 → 원인 배지 (프론트 처리 경로의 키)
- L: dummy 엔진(passability/rules.py)의 라벨. roadpush_ai 엔진은 AI 팀의 Mapillary 라벨을 그대로 쓴다

장애물 = 모델 팀 시각 클래스 4개 ('시각 클래스(후보)', 눈으로 구분되는 것만)
  vehicle 승용차·트럭·승합차·오토바이 → 차량
  construction 바리케이드 → 공사시설
  debris 입간판·리어카·의자·탁자 → 적치물
  micromobility 자전거·킥보드 → PM
- 상시 고정시설(가로수·가로등·전봇대·고정 볼라드·벤치 등)과 사람·동물은 장애물이 아니다
- 임시 방호벽·펜스·콘은 공사시설로 포함
- 사진에 차량이 있다고 '불법 주정차', 자전거·PM이 있다고 '방치'로 확정하지 않는다 → 원인은 물체 이름만 쓴다
"""

from enum import IntEnum


class L(IntEnum):
    UNKNOWN = 0  # 모델이 확신하지 못함·미분류
    SIDEWALK = 1  # 보행 가능 노면 (보도 재질 병합, 점자블록)
    ROAD = 2  # 차도 (횡단보도 포함)
    CURB = 3  # 연석 = 보도와 차도의 구조 경계 (모델이 낼 때만)
    OBSTACLE = 4  # 보도 점유 후보 (차량·공사시설·적치물·자전거·PM)
    TRANSIENT = 5  # 보행자 등 이동체. 점유가 아니라 지면을 가리는 것으로 본다
    BACKGROUND = 6  # 건물·식생·하늘·상시 고정시설 등 보행면이 아닌 것
    BIKE_LANE = 7  # 자전거도로. 보도와 구별이 어려우면 판단 유보 사유


# 원인 배지 이름 = 프론트 처리 경로(src/lib/constants.ts ROUTES)의 키. 시각 클래스 하나에 배지 하나
# micromobility는 자전거·킥보드를 한 클래스로 내므로 프론트에 이미 있는 PM 경로(운영사 수거, 교통행정과)로 보낸다
CAUSES = ("차량", "공사시설", "적치물", "PM")

# 장애물 모델이 내는 클래스 이름(소문자·한글) → 원인 배지
CAUSE_OF: dict[str, str] = {
    # ── 모델 팀 시각 클래스 ──
    "vehicle": "차량",
    "construction": "공사시설",
    "debris": "적치물",
    "micromobility": "PM",
    # ── 공개 모델(COCO·YOLOE 텍스트 카테고리·Vistas)이나 세부 이름으로 낼 때: 위 4개 중 하나로 합친다 ──
    **dict.fromkeys(
        ("car", "truck", "bus", "van", "caravan", "trailer", "other vehicle", "motorcycle", "motorbike"), "차량"
    ),
    **dict.fromkeys(("barricade", "barrier", "temporary barrier", "construction fence", "traffic cone"), "공사시설"),
    **dict.fromkeys(
        (
            "signboard",
            "sandwich board",
            "a-board",
            "cart",
            "handcart",
            "chair",
            "table",
            "box",
            "cardboard box",
            "trash",
            "garbage",
            "trash bag",
            "garbage bag",
        ),
        "적치물",
    ),  # fmt: skip
    **dict.fromkeys(("bicycle", "kickboard", "kick scooter", "electric scooter", "scooter", "wheeled slow"), "PM"),
    # ── 한글 클래스 이름으로 낼 때 ──
    **dict.fromkeys(("차량", "승용차", "트럭", "승합차", "오토바이"), "차량"),
    **dict.fromkeys(("공사시설", "바리케이드"), "공사시설"),
    **dict.fromkeys(("적치물", "입간판", "리어카", "의자", "탁자"), "적치물"),
    **dict.fromkeys(("자전거", "킥보드", "pm"), "PM"),
}
assert set(CAUSE_OF.values()) <= set(CAUSES)

# 사람·동물: 장애물이 아니라 지면을 가리는 이동체 (기존 합의대로 장애물에서 제외)
PERSON_NAMES = {
    "person", "pedestrian", "rider", "bicyclist", "motorcyclist", "other rider",
    "animal", "dog", "cat", "bird", "ground animal", "사람", "동물",
}  # fmt: skip


def cause_of(name: str) -> str | None:
    return CAUSE_OF.get(name.strip().lower())

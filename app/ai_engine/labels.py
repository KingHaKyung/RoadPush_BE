"""
RoadPush 통합 라벨. 노면 모델·장애물 모델의 결과를 이 라벨로 바꾼 뒤 통과 판정 규칙에 넣는다.
모델을 바꿔도(SelectStar DeepLab v3+, Mask2Former, YOLO11n-seg, YOLOE, 모델 팀 모델 …) 규칙은 그대로다.

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
    UNKNOWN = 0  # 모델이 확신하지 못함·미분류 (DeepLab caution_zone 포함)
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

# 노면·장면 모델 클래스 이름(소문자) → 통합 라벨 (Mapillary Vistas · Cityscapes 공통)
NAME_TO_L: dict[str, L] = {
    "sidewalk": L.SIDEWALK,
    "pedestrian area": L.SIDEWALK,
    "curb cut": L.SIDEWALK,
    "road": L.ROAD,
    "service lane": L.ROAD,
    "parking": L.ROAD,
    "crosswalk - plain": L.ROAD,
    "lane marking - crosswalk": L.ROAD,
    "lane marking - general": L.ROAD,
    "rail track": L.ROAD,
    "manhole": L.UNKNOWN,
    "catch basin": L.UNKNOWN,
    "pothole": L.UNKNOWN,
    "curb": L.CURB,
    "bike lane": L.BIKE_LANE,
    **{k: L.TRANSIENT for k in PERSON_NAMES},
    **{k: L.OBSTACLE for k in CAUSE_OF},
    # 상시 고정시설 → 장애물이 아님
    "pole": L.BACKGROUND,
    "utility pole": L.BACKGROUND,
    "street light": L.BACKGROUND,
    "traffic light": L.BACKGROUND,
    "traffic sign": L.BACKGROUND,
    "traffic sign frame": L.BACKGROUND,
    "bench": L.BACKGROUND,
    "bike rack": L.BACKGROUND,
    "fire hydrant": L.BACKGROUND,
    "mailbox": L.BACKGROUND,
    "junction box": L.BACKGROUND,
    "phone booth": L.BACKGROUND,
    "cctv camera": L.BACKGROUND,
    "guard rail": L.BACKGROUND,
    "fence": L.BACKGROUND,
    "wall": L.BACKGROUND,
    "building": L.BACKGROUND,
    "bridge": L.BACKGROUND,
    "tunnel": L.BACKGROUND,
    "vegetation": L.BACKGROUND,
    "terrain": L.BACKGROUND,
    "sky": L.BACKGROUND,
    "mountain": L.BACKGROUND,
    "sand": L.BACKGROUND,
    "snow": L.BACKGROUND,
    "water": L.BACKGROUND,
    "train": L.BACKGROUND,
    "on rails": L.BACKGROUND,
    "ego vehicle": L.BACKGROUND,
    "car mount": L.BACKGROUND,
}

# SelectStar DeepLab v3+ (AI Hub 인도보행 Surface 7클래스). 마스터 문서 8장:
# class1 = 아스팔트·우레탄 보도와 실제 자전거도로가 섞인 클래스 → 정상 보행면으로 전역 확정하지 않는다 (BIKE_LANE)
# 보도 후보 = class4 + class6. 클래스 순서는 모델 팀이 저자 코드와 대조해 확인할 것
SELECTSTAR_TO_L: list[L] = [
    L.BACKGROUND,  # 0 background
    L.BIKE_LANE,  # 1 bike_lane (아스팔트·우레탄 보도 포함)
    L.UNKNOWN,  # 2 caution_zone
    L.ROAD,  # 3 crosswalk
    L.SIDEWALK,  # 4 braille_guide_blocks
    L.ROAD,  # 5 roadway
    L.SIDEWALK,  # 6 sidewalk
]


def name_label(name: str) -> L:
    return NAME_TO_L.get(name.strip().lower(), L.UNKNOWN)


def cause_of(name: str) -> str | None:
    return CAUSE_OF.get(name.strip().lower())

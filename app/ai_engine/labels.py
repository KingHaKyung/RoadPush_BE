"""
RoadPush 통합 라벨. 노면 모델·장애물 모델의 결과를 이 라벨로 바꾼 뒤 통과 판정 규칙에 넣는다.
모델을 바꿔도(SelectStar DeepLab v3+, Mask2Former, YOLO11n-seg, YOLOE …) 규칙은 그대로다.

마스터 문서 V4 기준
- 주요 장애물: 주정차 차량 후보 · 공사시설 · 적치물 · 자전거 · PM
- 상시 고정시설(가로수·가로등·전봇대·고정 난간·고정 볼라드·벤치 등)은 장애물이 아니다 → BACKGROUND
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


# 원인 배지: 점유 픽셀에 겹친 물체 이름 → 원인
CAUSE_OF: dict[str, str] = {
    # 차량 (COCO·Vistas·Cityscapes)
    "car": "차량",
    "truck": "차량",
    "bus": "차량",
    "motorcycle": "차량",
    "caravan": "차량",
    "trailer": "차량",
    "other vehicle": "차량",
    "van": "차량",
    # 자전거·PM
    "bicycle": "자전거",
    "wheeled slow": "PM",
    "kick scooter": "PM",
    "electric scooter": "PM",
    "scooter": "PM",
    # 공사시설 (임시)
    "traffic cone": "공사시설",
    "barricade": "공사시설",
    "barrier": "공사시설",
    "temporary barrier": "공사시설",
    "construction fence": "공사시설",
    # 적치물
    "box": "적치물",
    "cardboard box": "적치물",
    "trash bag": "적치물",
    "garbage bag": "적치물",
    "trash can": "적치물",
    "signboard": "적치물",
    "sandwich board": "적치물",
    "banner": "적치물",
    "billboard": "적치물",
}

PERSON_NAMES = {"person", "pedestrian", "rider", "bicyclist", "motorcyclist", "other rider"}

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

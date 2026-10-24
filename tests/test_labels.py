"""장애물 클래스 → 원인 배지 매핑이 모델 팀 클래스·프론트 처리 경로와 맞는지"""

import numpy as np

from app.ai_engine.labels import CAUSE_OF, CAUSES, PERSON_NAMES, L, cause_of, name_label
from app.ai_engine.scene import causes_from, compose
from app.ai_engine.types import Instance, SurfaceResult

# 프론트 src/lib/constants.ts ROUTES의 키 (2026-10-23 main). 프론트에서 바뀌면 여기와 labels.CAUSES를 같이 고친다
FRONT_ROUTE_KEYS = {"차량", "이륜차", "PM", "자전거", "적치물", "입간판", "공사시설", "쓰레기"}


def test_model_team_visual_classes_map_to_front_causes():
    assert cause_of("vehicle") == "차량"
    assert cause_of("construction") == "공사시설"
    assert cause_of("debris") == "적치물"
    assert cause_of("micromobility") == "PM"
    # 시각 클래스 예시는 그 클래스로 합친다 (오토바이 ∈ vehicle, 입간판 ∈ debris, 자전거 ∈ micromobility)
    assert cause_of("오토바이") == cause_of("motorcycle") == "차량"
    assert cause_of("입간판") == cause_of("signboard") == "적치물"
    assert cause_of("자전거") == cause_of("bicycle") == "PM"
    assert cause_of("Vehicle ") == "차량"  # 대소문자·공백 무시


def test_every_cause_has_a_front_route():
    assert set(CAUSES) <= FRONT_ROUTE_KEYS
    assert set(CAUSE_OF.values()) == set(CAUSES)


def test_people_animals_and_fixed_facilities_are_not_obstacles():
    for n in ("person", "dog", "동물"):
        assert n in PERSON_NAMES and cause_of(n) is None
    for n in ("pole", "bench", "street light", "vegetation"):
        assert name_label(n) == L.BACKGROUND and cause_of(n) is None


def test_causes_from_model_instances():
    cm = np.full((10, 10), L.SIDEWALK, np.uint8)
    m1 = np.zeros((10, 10), bool)
    m1[5:9, 0:4] = True
    m2 = np.zeros((10, 10), bool)
    m2[5:9, 6:8] = True
    scene = compose(SurfaceResult(cm), [Instance("vehicle", m1, 0.9), Instance("signboard", m2, 0.8)], {})
    assert causes_from(scene, scene.class_map == L.OBSTACLE) == ["차량", "적치물"]
    assert set(CAUSE_OF) and all(k == k.lower() for k in CAUSE_OF)

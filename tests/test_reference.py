"""참조 레이어(ref_walk_link) 연결: 테이블이 있으면 구간 보도 폭을 채우고, 없으면 아무 영향 없음"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import engine
from app.main import app
from app.services import reference

API = "/api/v1"
# 팀원 데이터의 실제 행 (종로구 관수동 수표로: building_no 3.0 m / 세종로 청와대로: route_approx 4.0 m)
ROWS = [
    ("192365", "종로구", "관수동", "수표로", 8.36, True, False, False, False, 3.0, "building_no", 3.0, "L0", "P0",
     37.568776, 126.990173, "LINESTRING(126.990175 37.568738,126.990172 37.568814)"),
    ("51304", "종로구", "세종로", "청와대로", 18.29, True, False, False, False, 4.0, "route_approx", 4.0, "L0", "P0",
     37.583802, 126.97655, "LINESTRING(126.976549 37.583884,126.976552 37.583719)"),
    ("900001", "종로구", "관수동", None, 12.0, True, True, False, False, None, None, None, None, None,
     37.568776, 126.990200, "LINESTRING(126.990200 37.568700,126.990200 37.568850)"),
]  # fmt: skip


@pytest.fixture
def ref_table():
    cols = reference.COLUMNS
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE ref_walk_link (link_id VARCHAR(20) PRIMARY KEY, sgg_nm TEXT, emd_nm TEXT, road_name TEXT,"
                " length_m REAL, is_shared_with_car BOOLEAN, is_crosswalk BOOLEAN, is_underpass BOOLEAN,"
                " is_overpass BOOLEAN, sidewalk_width_m REAL,"
                " sidewalk_width_source TEXT, effective_width_m REAL, grade_mobility TEXT, grade_pedestrian TEXT,"
                " center_lat REAL, center_lng REAL, geom_wkt TEXT)"
            )
        )
        names = [c.strip() for c in cols.split(",")]
        conn.execute(
            text(f"INSERT INTO ref_walk_link ({cols}) VALUES ({', '.join(':' + n for n in names)})"),
            [dict(zip(names, r, strict=True)) for r in ROWS],
        )
    reference._has.clear()
    yield
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE ref_walk_link"))
    reference._has.clear()


def test_distance_to_linestring():
    wkt = "LINESTRING(127.0 37.0,127.001 37.0)"
    assert reference.distance_m(37.0001, 127.0005, wkt, (37.0, 127.0005)) == pytest.approx(11.1, abs=0.2)
    assert reference.distance_m(
        37.0, 127.0015, f"MULTI{wkt.replace('(', '((').replace(')', '))')}", (37.0, 127.0005)
    ) == (pytest.approx(44.4, abs=0.5))


def test_no_reference_table_is_noop():
    with TestClient(app) as c:
        reference._has.clear()
        site = c.post(f"{API}/sites", json={"name": "참조 없음", "lat": 37.568776, "lng": 126.990173}).json()
        assert site["sidewalk_width_m"] is None and site["ref_walk_link_id"] is None
        cands = c.get(f"{API}/sites/candidates", params={"lat": 37.568776, "lng": 126.990173}).json()
        assert cands["reference_walk_link"] is None


def test_site_width_from_building_no_only(ref_table):
    with TestClient(app) as c:
        near = c.post(f"{API}/sites", json={"name": "수표로", "lat": 37.56878, "lng": 126.99016}).json()
        assert near["ref_walk_link_id"] == "192365"  # 횡단보도 링크(900001)는 가까워도 뺀다
        assert near["sidewalk_width_m"] == 3.0 and near["sidewalk_width_source"] == "ref_building_no"

        approx = c.post(f"{API}/sites", json={"name": "청와대로", "lat": 37.5838, "lng": 126.97656}).json()
        assert approx["ref_walk_link_id"] == "51304"
        assert approx["sidewalk_width_m"] is None  # route_approx는 기본으로 채우지 않는다

        field = c.post(
            f"{API}/sites", json={"name": "실측", "lat": 37.56878, "lng": 126.99016, "sidewalk_width_m": 2.2}
        )
        assert field.json()["sidewalk_width_m"] == 2.2 and field.json()["sidewalk_width_source"] == "field"

        far = c.post(f"{API}/sites", json={"name": "성남", "lat": 37.386851, "lng": 127.127334}).json()
        assert far["ref_walk_link_id"] is None and far["sidewalk_width_m"] is None

        cands = c.get(f"{API}/sites/candidates", params={"lat": 37.56878, "lng": 126.99016}).json()
        ref = cands["reference_walk_link"]
        assert ref["link_id"] == "192365" and ref["road_name"] == "수표로" and ref["distance_m"] < 5

        patched = c.patch(f"{API}/sites/{near['id']}", json={"sidewalk_width_m": 2.8}).json()
        assert patched["sidewalk_width_source"] == "field"

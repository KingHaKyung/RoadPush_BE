"""참조 레이어 v2 연결: 테이블이 있으면 링크 매칭·구간 보도 폭을 채우고, 없으면 아무 영향 없음"""

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import engine
from app.main import app
from app.services import nodelink, reference

API = "/api/v1"
_spec = importlib.util.spec_from_file_location(
    "load_reference", Path(__file__).parents[1] / "scripts/load_reference.py"
)
loader = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(loader)

WALK = "link_id,sgg_nm,emd_nm,road_name,length_m,is_shared_with_car,is_crosswalk,is_overpass,is_underpass,is_bridge,is_indoor_candidate,sidewalk_width_m,sidewalk_width_source,center_lat,center_lng,geom_wkt"  # noqa: E501
# 팀원 데이터 v2의 실제 행 (종로구 관수동 수표로: building_no 3.0 m / 세종로 청와대로: route_approx 4.0 m)
WALK_ROWS = [
    ("192365", "종로구", "관수동", "수표로", "8.36", "True", "False", "False", "False", "False", "False", "3.0",
     "building_no", "37.568776", "126.990173", "LINESTRING(126.990175 37.568738,126.990172 37.568814)"),
    ("51304", "종로구", "세종로", "청와대로", "18.29", "True", "False", "False", "False", "False", "False", "4.0",
     "route_approx", "37.583802", "126.97655", "LINESTRING(126.976549 37.583884,126.976552 37.583719)"),
    ("900001", "종로구", "관수동", "", "12.0", "True", "True", "False", "False", "False", "False", "", "",
     "37.568776", "126.990200", "LINESTRING(126.990200 37.568700,126.990200 37.568850)"),
]  # fmt: skip
SN = "link_id,sgg_nm,f_node,t_node,road_name,road_rank,is_urban_expressway,length_m,lanes_one_dir,lanes_total,road_width_m,road_width_source,has_median,is_oneway,has_sidewalk,sidewalk_left,sidewalk_left_width_m,sidewalk_right,sidewalk_right_width_m,sidewalk_width_m,sidewalk_width_source,is_backstreet,grade_mobility,grade_pedestrian,grade_basis,unjudged_reason,center_lat,center_lng,geom_wkt"  # noqa: E501
# 성남 v2 실제 행 (분당구 성남대로: 보도 4.0 m / 수정구 신촌남로: 보도 없는 이면도로)
SN_ROWS = [
    ("2060113500", "분당구", "2060035801", "2060038601", "성남대로", "107", "False", "413.8", "3", "6.0", "22.9",
     "ngii", "True", "False", "True", "False", "", "True", "4.0", "4.0", "ngii_sidewalk_1to5000", "False", "L0", "P0",
     "sidewalk_width", "", "37.38620068873715", "127.12684072085843",
     "LINESTRING(127.125295 37.384808,127.125753 37.385108,127.126335 37.385610,127.126655 37.385981,"
     "127.126841 37.386201,127.127060 37.386432,127.127401 37.386929,127.127771 37.387644,127.127861 37.387858)"),
    ("2040391501", "수정구", "2049194300", "2049194500", "신촌남로", "107", "False", "75.85", "1", "2.0", "8.0",
     "ngii", "False", "False", "False", "False", "", "False", "", "", "", "True", "", "", "", "backstreet_8m_plus",
     "37.45862006321406", "127.10478304784834",
     "LINESTRING(127.104187 37.458464,127.104638 37.458567,127.104783 37.458620,127.104980 37.458712)"),
]  # fmt: skip


def _reset():
    reference._has.clear()
    nodelink.links.cache_clear()


@pytest.fixture
def ref_table():
    """팀원 스키마(scripts/reference/ref_schema_v2.sql)로 만들고 적재 스크립트로 넣는다"""
    loader.create_tables(engine)
    loader.load(engine, "ref_walk_link", [dict(zip(WALK.split(","), r, strict=True)) for r in WALK_ROWS])
    loader.load(engine, "ref_seongnam_link", [dict(zip(SN.split(","), r, strict=True)) for r in SN_ROWS])
    _reset()
    yield
    with engine.begin() as conn:
        for t in loader.TABLES:
            conn.execute(text(f"DROP TABLE {t}"))
    _reset()


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

        cands = c.get(f"{API}/sites/candidates", params={"lat": 37.56878, "lng": 126.99016}).json()
        ref = cands["reference_walk_link"]
        assert ref["link_id"] == "192365" and ref["road_name"] == "수표로" and ref["distance_m"] < 5

        patched = c.patch(f"{API}/sites/{near['id']}", json={"sidewalk_width_m": 2.8}).json()
        assert patched["sidewalk_width_source"] == "field"


def test_seongnam_link_matches_whole_city_and_fills_ngii_width(ref_table):
    with TestClient(app) as c:
        # 예시 GeoJSON(분당구 일부)에 없는 수정구 링크도 ref_seongnam_link로 매칭된다
        back = c.post(f"{API}/sites", json={"name": "신촌남로", "lat": 37.45862, "lng": 127.10478}).json()
        assert back["link_id"] == "2040391501" and back["sidewalk_width_m"] is None  # 보도 없는 이면도로

        main = c.post(f"{API}/sites", json={"name": "성남대로", "lat": 37.386851, "lng": 127.127334}).json()
        assert main["link_id"] == "2060113500" and main["ref_walk_link_id"] is None
        assert main["sidewalk_width_m"] == 4.0 and main["sidewalk_width_source"] == "ref_ngii"

        cands = c.get(f"{API}/sites/candidates", params={"lat": 37.45862, "lng": 127.10478}).json()
        ref = cands["reference_link"]
        assert ref["link_id"] == "2040391501" and ref["is_backstreet"] is True and ref["has_sidewalk"] is False
        assert cands["reference_walk_link"] is None

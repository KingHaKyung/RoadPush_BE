"""
참조 레이어 v2 (팀원 제공, scripts/load_reference.py로 넣는다. Alembic 밖 테이블)
테이블이 없으면(아직 안 넣은 DB) 모든 함수가 None을 돌려주고 나머지 기능은 그대로 돈다.

- ref_seongnam_link: 성남시 전체 표준노드링크. link_id가 sites.link_id와 같은 키
  보도폭은 연속수치지형도 인도 폭(0.5m 단위 대표값, 가로수·볼라드 미차감). 출처 ref_ngii
- ref_walk_link: 서울 보행 링크. link_id는 서울시 도보 네트워크 LNKG_ID라 sites.ref_walk_link_id에 따로 둔다
  보도 폭 출처: building_no(건물번호로 구간 매칭) · route_approx(노선 단위 근사)
- 보도 폭은 참고값이다. 판정은 상대폭으로 하고, 폭은 '상대폭 × 보도 폭' 추정 절대폭을 덧붙일 때만 쓴다
- 도로폭은 실폭(road_width_rw_m, 도로명주소 실폭도로 면)이 있으면 그것을, 없으면 대표 도로폭(road_width_m)을
  구간에 채운다. 위험도 '차도 우회' 항에만 쓴다. 실폭 컬럼은 v2 원본에 없어 load_reference.py가 더한다
- 등급(grade_*)은 참고용이라 판정에 쓰지 않는다
"""

import logging
import math
import re

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import Site

log = logging.getLogger("roadpush.reference")

COLUMNS = (
    "link_id, sgg_nm, emd_nm, road_name, length_m, is_shared_with_car, is_crosswalk, is_underpass, is_overpass, "
    "is_indoor_candidate, sidewalk_width_m, sidewalk_width_source, grade_mobility, grade_pedestrian, "
    "center_lat, center_lng, geom_wkt, lanes, road_width_m, road_width_source, road_width_rw_m"
)
SEONGNAM_COLUMNS = (
    "link_id, sgg_nm, road_name, road_rank, length_m, lanes_total, road_width_m, road_width_source, "
    "road_width_rw_m, has_sidewalk, sidewalk_left_width_m, sidewalk_right_width_m, sidewalk_width_m, "
    "sidewalk_width_source, is_backstreet, grade_mobility, grade_pedestrian, unjudged_reason, center_lat, center_lng"
)
_has: dict[tuple[str, str], bool] = {}
_cols: dict[tuple[str, str], set[str]] = {}


def has_reference(db: Session, table: str = "ref_walk_link") -> bool:
    key = (str(db.get_bind().url), table)
    if key not in _has:
        _has[key] = inspect(db.get_bind()).has_table(table)
    return _has[key]


def _select(db: Session, table: str, columns: str) -> str:
    """columns 중 그 DB 테이블에 실제 있는 것만 (실폭 컬럼을 아직 더하지 않은 DB도 동작하게)"""
    key = (str(db.get_bind().url), table)
    if key not in _cols:
        _cols[key] = {c["name"] for c in inspect(db.get_bind()).get_columns(table)}
    return ", ".join(c for c in (x.strip() for x in columns.split(",")) if c in _cols[key])


def seongnam_link(db: Session, link_id: str | None) -> dict | None:
    """표준노드링크 LINK_ID로 성남 참조 링크 (도로폭·보도 유무·보도폭·이면도로)"""
    if not link_id or not has_reference(db, "ref_seongnam_link"):
        return None
    cols = _select(db, "ref_seongnam_link", SEONGNAM_COLUMNS)
    sql = text(f"SELECT {cols} FROM ref_seongnam_link WHERE link_id = :id")
    row = db.execute(sql, {"id": link_id}).mappings().first()
    if row is None:
        return None
    # SQLite는 boolean을 1/0으로 돌려준다 → PostgreSQL과 같게
    return {k: bool(v) if k in ("has_sidewalk", "is_backstreet") and v is not None else v for k, v in row.items()}


def _lines(wkt: str | None) -> list[list[tuple[float, float]]]:
    """LINESTRING·MULTILINESTRING WKT → [[(lng, lat), ...], ...]"""
    parts = re.findall(r"\(([^()]+)\)", wkt or "")
    return [[tuple(map(float, p.split()[:2])) for p in part.split(",")] for part in parts]


def distance_m(lat: float, lng: float, wkt: str | None, center: tuple[float, float]) -> float:
    """점과 링크 선형의 최단거리(m). 링크가 짧아 지역 평면 근사로 충분하다. 선형이 없으면 중심점 거리"""
    ky, kx = 111_320.0, 111_320.0 * math.cos(math.radians(lat))
    best = math.hypot((center[1] - lng) * kx, (center[0] - lat) * ky)
    for line in _lines(wkt):
        pts = [((x - lng) * kx, (y - lat) * ky) for x, y in line]
        for (x1, y1), (x2, y2) in zip(pts, pts[1:], strict=False):
            dx, dy = x2 - x1, y2 - y1
            t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, -(x1 * dx + y1 * dy) / (dx * dx + dy * dy)))
            best = min(best, math.hypot(x1 + t * dx, y1 + t * dy))
        if len(pts) == 1:
            best = min(best, math.hypot(*pts[0]))
    return best


def nearest_walk_link(db: Session, lat: float, lng: float, max_m: float | None = None) -> dict | None:
    """
    좌표에서 가장 가까운 보행 링크 (max_m 안). 사진은 지상 보도에서 찍으므로
    횡단보도·지하보도·육교와 지하·실내 후보(is_indoor_candidate, v2) 링크는 뺀다
    """
    if not has_reference(db):
        return None
    s = get_settings()
    max_m = s.ref_match_max_m if max_m is None else max_m
    # 링크 중심점 인덱스로 먼저 거르고, 긴 링크는 중심이 멀어도 선이 가까울 수 있어 여유를 둔다
    margin = max_m + s.ref_bbox_margin_m
    dlat, dlng = margin / 111_320, margin / (111_320 * math.cos(math.radians(lat)))
    rows = db.execute(
        text(
            f"SELECT {_select(db, 'ref_walk_link', COLUMNS)} FROM ref_walk_link WHERE center_lat BETWEEN :a AND :b "
            "AND center_lng BETWEEN :c AND :d "
            "AND NOT (is_crosswalk OR is_underpass OR is_overpass OR is_indoor_candidate)"
        ),
        {"a": lat - dlat, "b": lat + dlat, "c": lng - dlng, "d": lng + dlng},
    ).mappings()
    best = None
    for r in rows:
        d = distance_m(lat, lng, r["geom_wkt"], (r["center_lat"], r["center_lng"]))
        if d <= max_m and (best is None or d < best[0]):
            best = (d, r)
    if best is None:
        return None
    out = {k: v for k, v in best[1].items() if k != "geom_wkt"}
    out["distance_m"] = round(best[0], 1)
    return out


def _road_width(ref: dict) -> tuple[float | None, str | None]:
    """실폭 우선, 없으면 대표 도로폭 (출처 ref_rw · ref_ngii · ref_notice)"""
    if ref.get("road_width_rw_m"):
        return float(ref["road_width_rw_m"]), "ref_rw"
    if ref.get("road_width_m"):
        return float(ref["road_width_m"]), f"ref_{ref.get('road_width_source') or 'unknown'}"
    return None, None


def apply_to_site(db: Session, site: Site) -> None:
    """
    구간 등록 때 참조 레이어로 보도 폭·도로폭·차로 수를 채운다. 현장 실측을 넣었으면 그 값을 쓴다 (출처 field).
    성남: site.link_id로 ref_seongnam_link → 보도 ref_ngii. 서울: 20m 안 보행 링크 → 보도 ref_building_no 등.
    조회가 실패해도 구간 등록은 그대로 진행한다.
    """
    if site.sidewalk_width_m is not None:
        site.sidewalk_width_source = site.sidewalk_width_source or "field"
    if site.road_width_m is not None:
        site.road_width_source = site.road_width_source or "field"
    allowed = get_settings().ref_width_sources
    try:
        sn = seongnam_link(db, site.link_id)
        ref = None if sn else nearest_walk_link(db, site.lat, site.lng)
    except Exception:  # 참조 레이어 문제로 구간 등록을 막지 않는다
        log.exception("참조 레이어 조회 실패")
        return
    hit = sn or ref
    if hit is None:
        return
    if site.road_width_m is None:
        site.road_width_m, site.road_width_source = _road_width(hit)
    if site.road_lanes is None:
        lanes = hit.get("lanes_total") if sn else hit.get("lanes")
        site.road_lanes = int(lanes) if lanes else None
    if sn is not None:
        if site.sidewalk_width_m is None and sn["sidewalk_width_m"] and "ngii" in allowed:
            site.sidewalk_width_m = float(sn["sidewalk_width_m"])
            site.sidewalk_width_source = "ref_ngii"
        return
    site.ref_walk_link_id = ref["link_id"]
    src = ref["sidewalk_width_source"]
    if site.sidewalk_width_m is None and ref["sidewalk_width_m"] and src in allowed:
        site.sidewalk_width_m = float(ref["sidewalk_width_m"])
        site.sidewalk_width_source = f"ref_{src}"


def refresh_site(db: Session, site: Site) -> None:
    """참조 데이터를 다시 넣은 뒤(실폭 추가 등) 구간 값을 다시 채운다. 현장 실측(field)은 그대로 둔다"""
    if site.sidewalk_width_source != "field":
        site.sidewalk_width_m = site.sidewalk_width_source = None
    if site.road_width_source != "field":
        site.road_width_m = site.road_width_source = site.road_lanes = None
    apply_to_site(db, site)

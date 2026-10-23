"""
참조 레이어: 팀원이 만든 서울 보행 링크(ref_walk_link).
Alembic 밖에서 만든 테이블이라 docs/DB.md 6장 순서로 따로 넣는다.
테이블이 없으면(로컬 SQLite·아직 안 넣은 DB) 모든 함수가 None을 돌려주고 나머지 기능은 그대로 돈다.

- ref_walk_link.link_id는 서울시 도보 네트워크 LNKG_ID다.
  sites.link_id(표준노드링크 LINK_ID)와 다른 키라 sites.ref_walk_link_id에 따로 둔다
- 보도 폭은 참고값이다. 판정은 상대폭으로 하고, 폭은 '상대폭 × 보도 폭' 추정 절대폭을 덧붙일 때만 쓴다
- 보도 폭 출처: building_no(건물번호로 구간 매칭) · route_approx(노선 단위 근사). 기본은 building_no만 채운다
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
    "sidewalk_width_m, sidewalk_width_source, effective_width_m, grade_mobility, grade_pedestrian, "
    "center_lat, center_lng, geom_wkt"
)
_has: dict[str, bool] = {}


def has_reference(db: Session) -> bool:
    url = str(db.get_bind().url)
    if url not in _has:
        _has[url] = inspect(db.get_bind()).has_table("ref_walk_link")
    return _has[url]


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
    좌표에서 가장 가까운 보행 링크 (max_m 안). 사진은 지상 보도에서 찍으므로 횡단보도·지하보도·육교 링크는 뺀다.
    지하철역 연결통로 중 is_underpass가 false로 들어간 링크는 아직 걸러지지 않는다 (데이터 쪽 보완 요청)
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
            f"SELECT {COLUMNS} FROM ref_walk_link WHERE center_lat BETWEEN :a AND :b "
            "AND center_lng BETWEEN :c AND :d AND NOT (is_crosswalk OR is_underpass OR is_overpass)"
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


def apply_to_site(db: Session, site: Site) -> None:
    """
    구간 등록 때 가까운 보행 링크를 붙이고, 현장 실측 폭이 없으면 참조 보도 폭으로 채운다.
    실측 폭을 넣었으면 그 값을 쓴다 (출처 field). 조회가 실패해도 구간 등록은 그대로 진행한다.
    """
    if site.sidewalk_width_m is not None:
        site.sidewalk_width_source = site.sidewalk_width_source or "field"
    try:
        ref = nearest_walk_link(db, site.lat, site.lng)
    except Exception:  # 참조 레이어 문제로 구간 등록을 막지 않는다
        log.exception("참조 보행 링크 조회 실패")
        return
    if ref is None:
        return
    site.ref_walk_link_id = ref["link_id"]
    src = ref["sidewalk_width_source"]
    if site.sidewalk_width_m is None and ref["sidewalk_width_m"] and src in get_settings().ref_width_sources:
        site.sidewalk_width_m = float(ref["sidewalk_width_m"])
        site.sidewalk_width_source = f"ref_{src}"

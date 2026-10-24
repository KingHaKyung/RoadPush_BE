"""
관측 좌표 → 가장 가까운 표준노드링크 링크. 프론트 지도 구간과 같은 LINK_ID를 쓴다.
DB에 참조 레이어 ref_seongnam_link(성남시 전체 10,680링크)가 있으면 그걸 쓰고(도시고속도로 제외),
없으면 예시 지역 GeoJSON(분당구 일부)을 쓴다. 이 규모는 파이썬으로 충분하다. 전국으로 넓히면 PostGIS로 옮긴다.
"""

import itertools
import json
import math
import re
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import inspect, text

from ..config import get_settings


@dataclass
class Link:
    id: str
    road: str
    rank: str
    lanes: int
    max_speed: int
    coords: list[tuple[float, float]]  # (lng, lat)


@lru_cache
def links() -> list[Link]:
    return _ref_links() or _geojson_links()


def _ref_links() -> list[Link]:
    from ..db import engine

    if not inspect(engine).has_table("ref_seongnam_link"):
        return []
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT link_id, road_name, road_rank, lanes_one_dir, geom_wkt FROM ref_seongnam_link "
                "WHERE NOT is_urban_expressway"
            )
        ).all()
    out = []
    for r in rows:
        pts = re.findall(r"(-?[\d.]+) (-?[\d.]+)", r.geom_wkt or "")
        if len(pts) >= 2:
            out.append(
                Link(
                    r.link_id,
                    r.road_name or "",
                    r.road_rank or "",
                    r.lanes_one_dir or 0,
                    0,
                    [(float(x), float(y)) for x, y in pts],
                )
            )
    return out


def _geojson_links() -> list[Link]:
    path = get_settings().nodelink_path
    if not path.exists():
        return []
    fc = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for f in fc["features"]:
        p = f["properties"]
        if p.get("kind") != "link":
            continue
        out.append(
            Link(
                p["LINK_ID"],
                p.get("ROAD_NAME") or "",
                p.get("ROAD_RANK") or "",
                p.get("LANES") or 0,
                p.get("MAX_SPD") or 0,
                [tuple(c) for c in f["geometry"]["coordinates"]],
            )
        )
    return out


def _dist_m(lat: float, lng: float, a: tuple[float, float], b: tuple[float, float]) -> float:
    kx = 111_320 * math.cos(math.radians(lat))
    ky = 110_540
    px, py = 0.0, 0.0
    ax, ay = (a[0] - lng) * kx, (a[1] - lat) * ky
    bx, by = (b[0] - lng) * kx, (b[1] - lat) * ky
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(ax + t * dx - px, ay + t * dy - py)


def nearest_links(lat: float, lng: float, k: int = 3, max_m: float | None = None) -> list[tuple[Link, float]]:
    max_m = max_m if max_m is not None else get_settings().link_match_max_m
    # 위경도 범위로 먼저 거른다 (성남 전체 1만 링크)
    dlat = max_m / 110_540
    dlng = max_m / (111_320 * math.cos(math.radians(lat)))
    scored = []
    for ln in links():
        xs, ys = [c[0] for c in ln.coords], [c[1] for c in ln.coords]
        if min(xs) - dlng > lng or max(xs) + dlng < lng or min(ys) - dlat > lat or max(ys) + dlat < lat:
            continue
        d = min(_dist_m(lat, lng, a, b) for a, b in itertools.pairwise(ln.coords))
        if d <= max_m:
            scored.append((ln, d))
    return sorted(scored, key=lambda x: x[1])[:k]


def link_by_id(link_id: str | None) -> Link | None:
    return next((ln for ln in links() if ln.id == link_id), None) if link_id else None

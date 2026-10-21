"""
관측 좌표 → 가장 가까운 표준노드링크 링크. 프론트 지도 구간과 같은 LINK_ID를 쓴다.
예시 지역(성남시 분당구, 링크 759개)은 파이썬으로 충분하다. 전국으로 넓히면 PostGIS(ST_DWithin + KNN)로 옮긴다.
"""

import itertools
import json
import math
from dataclasses import dataclass
from functools import lru_cache

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
    scored = []
    for ln in links():
        d = min(_dist_m(lat, lng, a, b) for a, b in itertools.pairwise(ln.coords))
        if d <= max_m:
            scored.append((ln, d))
    return sorted(scored, key=lambda x: x[1])[:k]


def link_by_id(link_id: str | None) -> Link | None:
    return next((ln for ln in links() if ln.id == link_id), None) if link_id else None

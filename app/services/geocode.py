"""
좌표 → 행정구역·주소 (브이월드 역지오코딩 getAddress). 프론트 scripts/geocode-observations.mjs와 같은 형식을 만든다.
- area: 시군구 + 법정동 (예: '분당구 이매동'). 프론트 정렬·검색 기준
- addr: 지번 주소 + 행정동 (예: '경기도 성남시 분당구 이매동 129-1 (행정동 이매1동)')
VWORLD_KEY가 없거나 조회에 실패하면 None (관측 등록·분석은 그대로 진행)
"""

import json
import logging
from functools import lru_cache
from urllib.parse import urlencode
from urllib.request import urlopen

from ..config import get_settings
from ..db.models import Observation

log = logging.getLogger("roadpush.geocode")

URL = "https://api.vworld.kr/req/address"


def _fetch(lat: float, lng: float) -> dict:
    s = get_settings()
    q = urlencode({
        "service": "address", "request": "getAddress", "version": "2.0", "crs": "epsg:4326",
        "point": f"{lng},{lat}", "format": "json", "type": "parcel", "zipcode": "false", "simple": "false",
        "key": s.vworld_key, "domain": s.vworld_domain,
    })  # fmt: skip
    with urlopen(f"{URL}?{q}", timeout=s.geocode_timeout_sec) as res:
        return json.load(res)


def parse(j: dict) -> tuple[str, str] | None:
    r = j.get("response") or {}
    if r.get("status") != "OK" or not r.get("result"):
        return None
    first = r["result"][0]
    st = first["structure"]
    gu = st["level2"].split(" ")[-1]  # '성남시 분당구' → '분당구'
    area = f"{gu} {st['level4L']}".strip()
    addr = first["text"] + (f" (행정동 {st['level4A']})" if st.get("level4A") else "")
    return area, addr


@lru_cache(maxsize=4096)
def reverse(lat: float, lng: float) -> tuple[str, str] | None:
    """같은 좌표(소수 6자리)는 한 번만 조회한다. 실패는 캐시하지 않는다"""
    out = parse(_fetch(lat, lng))
    if out is None:
        raise LookupError(f"역지오코딩 결과 없음 ({lat}, {lng})")
    return out


def fill_address(obs: Observation) -> bool:
    """area·addr가 비어 있으면 촬영 좌표(없으면 구간 대표 위치)로 채운다. 등록 때 넣은 값은 덮어쓰지 않는다"""
    if (obs.area and obs.addr) or not get_settings().vworld_key:
        return False
    lat, lng = (obs.lat, obs.lng) if obs.lat is not None else (obs.site.lat, obs.site.lng)
    try:
        area, addr = reverse(round(lat, 6), round(lng, 6))
    except Exception as e:  # 네트워크·키 오류로 분석을 멈추지 않는다
        log.warning("역지오코딩 실패 %s: %s", obs.id, e)
        return False
    obs.area = obs.area or area
    obs.addr = obs.addr or addr
    return True

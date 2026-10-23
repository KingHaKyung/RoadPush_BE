"""
배포용 외부 지도·CCTV 프록시. 프론트 vite.config.ts 개발 서버 프록시와 같은 경로·동작이다.
개발할 때는 프론트 개발 서버가 처리하고, 배포하면 Vercel rewrites로 이 백엔드에 넘긴다
(docs/FRONTEND_INTEGRATION.md 5장).
키는 백엔드 .env에만 두고 응답에 넣지 않는다 (UTIC 재생 페이지 주소는 UTIC 규격상 키를 포함한다).

- /vworld/wmts/{경로}  : 브이월드 배경지도 타일 (VWORLD_KEY, 인증키에 등록한 서비스 URL을 Referer로)
- /api/utic/player     : UTIC CCTV 재생 페이지로 302. UTIC는 등록 IP 대역에서만 응답하므로 등록 IP 서버에서 돌린다
- /api/its/cctvs       : ITS 고속도로·국도 CCTV 위치 (6시간 캐시)
- /api/its/stream      : 카메라 주변을 다시 조회해 지금의 HLS 주소
"""

import json
import logging
import re
import threading
import time
from http.cookies import SimpleCookie
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from ..config import get_settings

log = logging.getLogger("roadpush.proxy")
router = APIRouter(include_in_schema=False)

UTIC = "https://www.utic.go.kr"
ITS = "https://openapi.its.go.kr:9443/cctvInfo"
KOREA = {"minX": 124, "maxX": 132, "minY": 33, "maxY": 39}


def _http(url: str, headers: dict | None = None, timeout: float = 15) -> tuple[int, dict, bytes]:
    """GET 한 번. (상태, 헤더, 본문). 테스트에서 이 함수를 바꿔 끼운다"""
    try:
        with urlopen(UrlRequest(url, headers=headers or {}), timeout=timeout) as r:
            return (
                r.status,
                {k.lower(): v for k, v in r.headers.items()} | {"set-cookie": r.headers.get_all("Set-Cookie") or []},
                r.read(),
            )
    except HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()


def _need(key: str, name: str) -> str:
    if not key:
        raise HTTPException(503, f"백엔드 .env에 {name}가 없습니다")
    return key


# ── 브이월드 배경지도 ──────────────────────────────────────────────
@router.get("/vworld/wmts/{path:path}")
def vworld_wmts(path: str):
    s = get_settings()
    key = _need(s.vworld_key, "VWORLD_KEY")
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", path) or ".." in path:
        raise HTTPException(400, "타일 경로가 올바르지 않습니다")
    status, h, body = _http(
        f"https://api.vworld.kr/req/wmts/1.0.0/{quote(key)}/{path}", {"Referer": s.vworld_domain.rstrip("/") + "/"}
    )
    out = {"Cache-Control": h.get("cache-control") or "public, max-age=86400"}
    return Response(body, status, headers=out, media_type=h.get("content-type", "image/png"))


# ── UTIC CCTV 재생 ────────────────────────────────────────────────
class _Utic:
    """UTIC는 같은 주소를 1초에 5번 넘게 부르면 IP를 차단한다 → 요청 간격 1.1초, 카메라 정보 캐시"""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.info: dict[str, dict] = {}
        self.cookie, self.cookie_at, self.last = "", 0.0, 0.0

    def _gap(self) -> None:
        d = 1.1 - (time.monotonic() - self.last)
        if d > 0:
            time.sleep(d)
        self.last = time.monotonic()

    def camera(self, key: str, cid: str) -> dict:
        with self.lock:
            if cid in self.info:
                return self.info[cid]
            # 카메라 정보(종류·접속값)는 재생 페이지로 세션을 연 뒤에만 준다
            if not self.cookie or time.monotonic() - self.cookie_at > 600:
                self._gap()
                _, h, _ = _http(f"{UTIC}/jsp/map/openDataCctvStream.jsp?key={quote(key)}&cctvid={cid}")
                jar = SimpleCookie()
                for c in h.get("set-cookie", []):
                    jar.load(c)
                self.cookie = "; ".join(f"{k}={m.value}" for k, m in jar.items())
                self.cookie_at = time.monotonic()
            self._gap()
            _, _, body = _http(
                f"{UTIC}/map/getCctvInfoById.do?cctvId={cid}",
                {"Cookie": self.cookie, "Referer": f"{UTIC}/jsp/map/openDataCctvStream.jsp"},
            )
            try:
                j = json.loads(body)
            except ValueError:
                j = {}
            if not j.get("CCTVID"):
                self.cookie = ""
                raise RuntimeError(j.get("msg") or "UTIC에서 카메라 정보를 받지 못했습니다")
            self.info[cid] = j
            return j


utic = _Utic()


@router.get("/api/utic/player")
def utic_player(cctvid: str = ""):
    if not re.fullmatch(r"[A-Za-z0-9]{3,16}", cctvid):
        return HTMLResponse("cctvid가 올바르지 않습니다", 400)
    try:
        key = get_settings().utic_api_key
        if not key:
            raise RuntimeError("백엔드 .env에 UTIC_API_KEY가 없습니다")
        c = utic.camera(key, cctvid)
    except Exception as e:
        log.warning("UTIC 실패 %s: %s", cctvid, e)
        msg = str(e).replace("<", "&lt;")
        return HTMLResponse(
            f'<p style="font-family:sans-serif;padding:16px">영상을 불러오지 못했습니다: {msg}</p>', 502
        )

    def v(x) -> str:
        return "undefined" if x is None or x == "" else str(x)

    q = urlencode({
        "key": key, "cctvid": cctvid, "cctvName": quote(c.get("CCTVNAME") or ""), "kind": v(c.get("KIND")),
        "cctvip": v(c.get("CCTVIP")), "cctvch": v(c.get("CH")), "id": v(c.get("ID")),
        "cctvpasswd": v(c.get("PASSWD")), "cctvport": v(c.get("PORT")),
    })  # fmt: skip
    return RedirectResponse(f"{UTIC}/jsp/map/openDataCctvStream.jsp?{q}", 302)


# ── ITS 국가교통정보센터 CCTV ─────────────────────────────────────
def _its_query(key: str, type_: str, box: dict) -> list[dict]:
    q = urlencode({"apiKey": key, "type": type_, "cctvType": "4", "getType": "json", **box})
    _, _, body = _http(f"{ITS}?{q}", timeout=60)
    d = (json.loads(body).get("response") or {}).get("data")
    return d if isinstance(d, list) else [d] if d else []  # 결과가 1개면 객체 하나로 온다


_its_cache: dict = {"at": 0.0, "body": None}
_its_lock = threading.Lock()


@router.get("/api/its/cctvs")
def its_cctvs():
    try:
        key = get_settings().its_api_key
        if not key:
            raise RuntimeError("백엔드 .env에 ITS_API_KEY가 없습니다")
        with _its_lock:  # 전국 응답은 약 4MB·30초라 한 번만 받아 6시간 쓴다
            if _its_cache["body"] is None or time.time() - _its_cache["at"] > 6 * 3600:
                cctvs = []
                for type_ in ("ex", "its"):
                    for d in _its_query(key, type_, KOREA):
                        name = str(d["cctvname"])
                        m = re.match(r"^\[([^\]]+)\]", name)
                        lat, lng = float(d["coordy"]), float(d["coordx"])
                        cctvs.append({
                            "id": f"its-{type_}-{lat:.5f},{lng:.5f}", "name": re.sub(r"^\[[^\]]+\]\s*", "", name),
                            "center": m.group(1) if m else "", "road": "고속도로" if type_ == "ex" else "국도",
                            "lat": lat, "lng": lng, "src": "its", "its": {"type": type_, "name": name},
                        })  # fmt: skip
                if not cctvs:
                    raise RuntimeError("ITS에서 CCTV 목록을 받지 못했습니다")
                _its_cache.update(at=time.time(), body={"source": "ITS 국가교통정보센터", "cctvs": cctvs})
            return _its_cache["body"]
    except Exception as e:
        log.warning("ITS 목록 실패: %s", e)
        return JSONResponse({"error": str(e), "cctvs": []}, 502)


@router.get("/api/its/stream")
def its_stream(type: str = "", name: str = "", lat: float | None = None, lng: float | None = None):
    if type not in ("ex", "its") or lat is None or lng is None:
        return JSONResponse({"error": "요청 값이 올바르지 않습니다"}, 400)
    try:
        key = get_settings().its_api_key
        if not key:
            raise RuntimeError("백엔드 .env에 ITS_API_KEY가 없습니다")
        d = 0.003
        rows = _its_query(key, type, {"minX": lng - d, "maxX": lng + d, "minY": lat - d, "maxY": lat + d})

        def dist(c: dict) -> float:
            return (float(c["coordy"]) - lat) ** 2 + (float(c["coordx"]) - lng) ** 2

        same = sorted((c for c in rows if c["cctvname"] == name), key=dist)
        hit = same[0] if same else min(rows, key=dist, default=None)
        if hit is None:
            raise RuntimeError("카메라를 찾지 못했습니다")
        return {"hls": hit["cctvurl"]}
    except Exception as e:
        return JSONResponse({"error": str(e)}, 502)

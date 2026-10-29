"""
RoadPush 백엔드 진입점.
  uvicorn app.main:app --reload
프론트 .env: VITE_API_BASE_URL=http://localhost:8000/api/v1
"""

import logging
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .api import actions, map, observations, proxy, risk, sites
from .config import get_settings
from .db import init_db
from .services import versions, worker

settings = get_settings()
# 콘솔과 함께 파일에도 남긴다 (창을 닫아도 업로드·분석 처리 시간을 나중에 확인)
settings.log_dir.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(settings.log_dir / "roadpush.log", maxBytes=10_000_000, backupCount=5, encoding="utf-8"),
    ],
)
http_log = logging.getLogger("roadpush.http")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    worker.recover_stuck()
    stop = worker.start_thread() if settings.run_worker_in_api else None
    yield
    if stop:
        stop.set()


app = FastAPI(title="RoadPush API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=settings.cors_origin_regex or None,
    allow_methods=["*"],
    allow_headers=["*"],
)

API = "/api/v1"
SLOW_POLL_MS = 1000


@app.middleware("http")
async def log_timing(request: Request, call_next):
    """/api/v1 요청마다 처리 시간. 프론트가 5초마다 보내는 목록 조회는 느리거나 실패했을 때만"""
    start = perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        path = request.url.path
        ms = (perf_counter() - start) * 1000
        poll = request.method == "GET" and path == f"{API}/observations"
        if path.startswith(API) and request.method != "OPTIONS" and (not poll or ms >= SLOW_POLL_MS or status >= 400):
            http_log.info("%s %s %d %.0fms", request.method, path, status, ms)


app.include_router(actions.router, prefix=API)  # /observations/bulk-action이 /observations/{id}보다 먼저
app.include_router(observations.router, prefix=API)
app.include_router(sites.router, prefix=API)
app.include_router(map.router, prefix=API)
app.include_router(risk.router, prefix=API)
app.include_router(proxy.router)  # 프론트 개발 서버 프록시와 같은 경로 (/vworld/wmts, /api/utic, /api/its)

settings.storage_dir.mkdir(parents=True, exist_ok=True)
# 원본·overlay 파일. 운영 단계에서는 인증된 URL로 바꾼다 (직접 촬영 자료의 얼굴·번호판 비식별 확인 필요)
app.mount("/files", StaticFiles(directory=settings.storage_dir), name="files")


@app.get("/", include_in_schema=False)
def root():
    """주소창에 서버 주소만 넣었을 때 API 문서 화면으로 보낸다"""
    return RedirectResponse("/docs")


@app.get(f"{API}/health")
def health():
    return {
        "status": "ok",
        "analysis_engine": settings.analysis_engine,
        "obstacle_causes": settings.analysis_engine == "roadpush_ai",
        # 코드 버전: {"commit", "dirty"} (백엔드·AI 저장소의 git)
        "backend": versions.backend_version(),
        "ai": versions.ai_version(),
        # 분석 워커: alive(스레드 생존)·pending·processing·last_error. alive=false면 업로드는 받아도 분석이 멈춘 상태
        "worker": worker.status(),
    }

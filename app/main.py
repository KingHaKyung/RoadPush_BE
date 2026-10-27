"""
RoadPush 백엔드 진입점.
  uvicorn app.main:app --reload
프론트 .env: VITE_API_BASE_URL=http://localhost:8000/api/v1
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .api import actions, map, observations, proxy, risk, sites
from .config import get_settings
from .db import init_db
from .services import versions, worker

logging.basicConfig(level=logging.INFO)
settings = get_settings()


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
        # 코드 버전: {"commit", "dirty"(git) 또는 "packaged_at"(배포 묶음), "source": git | package}
        "backend": versions.backend_version(),
        "ai": versions.ai_version(),
    }

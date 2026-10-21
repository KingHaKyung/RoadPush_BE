"""
분석 워커. analyses 테이블의 pending 행을 하나씩 집어 처리한다.
GPU가 하나뿐인 MVP에서는 워커 1개가 맞다 (모델을 한 번만 올리고 순서대로 처리).
따로 띄우기: python -m app.services.worker

오류 처리 (마스터 문서 4.3)
- 입력 오류(손상 파일·지원하지 않는 입력): 재시도하지 않고 바로 실패
- 실행 오류: 같은 분석 작업을 max_attempts까지 자동 재시도, 넘으면 실패
- 판단 유보: 실패가 아니라 완료. 검수 대상으로 간다
"""

import logging
import threading
import time
import traceback

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from ..ai_engine.pipeline import InputError
from ..config import get_settings
from ..db import SessionLocal, init_db
from ..db.models import Analysis, Observation, now
from .analysis import run_analysis
from .storage import get_storage

log = logging.getLogger("roadpush.worker")


def claim_next() -> str | None:
    """pending 하나를 processing으로 바꾸고 id를 돌려준다. 다른 워커가 먼저 가져갔으면 None"""
    with SessionLocal() as db:
        ana_id = db.scalar(
            select(Analysis.id).where(Analysis.status == "pending").order_by(Analysis.created_at).limit(1)
        )
        if ana_id is None:
            return None
        n = db.execute(
            update(Analysis)
            .where(Analysis.id == ana_id, Analysis.status == "pending")
            .values(status="processing", attempts=Analysis.attempts + 1)
        ).rowcount
        db.commit()
        return ana_id if n else None


def process(ana_id: str) -> None:
    s = get_settings()
    with SessionLocal() as db:
        ana = db.get(Analysis, ana_id)
        obs = db.scalar(
            select(Observation).where(Observation.id == ana.observation_id).options(selectinload(Observation.site))
        )
        try:
            run_analysis(get_storage(), ana, obs)
            ana.status, ana.error, ana.error_kind = "completed", None, None
        except InputError as e:
            ana.status, ana.error, ana.error_kind = "failed", str(e), "input"
        except Exception as e:  # 실행 오류는 기록하고 한도 안에서 다시 큐로
            log.exception("분석 실패 %s", ana_id)
            ana.error = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"
            if ana.attempts < s.max_attempts:
                ana.status, ana.error_kind = "pending", "runtime"
            else:
                ana.status, ana.error_kind = "failed", "exhausted"
        ana.finished_at = now()
        db.commit()


def run_once() -> bool:
    ana_id = claim_next()
    if ana_id:
        process(ana_id)
    return ana_id is not None


def loop(stop: threading.Event | None = None) -> None:
    s = get_settings()
    log.info("분석 워커 시작 (surface=%s, obstacles=%s)", s.surface_model, s.obstacle_model)
    while not (stop and stop.is_set()):
        if not run_once():
            time.sleep(s.worker_poll_sec)


def start_thread() -> threading.Event:
    stop = threading.Event()
    threading.Thread(target=loop, args=(stop,), daemon=True, name="analysis-worker").start()
    return stop


def recover_stuck() -> None:
    """서버가 처리 중에 꺼졌으면 processing으로 남은 행을 다시 대기로 돌린다"""
    with SessionLocal() as db:
        db.execute(update(Analysis).where(Analysis.status == "processing").values(status="pending"))
        db.commit()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    recover_stuck()
    loop()

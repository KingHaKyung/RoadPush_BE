"""
분석 워커. analyses 테이블의 pending 행을 하나씩 집어 처리한다.
GPU가 하나뿐인 MVP에서는 워커 1개가 맞다 (모델을 한 번만 올리고 순서대로 처리).
따로 띄우기: python -m app.services.worker

오류 처리 (마스터 문서 4.3)
- 입력 오류(손상 파일·지원하지 않는 입력): 재시도하지 않고 바로 실패
- 실행 오류: 같은 분석 작업을 max_attempts까지 자동 재시도, 넘으면 실패
- 판단 유보: 실패가 아니라 완료. 검수 대상으로 간다
- 워커 자체 오류(DB 연결 끊김 등): 스레드를 죽이지 않고 기다렸다 다시 돈다 (최대 MAX_BACKOFF_SEC 간격)

처리 시간은 evidence.timings(초)와 로그 한 줄로 남긴다 (대기·주소·AI 단계·파일 저장·위험도·전체)
"""

import logging
import threading
import time
import traceback
from datetime import UTC, datetime
from time import perf_counter

from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload

from ..ai_engine.pipeline import InputError
from ..config import get_settings
from ..db import SessionLocal, init_db
from ..db.models import Analysis, Observation, now
from .analysis import run_analysis
from .geocode import fill_address
from .storage import get_storage

log = logging.getLogger("roadpush.worker")

MAX_BACKOFF_SEC = 60.0
# /health에 내는 워커 상태
_state: dict = {"thread": None, "last_error": None, "last_error_at": None}


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
            .values(status="processing", attempts=Analysis.attempts + 1, started_at=now())
        ).rowcount
        db.commit()
        return ana_id if n else None


def _utc(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=UTC)  # SQLite는 시간대를 버린다


def _summary(ana: Analysis, t: dict) -> str:
    stages = t.get("ai_stages") or {}
    detail = " / ".join(f"{k} {v:.2f}" for k, v in stages.items() if k != "total")
    ai = f"AI {t['ai']:.1f}s" + (f"({detail})" if detail else "") if "ai" in t else "AI -"
    return (
        f"{ana.id} ({ana.observation_id}) 시도 {ana.attempts} · 대기 {t.get('queue_wait', 0):.1f}s · "
        f"주소 {t.get('address', 0):.2f}s · {ai} · 저장 {t.get('save_files', 0):.2f}s · "
        f"위험도 {t.get('risk', 0):.2f}s · 총 {t['total']:.1f}s"
    )


def process(ana_id: str) -> None:
    s = get_settings()
    start = perf_counter()
    with SessionLocal() as db:
        ana = db.get(Analysis, ana_id)
        obs = db.scalar(
            select(Observation).where(Observation.id == ana.observation_id).options(selectinload(Observation.site))
        )
        t: dict = {}
        if ana.started_at:
            t["queue_wait"] = round((_utc(ana.started_at) - _utc(ana.created_at)).total_seconds(), 3)
        step = perf_counter()
        fill_address(obs)  # 행정구역·주소가 비어 있으면 채운다 (실패해도 분석은 진행)
        t["address"] = round(perf_counter() - step, 3)
        try:
            run_analysis(get_storage(), ana, obs, t)
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
        t["total"] = round(perf_counter() - start, 3)
        ana.evidence = {**(ana.evidence or {}), "timings": t}  # JSON 컬럼은 새 dict로 바꿔야 저장된다
        ana.finished_at = now()
        step = perf_counter()
        db.commit()
        commit = perf_counter() - step
        if ana.status == "completed":
            log.info(
                "분석 완료 %s · 판정 %s 위험도 %s · DB 저장 %.2fs",
                _summary(ana, t),
                ana.passability,
                ana.risk_score,
                commit,
            )
        else:
            log.warning("분석 %s(%s) %s · DB 저장 %.2fs", ana.status, ana.error_kind, _summary(ana, t), commit)


def _release(ana_id: str) -> None:
    """process() 밖 오류(DB 끊김 등)로 processing에 남은 행을 대기로 되돌린다. 실패하면 재시작 때 recover_stuck"""
    try:
        with SessionLocal() as db:
            db.execute(
                update(Analysis).where(Analysis.id == ana_id, Analysis.status == "processing").values(status="pending")
            )
            db.commit()
    except Exception:
        log.warning("%s를 대기로 되돌리지 못함 (서버 재시작 때 되돌림)", ana_id)


def run_once() -> bool:
    ana_id = claim_next()
    if ana_id:
        try:
            process(ana_id)
        except Exception:
            _release(ana_id)
            raise
    return ana_id is not None


def loop(stop: threading.Event | None = None) -> None:
    s = get_settings()
    log.info("분석 워커 시작 (engine=%s)", s.analysis_engine)
    backoff = s.worker_poll_sec
    while not (stop and stop.is_set()):
        try:
            busy = run_once()
        except Exception as e:  # 스레드가 죽으면 업로드는 받는데 분석이 영영 멈춘다
            _state.update(last_error=f"{type(e).__name__}: {e}", last_error_at=now().isoformat())
            log.exception("워커 오류. %.0f초 뒤 다시 시도", backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SEC)
            continue
        backoff = s.worker_poll_sec
        if not busy:
            time.sleep(s.worker_poll_sec)


def start_thread() -> threading.Event:
    stop = threading.Event()
    _state["thread"] = threading.Thread(target=loop, args=(stop,), daemon=True, name="analysis-worker")
    _state["thread"].start()
    return stop


def status() -> dict:
    """/health용: 스레드 생존·대기 건수·마지막 오류. API 안에서 워커를 돌리지 않으면 alive=None"""
    th = _state["thread"]
    out = {
        "alive": th.is_alive() if th else None,
        "last_error": _state["last_error"],
        "last_error_at": _state["last_error_at"],
    }
    try:
        with SessionLocal() as db:
            counts = dict(
                db.execute(
                    select(Analysis.status, func.count())
                    .where(Analysis.status.in_(("pending", "processing")))
                    .group_by(Analysis.status)
                ).all()
            )
        out |= {"pending": counts.get("pending", 0), "processing": counts.get("processing", 0)}
    except Exception as e:
        out |= {"pending": None, "processing": None, "db_error": f"{type(e).__name__}: {e}"}
    return out


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

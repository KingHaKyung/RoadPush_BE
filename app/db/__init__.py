from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from ..config import get_settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    if url.startswith("sqlite"):
        # 워커 스레드와 API가 같은 파일을 쓰므로 스레드 검사를 끈다
        return create_engine(url, connect_args={"check_same_thread": False})
    # PostgreSQL(Supabase): 끊긴 연결을 미리 확인하고, 트랜잭션 풀러(6543)에서도 되도록 prepared statement를 끈다
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5, connect_args={"prepare_threshold": None})


engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(engine, expire_on_commit=False)

ROOT = Path(__file__).resolve().parents[2]


def init_db() -> None:
    """서버 시작 때 migrations/의 마이그레이션을 최신까지 적용한다 (SQLite·Supabase 공통)"""
    import logging

    logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)  # import 때 찍히는 플러그인 로그 숨김
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(cfg, "head")


def get_db() -> Iterator[Session]:
    with SessionLocal() as db:
        yield db

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from ..config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()
engine = create_engine(
    _settings.database_url,
    # SQLite는 워커 스레드와 API가 같은 파일을 쓰므로 스레드 검사를 끈다
    connect_args={"check_same_thread": False} if _settings.database_url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def init_db() -> None:
    from . import models  # noqa: F401  테이블 등록

    # MVP는 create_all. 스키마가 자주 바뀌기 시작하면 migrations/에 Alembic을 둔다
    Base.metadata.create_all(engine)


def get_db() -> Iterator[Session]:
    with SessionLocal() as db:
        yield db

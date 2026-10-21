"""Alembic 환경. 접속 주소는 앱 설정(.env의 DATABASE_URL)을 그대로 쓴다."""

from alembic import context

import app.db.models  # noqa: F401  테이블 등록
from app.config import get_settings
from app.db import Base, make_engine

target_metadata = Base.metadata


def run_offline() -> None:
    """alembic upgrade head --sql : DB에 붙지 않고 SQL만 출력 (Supabase SQL 편집기 확인용)"""
    context.configure(url=get_settings().database_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    engine = make_engine(get_settings().database_url)
    with engine.connect() as conn:
        # SQLite는 ALTER TABLE이 제한적이라 batch 모드로 바꾼다
        context.configure(
            connection=conn, target_metadata=target_metadata, render_as_batch=conn.dialect.name == "sqlite"
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    run_online()

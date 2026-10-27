"""
서비스 데이터(구간·관측·분석·검수·조치·메모·위험도 기준)를 한 DB에서 다른 DB로 그대로 복사한다.
로컬 SQLite에서 분석까지 끝낸 결과를 Supabase로 옮길 때 쓴다 (다시 분석하지 않으므로 결과가 PC와 같다).

    python scripts/copy_service_data.py --to "<Supabase 주소>"                        # .env DATABASE_URL → 대상
    python scripts/copy_service_data.py --from sqlite:///./data/roadpush.db --to "<주소>" --replace

- 대상은 서버를 한 번 띄웠거나 alembic upgrade head로 같은 스키마 버전이어야 한다 (버전이 다르면 멈춘다)
- 대상에 서비스 데이터가 있으면 멈춘다. --replace를 주면 대상 서비스 테이블을 비우고 넣는다
- 참조 레이어(ref_*)는 scripts/load_reference.py로 따로 옮긴다
- 사진·overlay 파일은 DB에 없다 (저장 키만). STORAGE_DIR 폴더를 배포 서버에 따로 복사한다 (docs/DEPLOY.md)
"""

import argparse
import sys
from datetime import UTC
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import DateTime, create_engine, func, select, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

import app.db.models  # noqa: E402, F401  (테이블 등록)
from app.config import get_settings  # noqa: E402
from app.db import Base  # noqa: E402

BATCH = 500
TABLES = [t for t in Base.metadata.sorted_tables]  # 외래키 순서 (구간 → 관측 → 분석 → …)


def engine_for(url: str):
    url = url.replace("postgresql://", "postgresql+psycopg://", 1) if url.startswith("postgresql://") else url
    return create_engine(url, connect_args={"prepare_threshold": None} if url.startswith("postgresql") else {})


def _version(eng) -> str | None:
    with eng.connect() as conn:
        return conn.scalar(text("SELECT version_num FROM alembic_version"))


def _utc(row: dict, table) -> dict:
    """SQLite는 시간대 없이 UTC로 돌려준다 → 시간대를 붙여 PostgreSQL에 같은 시각으로 넣는다"""
    for c in table.columns:
        v = row.get(c.name)
        if isinstance(c.type, DateTime) and v is not None and v.tzinfo is None:
            row[c.name] = v.replace(tzinfo=UTC)
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", help="원본 DB (기본 .env의 DATABASE_URL)")
    ap.add_argument("--to", required=True, help="대상 DB")
    ap.add_argument("--replace", action="store_true", help="대상 서비스 테이블을 비우고 넣는다")
    args = ap.parse_args()

    src = engine_for(args.src or get_settings().database_url)
    dst = engine_for(args.to)
    show = lambda e: make_url(str(e.url)).render_as_string(hide_password=True)  # noqa: E731
    print(f"원본: {show(src)}\n대상: {show(dst)}")
    if (sv := _version(src)) != (dv := _version(dst)):
        sys.exit(f"[X] 스키마 버전이 다릅니다 (원본 {sv}, 대상 {dv}). alembic upgrade head로 맞추세요")

    with dst.connect() as conn:
        have = {t.name: conn.scalar(select(func.count()).select_from(t)) for t in TABLES}
    if any(have.values()) and not args.replace:
        sys.exit(f"[X] 대상에 이미 데이터가 있습니다 {have}. 비우고 넣으려면 --replace")

    with src.connect() as s, dst.begin() as d:
        if args.replace:
            for t in reversed(TABLES):
                d.execute(t.delete())
        for t in TABLES:
            rows = [_utc(dict(r), t) for r in s.execute(select(t)).mappings()]
            for i in range(0, len(rows), BATCH):
                d.execute(t.insert(), rows[i : i + BATCH])
            print(f"  {t.name}: {len(rows):,}행")

    with src.connect() as s, dst.connect() as d:
        diff = [
            t.name
            for t in TABLES
            if s.scalar(select(func.count()).select_from(t)) != d.scalar(select(func.count()).select_from(t))
        ]
    if diff:
        sys.exit(f"[X] 행 수가 다릅니다: {diff}")
    print("[OK] 모든 서비스 테이블 행 수 일치. 사진 파일(STORAGE_DIR)은 배포 서버에 따로 복사하세요")


if __name__ == "__main__":
    main()

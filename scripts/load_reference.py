"""
참조 레이어(ref_* 네 테이블)를 DB에 넣는다. 팀원이 준 CSV 폴더나, 이미 넣어 둔 다른 DB에서 읽는다.

    python scripts/load_reference.py <CSV 폴더>                               # .env의 DATABASE_URL로
    python scripts/load_reference.py <CSV 폴더> --to "<주소>"
    python scripts/load_reference.py --from-db sqlite:///./data/roadpush.db --to "<Supabase 주소>"  # 로컬 → Supabase

테이블 정의는 scripts/reference/ref_schema_v2.sql (팀원 제공 01_schema.sql v2).
PostgreSQL은 이 파일 전체(RLS 포함)를 실행하고, SQLite는 CREATE 문만 실행한다.
네 테이블을 비우고 다시 넣는다. 비밀번호는 출력하지 않는다.
"""

import argparse
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import Boolean, Float, Integer, MetaData, Numeric, Table, create_engine, select, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.config import get_settings  # noqa: E402

SCHEMA = Path(__file__).with_name("reference") / "ref_schema_v2.sql"
TABLES = ["ref_seongnam_link", "ref_walk_link", "ref_road_segment", "ref_sidewalk_section"]
BATCH = 5000
csv.field_size_limit(1 << 30)  # geom_wkt가 긴 링크


def engine_for(url: str):
    url = url.replace("postgresql://", "postgresql+psycopg://", 1) if url.startswith("postgresql://") else url
    return create_engine(url, connect_args={"prepare_threshold": None} if url.startswith("postgresql") else {})


def create_tables(eng) -> None:
    sql = SCHEMA.read_text(encoding="utf-8")
    with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        if eng.dialect.name == "postgresql":
            conn.exec_driver_sql(sql)
            return
        # SQLite: ALTER … IF NOT EXISTS·RLS가 없으므로 CREATE 문만
        for stmt in re.sub(r"--[^\n]*", "", sql).split(";"):
            if stmt.strip().upper().startswith("CREATE"):
                conn.exec_driver_sql(stmt)


def converter(col):
    t = col.type
    if isinstance(t, Boolean):
        return lambda v: v if isinstance(v, bool) else str(v).strip().lower() in ("true", "t", "1")
    if isinstance(t, Integer):
        return lambda v: int(float(v))  # CSV에 '2.0'처럼 들어온 정수
    if isinstance(t, Float | Numeric):
        return float
    return lambda v: v


def rows_from_csv(folder: Path, name: str):
    with (folder / f"{name}.csv").open(encoding="utf-8", newline="") as f:
        yield from csv.DictReader(f)


def rows_from_db(src, name: str):
    with src.connect() as conn:
        for r in conn.execute(text(f"SELECT * FROM {name}")).mappings():
            yield dict(r)


def load(eng, name: str, rows) -> int:
    table = Table(name, MetaData(), autoload_with=eng)
    conv = {c.name: converter(c) for c in table.columns}
    n, batch = 0, []
    with eng.begin() as conn:
        conn.execute(table.delete())
        for r in rows:
            batch.append({k: (None if v is None or v == "" else conv[k](v)) for k, v in r.items() if k in conv})
            if len(batch) >= BATCH:
                conn.execute(table.insert(), batch)
                n += len(batch)
                batch = []
                print(f"\r  {name}: {n:,}행", end="", flush=True)
        if batch:
            conn.execute(table.insert(), batch)
            n += len(batch)
    print(f"\r  {name}: {n:,}행 적재")
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?", type=Path, help="CSV 폴더 (ref_seongnam_link.csv 등)")
    ap.add_argument("--from-db", help="CSV 대신 이 DB의 ref_* 테이블에서 읽는다")
    ap.add_argument("--to", help="넣을 DB 주소 (기본 .env의 DATABASE_URL)")
    args = ap.parse_args()
    if bool(args.folder) == bool(args.from_db):
        sys.exit("CSV 폴더나 --from-db 중 하나를 주세요")

    eng = engine_for(args.to or get_settings().database_url)
    print(f"대상: {make_url(str(eng.url)).render_as_string(hide_password=True)}")
    src = engine_for(args.from_db) if args.from_db else None
    create_tables(eng)
    for name in TABLES:
        rows = rows_from_db(src, name) if src else rows_from_csv(args.folder, name)
        load(eng, name, rows)
    with eng.connect() as conn:
        for name in TABLES:
            print(f"[OK] {name}: {conn.scalar(select(text('count(*)')).select_from(text(name))):,}행")


if __name__ == "__main__":
    main()

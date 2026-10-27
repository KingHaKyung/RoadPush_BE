"""
참조 레이어(ref_* 네 테이블)를 DB에 넣는다. 팀원이 준 CSV 폴더나, 이미 넣어 둔 다른 DB에서 읽는다.

    python scripts/load_reference.py <CSV 폴더>                               # .env의 DATABASE_URL로
    python scripts/load_reference.py <CSV 폴더> --to "<주소>"
    python scripts/load_reference.py --from-db sqlite:///./data/roadpush.db --to "<Supabase 주소>"  # 로컬 → Supabase
    python scripts/load_reference.py --schema-only --to "<주소>"            # 테이블·컬럼만 (데이터 없이)
    python scripts/load_reference.py --road-width-rw 실폭.csv [--refresh-sites]  # 실폭만 갱신 (link_id,road_width_rw_m)

테이블 정의는 scripts/reference/ref_schema_v2.sql (팀원 제공 01_schema.sql v2) + EXTRA_COLUMNS(백엔드가 더한 컬럼).
PostgreSQL은 이 파일 전체(RLS 포함)를 실행하고, SQLite는 CREATE 문만 실행한다.
네 테이블을 비우고 다시 넣는다. 비밀번호는 출력하지 않는다.

실폭(도로명주소 실폭도로 면에서 계산한 차도 폭)은 v2 원본에 성남 값이 없다. 받으면 --road-width-rw로 넣는다.
CSV 열: link_id, road_width_rw_m (ref_seongnam_link는 표준노드링크 LINK_ID, ref_walk_link는 서울 LNKG_ID → --table)
--refresh-sites를 주면 등록된 구간의 도로폭·보도 폭을 다시 채우고 위험도를 다시 낸다 (현장 실측 값은 그대로).
"""

import argparse
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import (  # noqa: E402
    Boolean,
    Float,
    Integer,
    MetaData,
    Numeric,
    Table,
    create_engine,
    inspect,
    select,
    text,
)
from sqlalchemy.engine import make_url  # noqa: E402

from app.config import get_settings  # noqa: E402

SCHEMA = Path(__file__).with_name("reference") / "ref_schema_v2.sql"
TABLES = ["ref_seongnam_link", "ref_walk_link", "ref_road_segment", "ref_sidewalk_section"]
# v2 원본 스키마 밖에서 백엔드가 더한 컬럼 (SQLite·PostgreSQL 공통 타입)
EXTRA_COLUMNS = {
    "ref_seongnam_link": {"road_width_rw_m": "REAL"},  # 도로명주소 실폭 (출처 코드 rw)
    "ref_walk_link": {"road_width_rw_m": "REAL"},
}
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
        else:  # SQLite: ALTER … IF NOT EXISTS·RLS가 없으므로 CREATE 문만
            for stmt in re.sub(r"--[^\n]*", "", sql).split(";"):
                if stmt.strip().upper().startswith("CREATE"):
                    conn.exec_driver_sql(stmt)
    ensure_columns(eng)


def ensure_columns(eng) -> list[str]:
    """EXTRA_COLUMNS 중 없는 컬럼을 더한다 (이미 있으면 그대로). 더한 'table.column' 목록"""
    added = []
    insp = inspect(eng)
    with eng.begin() as conn:
        for table, cols in EXTRA_COLUMNS.items():
            if not insp.has_table(table):
                continue
            have = {c["name"] for c in insp.get_columns(table)}
            for col, typ in cols.items():
                if col not in have:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
                    added.append(f"{table}.{col}")
    return added


def load_road_width_rw(eng, path: Path, table: str) -> tuple[int, int]:
    """실폭 CSV로 road_width_rw_m만 갱신. (갱신한 행, CSV에 있지만 테이블에 없는 link_id 수)"""
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = [
            {"id": r["link_id"].strip(), "w": float(r["road_width_rw_m"])}
            for r in csv.DictReader(f)
            if (r.get("road_width_rw_m") or "").strip()
        ]
    bad = [r["w"] for r in rows if r["w"] <= 0]
    if bad:
        sys.exit(f"[X] 실폭은 양수여야 합니다 (0 이하 {len(bad)}건)")
    sql = text(f"UPDATE {table} SET road_width_rw_m = :w WHERE link_id = :id")
    with eng.begin() as conn:
        ids = {r["id"] for r in rows}
        found = {str(v) for (v,) in conn.execute(text(f"SELECT link_id FROM {table}"))}
        hits = [r for r in rows if r["id"] in found]
        for i in range(0, len(hits), BATCH):
            conn.execute(sql, hits[i : i + BATCH])
    return len(hits), len(ids - found)


def refresh_sites(url: str) -> tuple[int, int]:
    """등록된 구간의 참조 값을 다시 채우고 위험도를 다시 낸다. (구간 수, 점수가 바뀐 분석 수)"""
    from sqlalchemy.orm import Session

    from app.db.models import Site
    from app.services import reference, risk_policy

    with Session(engine_for(url)) as db:
        sites = list(db.scalars(select(Site)))
        for site in sites:
            reference.refresh_site(db, site)
        n = risk_policy.recompute(db, *risk_policy.current_params(db))
        db.commit()
    return len(sites), n


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
    ap.add_argument("--schema-only", action="store_true", help="테이블·컬럼만 만들고 데이터는 넣지 않음")
    ap.add_argument("--road-width-rw", type=Path, help="실폭 CSV (link_id, road_width_rw_m)로 실폭만 갱신")
    ap.add_argument("--table", default="ref_seongnam_link", choices=list(EXTRA_COLUMNS), help="--road-width-rw 대상")
    ap.add_argument("--refresh-sites", action="store_true", help="구간 도로폭·보도 폭을 다시 채우고 위험도 재계산")
    args = ap.parse_args()
    modes = sum([bool(args.folder), bool(args.from_db), args.schema_only, bool(args.road_width_rw)])
    if modes > 1 or (modes == 0 and not args.refresh_sites):
        sys.exit("CSV 폴더, --from-db, --schema-only, --road-width-rw 중 하나를 주세요 (또는 --refresh-sites만)")

    url = args.to or get_settings().database_url
    eng = engine_for(url)
    print(f"대상: {make_url(str(eng.url)).render_as_string(hide_password=True)}")

    if args.road_width_rw:
        if not inspect(eng).has_table(args.table):
            sys.exit(f"[X] {args.table}이 없습니다. 참조 데이터를 먼저 넣어 주세요")
        print("컬럼 추가:", ", ".join(ensure_columns(eng)) or "없음 (이미 있음)")
        n, missing = load_road_width_rw(eng, args.road_width_rw, args.table)
        print(f"[OK] {args.table}.road_width_rw_m {n:,}행 갱신")
        if missing:
            print(f"[!] 테이블에 없는 link_id {missing:,}개는 건너뜀")
    elif args.folder or args.from_db or args.schema_only:
        create_tables(eng)
        if args.schema_only:
            print("[OK] 테이블·컬럼 준비 (데이터 없음):", ", ".join(TABLES))
        else:
            src = engine_for(args.from_db) if args.from_db else None
            for name in TABLES:
                load(eng, name, rows_from_db(src, name) if src else rows_from_csv(args.folder, name))
            with eng.connect() as conn:
                for name in TABLES:
                    print(f"[OK] {name}: {conn.scalar(select(text('count(*)')).select_from(text(name))):,}행")

    if args.refresh_sites:
        sites, changed = refresh_sites(url)
        print(f"[OK] 구간 {sites}개 다시 채움 · 위험도 바뀐 분석 {changed}건")


if __name__ == "__main__":
    main()

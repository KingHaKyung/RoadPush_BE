"""
.env의 DATABASE_URL로 DB 연결을 점검한다. Supabase 연결 직후 이 스크립트로 확인한다.

    python scripts/check_db.py            # 연결·마이그레이션 상태·테이블·RLS 확인
    python scripts/check_db.py --upgrade  # 마이그레이션까지 적용 (서버를 켜지 않고 테이블 만들기)

비밀번호는 출력하지 않는다.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.config import get_settings  # noqa: E402

TABLES = ["sites", "observations", "analyses", "reviews", "actions", "action_logs"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--upgrade", action="store_true", help="마이그레이션을 최신까지 적용")
    args = ap.parse_args()

    url = make_url(get_settings().database_url)
    print(f"대상: {url.render_as_string(hide_password=True)}")

    from app.db import engine, init_db

    try:
        with engine.connect() as conn:
            if conn.dialect.name == "postgresql":
                print(f"[OK] 연결 · {conn.execute(text('select version()')).scalar().split(',')[0]}")
            else:
                print(f"[OK] 연결 · {conn.dialect.name}")
    except Exception as e:  # 연결 실패 원인을 그대로 보여준다
        print(f"[FAIL] 연결 실패: {type(e).__name__}: {str(e).splitlines()[0]}")
        print("  - 주소를 Supabase 대시보드 Connect → Session pooler에서 다시 복사했는지")
        print("  - 앞부분이 postgresql+psycopg:// 인지, 비밀번호 특수문자를 URL 인코딩했는지")
        print("  - 프로젝트가 일시정지(Paused) 상태가 아닌지")
        sys.exit(1)

    if args.upgrade:
        init_db()
        print("[OK] 마이그레이션 적용")

    from alembic.script import ScriptDirectory

    from app.db import alembic_config

    head = ScriptDirectory.from_config(alembic_config()).get_current_head()

    insp = inspect(engine)
    existing = set(insp.get_table_names())
    current = None
    if "alembic_version" in existing:
        with engine.connect() as conn:
            current = conn.execute(text("select version_num from alembic_version")).scalar()
    state = "최신" if current == head else ("미적용 (서버 실행 또는 --upgrade)" if current is None else "이전 버전")
    print(f"[{'OK' if current == head else '!!'}] 마이그레이션: DB={current} / 코드={head} → {state}")

    missing = [t for t in TABLES if t not in existing]
    print(
        f"[{'OK' if not missing else '!!'}] 테이블: {len(TABLES) - len(missing)}/{len(TABLES)}"
        + (f" (없음: {missing})" if missing else "")
    )

    if engine.dialect.name == "postgresql" and not missing:
        with engine.connect() as conn:
            rows = conn.execute(
                text("select relname, relrowsecurity from pg_class where relname = any(:t)"),
                {"t": [*TABLES, "alembic_version"]},
            ).all()
        off = [r[0] for r in rows if not r[1]]
        print(
            f"[{'OK' if not off else '!!'}] RLS(외부 API 노출 차단): "
            + ("모든 테이블 켜짐" if not off else f"꺼짐 {off}")
        )

    if engine.dialect.name == "postgresql" and not missing:
        with engine.connect() as conn:
            n = {t: conn.execute(text(f"select count(*) from {t}")).scalar() for t in ("sites", "observations")}
        print(f"[OK] 데이터: 구간 {n['sites']} · 관측 {n['observations']}")


if __name__ == "__main__":
    main()

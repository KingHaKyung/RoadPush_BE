"""
이미 등록한 관측 중 area·addr가 빈 것을 브이월드 역지오코딩으로 채운다 (새 관측은 분석 워커가 자동으로 채움).

    python scripts/geocode_observations.py           # 빈 관측만 채우기
    python scripts/geocode_observations.py --dry-run # 바꾸지 않고 결과만 출력

.env의 VWORLD_KEY가 필요하다.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import or_, select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.db.models import Observation  # noqa: E402
from app.services.geocode import fill_address  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="DB를 바꾸지 않음")
    args = ap.parse_args()
    if not get_settings().vworld_key:
        sys.exit(".env에 VWORLD_KEY가 없습니다")

    init_db()
    with SessionLocal() as db:
        rows = db.scalars(
            select(Observation)
            .where(or_(Observation.area.is_(None), Observation.addr.is_(None)))
            .options(selectinload(Observation.site))
        ).all()
        ok = 0
        for o in rows:
            if fill_address(o):
                ok += 1
                print(f"{o.id}  {o.area}  |  {o.addr}")
        if args.dry_run:
            db.rollback()
        else:
            db.commit()
    print(f"대상 {len(rows)}건 → 채움 {ok}건{' (dry-run, 저장 안 함)' if args.dry_run else ''}")


if __name__ == "__main__":
    main()

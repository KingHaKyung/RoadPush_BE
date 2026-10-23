"""구간 보도 폭 출처와 참조 보행 링크 키 (팀원 제공 ref_walk_link 연결)

ref_* 테이블 자체는 Alembic 밖에서 만든다 (docs/DB.md 4장). 여기서는 sites 컬럼만 더한다.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("sites") as b:
        b.add_column(sa.Column("sidewalk_width_source", sa.String(24)))
        b.add_column(sa.Column("ref_walk_link_id", sa.String(20)))
    op.execute("UPDATE sites SET sidewalk_width_source = 'field' WHERE sidewalk_width_m IS NOT NULL")


def downgrade() -> None:
    with op.batch_alter_table("sites") as b:
        b.drop_column("ref_walk_link_id")
        b.drop_column("sidewalk_width_source")

"""분석 시작 시각 (등록 → 워커가 집을 때까지 대기 시간을 재려고)

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("analyses") as b:
        b.add_column(sa.Column("started_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    with op.batch_alter_table("analyses") as b:
        b.drop_column("started_at")

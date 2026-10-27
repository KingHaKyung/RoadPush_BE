"""구간 도로폭(실폭 우선)·차로 수, 위험도 기준(risk_policies) 버전 테이블

ref_* 테이블의 실폭 컬럼(road_width_rw_m)은 Alembic 밖이라 scripts/load_reference.py가 더한다.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("sites") as b:
        b.add_column(sa.Column("road_width_m", sa.Float()))
        b.add_column(sa.Column("road_width_source", sa.String(24)))
        b.add_column(sa.Column("road_lanes", sa.Integer()))
    op.create_table(
        "risk_policies",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False, unique=True),
        sa.Column("params", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("created_by", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    if op.get_bind().dialect.name == "postgresql":
        # 0001과 같은 이유로 Supabase Data API 노출을 막는다
        op.execute("ALTER TABLE risk_policies ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("risk_policies")
    with op.batch_alter_table("sites") as b:
        b.drop_column("road_lanes")
        b.drop_column("road_width_source")
        b.drop_column("road_width_m")

"""검수 취소·재분석 취소 상태, 관측 메모 테이블 (프론트 판정 취소·재분석 취소·메모 저장)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _status(col: str, values: list[str]) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


def _replace_check(table: str, name: str, values: list[str]) -> None:
    with op.batch_alter_table(table) as b:
        b.drop_constraint(name, type_="check")
        b.create_check_constraint(name, _status("status", values))


def upgrade() -> None:
    _replace_check("analyses", "ck_analyses_status", ["pending", "processing", "completed", "failed", "cancelled"])
    _replace_check("reviews", "ck_reviews_status", ["confirmed", "corrected", "cancelled"])
    op.create_table(
        "observation_notes",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("observation_id", sa.String(32), sa.ForeignKey("observations.id"), nullable=False, index=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("author", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    if op.get_bind().dialect.name == "postgresql":
        # 0001과 같은 이유로 Supabase Data API 노출을 막는다
        op.execute("ALTER TABLE observation_notes ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("observation_notes")
    op.execute("DELETE FROM reviews WHERE status = 'cancelled'")
    op.execute("UPDATE analyses SET status = 'failed', error_kind = 'input' WHERE status = 'cancelled'")
    _replace_check("reviews", "ck_reviews_status", ["confirmed", "corrected"])
    _replace_check("analyses", "ck_analyses_status", ["pending", "processing", "completed", "failed"])

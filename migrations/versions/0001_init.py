"""초기 스키마: 구간·관측·분석·검수·조치 (마스터 문서 5장, 4.4 상태 코드)

Revision ID: 0001
Revises:
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ["sites", "observations", "analyses", "reviews", "actions", "action_logs"]


def json() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def tstz(nullable: bool = False, name: str = "created_at") -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def check(col: str, values: list[str], name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(f"{col} IN ({', '.join(repr(v) for v in values)})", name=name)


def upgrade() -> None:
    op.create_table(
        "sites",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lng", sa.Float(), nullable=False),
        sa.Column("link_id", sa.String(20), index=True),
        sa.Column("sidewalk_width_m", sa.Float()),
        tstz(),
    )
    op.create_table(
        "observations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("site_id", sa.String(32), sa.ForeignKey("sites.id"), nullable=False, index=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("device", sa.String(64)),
        sa.Column("lat", sa.Float()),
        sa.Column("lng", sa.Float()),
        sa.Column("location_source", sa.String(16)),
        tstz(name="captured_at"),
        sa.Column("captured_at_source", sa.String(16), nullable=False),
        sa.Column("area", sa.String(100)),
        sa.Column("addr", sa.String(200)),
        sa.Column("media_type", sa.String(8), nullable=False),
        sa.Column("original_key", sa.String(300), nullable=False),
        tstz(),
        check("media_type", ["image", "video"], "ck_observations_media_type"),
    )
    op.create_index("ix_observations_captured_at", "observations", ["captured_at"])
    # 지도: 구간별 최신 촬영 관측
    op.create_index("ix_observations_site_captured", "observations", ["site_id", "captured_at"])

    op.create_table(
        "analyses",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("observation_id", sa.String(32), sa.ForeignKey("observations.id"), nullable=False, index=True),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("reanalysis_note", sa.Text()),
        sa.Column("status", sa.String(16), nullable=False, index=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("error_kind", sa.String(16)),
        sa.Column("model_version", sa.String(200)),
        sa.Column("rule_version", sa.String(60)),
        sa.Column("passability", sa.String(16)),
        sa.Column("risk_score", sa.Integer()),
        sa.Column("occupancy", sa.Integer()),
        sa.Column("bottleneck_ratio", sa.Float()),
        sa.Column("bottleneck_m_est", sa.Float()),
        sa.Column("confidence", sa.Float()),
        sa.Column("uncertainty_code", sa.String(20)),
        sa.Column("uncertainty_reason", sa.Text()),
        sa.Column("causes", json(), nullable=False),
        sa.Column("evidence", json(), nullable=False),
        sa.Column("files", json(), nullable=False),
        tstz(),
        tstz(nullable=True, name="finished_at"),
        check("trigger", ["initial", "reanalysis"], "ck_analyses_trigger"),
        check("status", ["pending", "processing", "completed", "failed"], "ck_analyses_status"),
        sa.CheckConstraint(
            "passability IS NULL OR passability IN ('passable', 'narrowed', 'blocked', 'undetermined')",
            name="ck_analyses_passability",
        ),
        sa.CheckConstraint("risk_score IS NULL OR risk_score BETWEEN 0 AND 100", name="ck_analyses_risk_score"),
    )
    op.create_table(
        "reviews",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("analysis_id", sa.String(32), sa.ForeignKey("analyses.id"), nullable=False, index=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("human_passability", sa.String(16)),
        sa.Column("comment", sa.Text()),
        sa.Column("reviewer", sa.String(64)),
        tstz(),
        check("status", ["confirmed", "corrected"], "ck_reviews_status"),
        sa.CheckConstraint(
            "human_passability IS NULL OR human_passability IN ('passable', 'narrowed', 'blocked', 'undetermined')",
            name="ck_reviews_human_passability",
        ),
    )
    op.create_table(
        "actions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("site_id", sa.String(32), sa.ForeignKey("sites.id"), nullable=False, index=True),
        sa.Column("before_observation_id", sa.String(32), sa.ForeignKey("observations.id"), nullable=False, index=True),
        sa.Column("after_observation_id", sa.String(32), sa.ForeignKey("observations.id")),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("dept", sa.String(64)),
        sa.Column("content", sa.Text()),
        tstz(),
        tstz(name="updated_at"),
        check("state", ["none", "inspection_requested", "in_progress", "done"], "ck_actions_state"),
    )
    op.create_table(
        "action_logs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("action_id", sa.String(32), sa.ForeignKey("actions.id"), nullable=False, index=True),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("dept", sa.String(64)),
        sa.Column("note", sa.Text()),
        sa.Column("actor", sa.String(64)),
        tstz(),
    )

    if op.get_bind().dialect.name == "postgresql":
        # Supabase는 public 스키마 테이블을 anon 키로 Data API에 노출한다.
        # 프론트는 DB에 직접 붙지 않으므로(마스터 문서 3장) RLS를 켜고 정책을 두지 않아 외부 접근을 막는다.
        # 백엔드는 postgres 역할로 접속해 RLS 영향을 받지 않는다.
        for t in [*TABLES, "alembic_version"]:
            op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    for t in reversed(TABLES):
        op.drop_table(t)

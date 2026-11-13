"""instagram: métricas por día, datos de historias y análisis guardados

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-13 20:00:00

Solo agrega tablas y columnas nuevas; no toca los datos de leads.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("instagram_posts") as batch:
        batch.add_column(sa.Column("profile_visits", sa.Integer()))
        batch.add_column(sa.Column("follows", sa.Integer()))
        batch.add_column(sa.Column("replies", sa.Integer()))
    op.create_table(
        "instagram_daily",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("day", sa.DateTime(), nullable=False),
        sa.Column("reach", sa.Integer()),
        sa.Column("views", sa.Integer()),
        sa.Column("profile_views", sa.Integer()),
        sa.Column("website_clicks", sa.Integer()),
        sa.Column("accounts_engaged", sa.Integer()),
        sa.Column("total_interactions", sa.Integer()),
        sa.Column("link_taps", sa.Integer()),
        sa.Column("link_breakdown", sa.JSON()),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("workspace_id", "day"),
    )
    op.create_table(
        "advice",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("workspace_id", "scope", "key"),
    )


def downgrade() -> None:
    op.drop_table("advice")
    op.drop_table("instagram_daily")
    with op.batch_alter_table("instagram_posts") as batch:
        batch.drop_column("replies")
        batch.drop_column("follows")
        batch.drop_column("profile_visits")

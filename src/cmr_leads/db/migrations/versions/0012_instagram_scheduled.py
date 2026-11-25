"""instagram: publicaciones programadas desde la app

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-14 10:00:00

Solo agrega una tabla nueva; no toca los datos existentes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "instagram_scheduled",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("plan_item_id", sa.Integer(), sa.ForeignKey("instagram_plan_items.id")),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("caption", sa.Text()),
        sa.Column("publish_at", sa.DateTime(), nullable=False),
        sa.Column("files", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pendiente"),
        sa.Column("media_id", sa.String(64)),
        sa.Column("permalink", sa.String(300)),
        sa.Column("error", sa.Text()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("published_at", sa.DateTime()),
    )
    op.create_index("ix_instagram_scheduled_workspace_id", "instagram_scheduled", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_instagram_scheduled_workspace_id", "instagram_scheduled")
    op.drop_table("instagram_scheduled")

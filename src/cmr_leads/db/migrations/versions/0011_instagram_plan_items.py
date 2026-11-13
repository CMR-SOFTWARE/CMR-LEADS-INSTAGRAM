"""instagram: cambios hechos a mano en el calendario

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-13 22:00:00

Solo agrega una tabla nueva; no toca los datos existentes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "instagram_plan_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("day", sa.DateTime(), nullable=False),
        sa.Column("hour", sa.Integer()),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("origin_day", sa.DateTime()),
        sa.Column("removed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_instagram_plan_items_workspace_id", "instagram_plan_items", ["workspace_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_instagram_plan_items_workspace_id", "instagram_plan_items")
    op.drop_table("instagram_plan_items")

"""estado de la app en la base y publicación en dos pasos

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-25 12:00:00

- app_state: lo que antes se guardaba en archivos JSON (búsqueda automática, DMs), para que
  dure también en la nube.
- instagram_scheduled.containers: contenedores de Meta ya creados, para publicar en dos pasos
  (preparar y, en la vuelta siguiente, publicar) sin esperar dentro de una misma llamada.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("value", sa.JSON()),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("workspace_id", "key", name="uq_app_state_key"),
    )
    with op.batch_alter_table("instagram_scheduled") as batch:
        batch.add_column(sa.Column("containers", sa.JSON()))


def downgrade() -> None:
    with op.batch_alter_table("instagram_scheduled") as batch:
        batch.drop_column("containers")
    op.drop_table("app_state")

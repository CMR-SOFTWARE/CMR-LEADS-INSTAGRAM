"""instagram: seguidores ganados por día

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-15 10:00:00

Solo agrega una columna; no toca los datos existentes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("instagram_daily") as batch:
        batch.add_column(sa.Column("follows", sa.Integer()))


def downgrade() -> None:
    with op.batch_alter_table("instagram_daily") as batch:
        batch.drop_column("follows")

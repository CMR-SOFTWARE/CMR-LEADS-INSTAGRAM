"""publicidad: desde qué día hay métricas guardadas (para completar el año)

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-13 18:00:00

Solo agrega una columna a ad_accounts; no toca los datos de leads.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("ad_accounts") as batch:
        batch.add_column(sa.Column("history_since", sa.DateTime()))


def downgrade() -> None:
    with op.batch_alter_table("ad_accounts") as batch:
        batch.drop_column("history_since")

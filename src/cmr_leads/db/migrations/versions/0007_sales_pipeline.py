"""embudo de ventas: etapas, reuniones y propuestas

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-05 12:00:00

Solo agrega columnas y tablas; no borra ni modifica datos existentes (salvo completar
`stage_entered_at` con la fecha del último cambio de estado, que antes no existía).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("leads") as batch:
        batch.add_column(sa.Column("next_step", sa.String(300), nullable=True))
        batch.add_column(sa.Column("stage_entered_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("lost_reason", sa.String(40), nullable=True))
        batch.add_column(sa.Column("deal_amount_ars", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("web_status", sa.String(10), nullable=True))
    op.execute(
        """
        UPDATE leads SET stage_entered_at = COALESCE(
            (SELECT MAX(changed_at) FROM status_changes WHERE status_changes.lead_id = leads.id),
            first_seen_at)
        """
    )
    op.create_table(
        "meetings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "lead_id", sa.Integer(), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("scheduled_at", sa.DateTime(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("next_steps", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_meetings_lead_id", "meetings", ["lead_id"])
    op.create_index("ix_meetings_scheduled_at", "meetings", ["scheduled_at"])
    op.create_table(
        "proposals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "lead_id", sa.Integer(), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("plan", sa.String(40), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("monthly_ars", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("one_time_ars", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="borrador"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_proposals_lead_id", "proposals", ["lead_id"])


def downgrade() -> None:
    op.drop_index("ix_proposals_lead_id", "proposals")
    op.drop_table("proposals")
    op.drop_index("ix_meetings_scheduled_at", "meetings")
    op.drop_index("ix_meetings_lead_id", "meetings")
    op.drop_table("meetings")
    with op.batch_alter_table("leads") as batch:
        batch.drop_column("web_status")
        batch.drop_column("deal_amount_ars")
        batch.drop_column("lost_reason")
        batch.drop_column("stage_entered_at")
        batch.drop_column("next_step")

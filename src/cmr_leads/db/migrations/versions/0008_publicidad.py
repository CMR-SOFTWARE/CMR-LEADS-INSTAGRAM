"""publicidad: cuentas, campañas, conjuntos, anuncios, métricas, checklist, alertas y reportes

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-13 12:00:00

Solo crea tablas nuevas; no toca los datos de leads.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ws() -> sa.Column:
    return sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False)


def _fk(name: str, table: str) -> sa.Column:
    return sa.Column(
        name, sa.Integer(), sa.ForeignKey(f"{table}.id", ondelete="CASCADE"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "ad_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        _ws(),
        sa.Column("meta_account_id", sa.String(40), nullable=False),
        sa.Column("name", sa.String(200)),
        sa.Column("currency", sa.String(10)),
        sa.Column("timezone_name", sa.String(60)),
        sa.Column("account_status", sa.Integer()),
        sa.Column("disable_reason", sa.Integer()),
        sa.Column("is_prepay", sa.Boolean()),
        sa.Column("balance", sa.Float()),
        sa.Column("spend_cap", sa.Float()),
        sa.Column("amount_spent", sa.Float()),
        sa.Column("funding_display", sa.String(200)),
        sa.Column("last_synced_at", sa.DateTime()),
        sa.Column("last_full_resync_at", sa.DateTime()),
        sa.Column("last_sync_error", sa.Text()),
        sa.UniqueConstraint("workspace_id", "meta_account_id"),
    )
    op.create_table(
        "ad_campaigns",
        sa.Column("id", sa.Integer(), primary_key=True),
        _ws(),
        _fk("account_id", "ad_accounts"),
        sa.Column("meta_id", sa.String(40), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("objective", sa.String(60)),
        sa.Column("status", sa.String(30)),
        sa.Column("effective_status", sa.String(30)),
        sa.Column("daily_budget", sa.Float()),
        sa.Column("lifetime_budget", sa.Float()),
        sa.Column("start_time", sa.DateTime()),
        sa.Column("stop_time", sa.DateTime()),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("workspace_id", "meta_id"),
    )
    op.create_index("ix_ad_campaigns_account_id", "ad_campaigns", ["account_id"])
    op.create_table(
        "ad_sets",
        sa.Column("id", sa.Integer(), primary_key=True),
        _ws(),
        _fk("campaign_id", "ad_campaigns"),
        sa.Column("meta_id", sa.String(40), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("status", sa.String(30)),
        sa.Column("effective_status", sa.String(30)),
        sa.Column("optimization_goal", sa.String(60)),
        sa.Column("destination_type", sa.String(60)),
        sa.Column("daily_budget", sa.Float()),
        sa.Column("lifetime_budget", sa.Float()),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("workspace_id", "meta_id"),
    )
    op.create_index("ix_ad_sets_campaign_id", "ad_sets", ["campaign_id"])
    op.create_table(
        "ads",
        sa.Column("id", sa.Integer(), primary_key=True),
        _ws(),
        _fk("campaign_id", "ad_campaigns"),
        _fk("ad_set_id", "ad_sets"),
        sa.Column("meta_id", sa.String(40), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("status", sa.String(30)),
        sa.Column("effective_status", sa.String(30)),
        sa.Column("review_feedback", sa.JSON()),
        sa.Column("thumbnail_url", sa.String(1000)),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("workspace_id", "meta_id"),
    )
    op.create_index("ix_ads_campaign_id", "ads", ["campaign_id"])
    op.create_index("ix_ads_ad_set_id", "ads", ["ad_set_id"])
    op.create_table(
        "ad_daily_metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        _ws(),
        _fk("ad_id", "ads"),
        _fk("ad_set_id", "ad_sets"),
        _fk("campaign_id", "ad_campaigns"),
        sa.Column("day", sa.DateTime(), nullable=False),
        sa.Column("spend", sa.Float(), nullable=False, server_default="0"),
        sa.Column("impressions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reach", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("clicks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("link_clicks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("messages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("actions", sa.JSON()),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("ad_id", "day"),
    )
    op.create_index("ix_ad_daily_metrics_workspace_id", "ad_daily_metrics", ["workspace_id"])
    op.create_index("ix_ad_daily_metrics_day", "ad_daily_metrics", ["day"])
    op.create_index("ix_ad_daily_metrics_campaign_id", "ad_daily_metrics", ["campaign_id"])
    op.create_index("ix_ad_daily_metrics_ad_set_id", "ad_daily_metrics", ["ad_set_id"])
    op.create_table(
        "ad_period_metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        _ws(),
        sa.Column("level", sa.String(20), nullable=False),
        sa.Column("meta_id", sa.String(40), nullable=False),
        sa.Column("since", sa.DateTime(), nullable=False),
        sa.Column("until", sa.DateTime(), nullable=False),
        sa.Column("reach", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("frequency", sa.Float()),
        sa.Column("impressions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("spend", sa.Float(), nullable=False, server_default="0"),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("workspace_id", "level", "meta_id", "since", "until"),
    )
    op.create_table(
        "ad_checklist_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        _ws(),
        sa.Column("key", sa.String(40), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pendiente"),
        sa.Column("notes", sa.Text()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("workspace_id", "key"),
    )
    op.create_table(
        "ad_alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        _ws(),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False, server_default="aviso"),
        sa.Column("dedupe_key", sa.String(120), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="abierta"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime()),
    )
    op.create_index("ix_ad_alerts_workspace_id", "ad_alerts", ["workspace_id"])
    op.create_index("ix_ad_alerts_dedupe_key", "ad_alerts", ["dedupe_key"])
    op.create_index("ix_ad_alerts_status", "ad_alerts", ["status"])
    op.create_table(
        "ad_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        _ws(),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("pdf_path", sa.String(500)),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("workspace_id", "month"),
    )


def downgrade() -> None:
    for table in (
        "ad_reports",
        "ad_alerts",
        "ad_checklist_items",
        "ad_period_metrics",
        "ad_daily_metrics",
        "ads",
        "ad_sets",
        "ad_campaigns",
        "ad_accounts",
    ):
        op.drop_table(table)

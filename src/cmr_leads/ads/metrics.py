"""Totales para el panel y el reporte, a partir de lo guardado en la base."""

from dataclasses import asdict, dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cmr_leads.ads.sync import day
from cmr_leads.db.models import Ad, AdCampaign, AdDailyMetric, AdPeriodMetric, AdSet


@dataclass
class Totals:
    spend: float = 0.0
    impressions: int = 0
    clicks: int = 0
    link_clicks: int = 0
    messages: int = 0
    reach: int | None = None  # None = sin dato exacto para ese período
    frequency: float | None = None

    @property
    def cost_per_message(self) -> float | None:
        return self.spend / self.messages if self.messages else None

    @property
    def ctr(self) -> float | None:
        return self.link_clicks / self.impressions if self.impressions else None

    @property
    def cpm(self) -> float | None:
        return self.spend / self.impressions * 1000 if self.impressions else None

    def as_dict(self) -> dict:
        data = asdict(self)
        data.update(cost_per_message=self.cost_per_message, ctr=self.ctr, cpm=self.cpm)
        return data


@dataclass
class Row:
    """Una fila del panel: campaña, conjunto o anuncio con sus totales del período."""

    id: int
    meta_id: str
    name: str
    status: str | None
    parent: str | None
    totals: Totals
    budget: float | None = None
    thumbnail_url: str | None = None


LEVELS = {
    "campanas": (AdCampaign, AdDailyMetric.campaign_id, "campaign"),
    "conjuntos": (AdSet, AdDailyMetric.ad_set_id, "adset"),
    "anuncios": (Ad, AdDailyMetric.ad_id, "ad"),
}
_SUMS = (
    func.coalesce(func.sum(AdDailyMetric.spend), 0),
    func.coalesce(func.sum(AdDailyMetric.impressions), 0),
    func.coalesce(func.sum(AdDailyMetric.clicks), 0),
    func.coalesce(func.sum(AdDailyMetric.link_clicks), 0),
    func.coalesce(func.sum(AdDailyMetric.messages), 0),
)


def _range(query, since: date, until: date):
    return query.where(AdDailyMetric.day >= day(since), AdDailyMetric.day <= day(until))


def period_reach(
    session: Session, workspace_id: int, level: str, meta_id: str, since: date, until: date
) -> AdPeriodMetric | None:
    return session.scalar(
        select(AdPeriodMetric).where(
            AdPeriodMetric.workspace_id == workspace_id,
            AdPeriodMetric.level == level,
            AdPeriodMetric.meta_id == meta_id,
            AdPeriodMetric.since == day(since),
            AdPeriodMetric.until == day(until),
        )
    )


def account_totals(
    session: Session, workspace_id: int, since: date, until: date, account_meta_id: str | None
) -> Totals:
    query = _range(select(*_SUMS).where(AdDailyMetric.workspace_id == workspace_id), since, until)
    spend, impressions, clicks, link_clicks, messages = session.execute(query).one()
    totals = Totals(float(spend), int(impressions), int(clicks), int(link_clicks), int(messages))
    if account_meta_id:
        period = period_reach(session, workspace_id, "account", account_meta_id, since, until)
        if period:
            totals.reach, totals.frequency = period.reach, period.frequency
    return totals


def level_rows(
    session: Session,
    workspace_id: int,
    level: str,
    since: date,
    until: date,
    campaign_id: int | None = None,
    live_reach: dict[str, int] | None = None,
) -> list[Row]:
    """Filas de campañas, conjuntos o anuncios con sus totales, de mayor a menor inversión."""
    model, metric_key, period_level = LEVELS[level]
    sums = {
        key: values
        for key, *values in session.execute(
            _range(
                select(metric_key, *_SUMS)
                .where(AdDailyMetric.workspace_id == workspace_id)
                .group_by(metric_key),
                since,
                until,
            )
        )
    }
    query = select(model).where(model.workspace_id == workspace_id)
    if campaign_id:
        query = query.where((model.id if model is AdCampaign else model.campaign_id) == campaign_id)
    campaigns = {
        c.id: c.name
        for c in session.scalars(select(AdCampaign).where(AdCampaign.workspace_id == workspace_id))
    }
    periods = {
        p.meta_id: p
        for p in session.scalars(
            select(AdPeriodMetric).where(
                AdPeriodMetric.workspace_id == workspace_id,
                AdPeriodMetric.level == period_level,
                AdPeriodMetric.since == day(since),
                AdPeriodMetric.until == day(until),
            )
        )
    }
    rows = []
    for item in session.scalars(query):
        spend, impressions, clicks, link_clicks, messages = sums.get(item.id, (0, 0, 0, 0, 0))
        totals = Totals(
            float(spend), int(impressions), int(clicks), int(link_clicks), int(messages)
        )
        if item.meta_id in periods:
            totals.reach = periods[item.meta_id].reach
            totals.frequency = periods[item.meta_id].frequency
        elif live_reach and item.meta_id in live_reach:
            totals.reach = live_reach[item.meta_id]
        is_active = item.effective_status not in (None, "ARCHIVED", "DELETED")
        if not (totals.spend or totals.impressions) and not is_active:
            continue  # archivados sin movimiento en el período: no suman nada
        rows.append(
            Row(
                id=item.id,
                meta_id=item.meta_id,
                name=item.name,
                status=item.effective_status,
                parent=None if model is AdCampaign else campaigns.get(item.campaign_id),
                totals=totals,
                budget=getattr(item, "daily_budget", None),
                thumbnail_url=getattr(item, "thumbnail_url", None),
            )
        )
    rows.sort(key=lambda r: (-r.totals.spend, r.name))
    return rows


def daily_series(session: Session, workspace_id: int, since: date, until: date) -> list[dict]:
    query = _range(
        select(AdDailyMetric.day, *_SUMS)
        .where(AdDailyMetric.workspace_id == workspace_id)
        .group_by(AdDailyMetric.day)
        .order_by(AdDailyMetric.day),
        since,
        until,
    )
    return [
        {"day": d.date() if isinstance(d, datetime) else d, "spend": float(s), "messages": int(m)}
        for d, s, _i, _c, _l, m in session.execute(query)
    ]


def by_month(series: list[dict]) -> list[dict]:
    """Agrupa la serie diaria por mes (para períodos largos, donde las barras diarias no se leen)."""
    months: dict[date, dict] = {}
    for d in series:
        key = d["day"].replace(day=1)
        row = months.setdefault(key, {"day": key, "spend": 0.0, "messages": 0})
        row["spend"] += d["spend"]
        row["messages"] += d["messages"]
    return list(months.values())


def pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or not previous:
        return None
    return (current - previous) / previous

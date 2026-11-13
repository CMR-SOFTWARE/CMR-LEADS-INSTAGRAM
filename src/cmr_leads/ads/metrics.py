"""Totales para el panel y el reporte, a partir de lo guardado en la base."""

from dataclasses import asdict, dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cmr_leads.ads.objectives import OBJECTIVES, cost_per_result, objective_key, result_of
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
    engagement: int = 0  # interacciones con la publicación (reacciones, guardados, clics, etc.)
    video_views: int = 0

    @property
    def cost_per_thousand_reach(self) -> float | None:
        return self.spend / self.reach * 1000 if self.reach and self.spend else None

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
        data.update(
            cost_per_message=self.cost_per_message,
            ctr=self.ctr,
            cpm=self.cpm,
            cost_per_thousand_reach=self.cost_per_thousand_reach,
        )
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
    objective: str = "alcance"

    @property
    def goal(self):
        return OBJECTIVES[self.objective]

    @property
    def result(self) -> int | None:
        return result_of(self.objective, self.totals)

    @property
    def cost_per_result(self) -> float | None:
        return cost_per_result(self.objective, self.totals)


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


ENGAGEMENT_ACTIONS = ("post_engagement",)
VIDEO_ACTIONS = ("video_view",)


def _range(query, since: date, until: date):
    return query.where(AdDailyMetric.day >= day(since), AdDailyMetric.day <= day(until))


def _count(actions, types) -> int:
    total = 0
    for a in actions or []:
        if a.get("action_type") in types:
            try:
                total += int(float(a.get("value") or 0))
            except (TypeError, ValueError):
                continue
    return total


def action_sums(session: Session, workspace_id: int, key_column, since: date, until: date) -> dict:
    """Interacciones y reproducciones (vienen dentro de `actions`) sumadas por clave."""
    sums: dict = {}
    query = _range(
        select(key_column, AdDailyMetric.actions).where(AdDailyMetric.workspace_id == workspace_id),
        since,
        until,
    )
    for key, actions in session.execute(query):
        engagement, video = sums.get(key, (0, 0))
        sums[key] = (
            engagement + _count(actions, ENGAGEMENT_ACTIONS),
            video + _count(actions, VIDEO_ACTIONS),
        )
    return sums


def objectives_by_level(session: Session, workspace_id: int, level: str) -> dict[int, str]:
    """Objetivo de cada campaña, conjunto o anuncio (sale del conjunto y de su campaña)."""
    campaigns = {
        c.id: c.objective
        for c in session.scalars(select(AdCampaign).where(AdCampaign.workspace_id == workspace_id))
    }
    sets = list(session.scalars(select(AdSet).where(AdSet.workspace_id == workspace_id)))
    by_set = {
        s.id: objective_key(campaigns.get(s.campaign_id), s.optimization_goal, s.destination_type)
        for s in sets
    }
    if level == "conjuntos":
        return by_set
    if level == "anuncios":
        return {
            a.id: by_set.get(a.ad_set_id, "alcance")
            for a in session.scalars(select(Ad).where(Ad.workspace_id == workspace_id))
        }
    by_campaign: dict[int, str] = {}
    for s in sets:  # el primer conjunto define el objetivo; si no hay, el de la campaña
        by_campaign.setdefault(s.campaign_id, by_set[s.id])
    for cid, objective in campaigns.items():
        by_campaign.setdefault(cid, objective_key(objective, None, None))
    return by_campaign


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
    extra = action_sums(session, workspace_id, AdDailyMetric.workspace_id, since, until)
    totals.engagement, totals.video_views = extra.get(workspace_id, (0, 0))
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
    extra = action_sums(session, workspace_id, metric_key, since, until)
    objectives = objectives_by_level(session, workspace_id, level)
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
        totals.engagement, totals.video_views = extra.get(item.id, (0, 0))
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
                objective=objectives.get(item.id, "alcance"),
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
    extra = action_sums(session, workspace_id, AdDailyMetric.day, since, until)
    reach = dict(
        session.execute(
            _range(
                select(AdDailyMetric.day, func.coalesce(func.sum(AdDailyMetric.reach), 0))
                .where(AdDailyMetric.workspace_id == workspace_id)
                .group_by(AdDailyMetric.day),
                since,
                until,
            )
        ).all()
    )
    return [
        {
            "day": d.date() if isinstance(d, datetime) else d,
            "spend": float(s),
            "messages": int(m),
            "reach": int(reach.get(d) or 0),
            "engagement": extra.get(d, (0, 0))[0],
        }
        for d, s, _i, _c, _l, m in session.execute(query)
    ]


def by_month(series: list[dict]) -> list[dict]:
    """Agrupa la serie diaria por mes (para períodos largos, donde las barras diarias no se leen)."""
    months: dict[date, dict] = {}
    for d in series:
        key = d["day"].replace(day=1)
        row = months.setdefault(
            key, {"day": key, "spend": 0.0, "messages": 0, "reach": 0, "engagement": 0}
        )
        for field in ("spend", "messages", "reach", "engagement"):
            row[field] += d.get(field, 0)
    return list(months.values())


def pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or not previous:
        return None
    return (current - previous) / previous

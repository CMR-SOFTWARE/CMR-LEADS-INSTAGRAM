"""Trae de Meta la cuenta, la estructura (campañas, conjuntos, anuncios) y las métricas.

Meta re-atribuye resultados hasta 28 días después, por eso cada sincronización vuelve a pedir
los últimos 7 días (y una vez por semana los últimos 28) y pisa lo que ya estaba guardado.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cmr_leads.ads.client import AdsApiError, AdsClient
from cmr_leads.db.models import (
    Ad,
    AdAccount,
    AdCampaign,
    AdDailyMetric,
    AdPeriodMetric,
    AdSet,
    utcnow,
)

# Conversaciones iniciadas por WhatsApp, Messenger o Instagram desde el anuncio.
MESSAGE_ACTIONS = ("onsite_conversion.messaging_conversation_started_7d",)
ACCOUNT_FIELDS = (
    "name,currency,timezone_name,account_status,disable_reason,"
    "balance,spend_cap,amount_spent,is_prepay_account"
)
CAMPAIGN_FIELDS = (
    "id,name,objective,status,effective_status,daily_budget,lifetime_budget,start_time,stop_time"
)
ADSET_FIELDS = (
    "id,name,campaign_id,status,effective_status,optimization_goal,destination_type,"
    "daily_budget,lifetime_budget"
)
AD_FIELDS = (
    "id,name,campaign_id,adset_id,status,effective_status,ad_review_feedback,"
    "creative{thumbnail_url}"
)
INSIGHT_FIELDS = (
    "ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,"
    "spend,impressions,reach,clicks,inline_link_clicks,actions"
)
PERIOD_LEVELS = {"account": "account_id", "campaign": "campaign_id", "ad": "ad_id"}
FULL_RESYNC_DAYS = 28
RECENT_RESYNC_DAYS = 7
FULL_RESYNC_EVERY = timedelta(days=7)


@dataclass
class AdsSyncResult:
    campaigns: int = 0
    ad_sets: int = 0
    ads: int = 0
    metric_rows: int = 0
    periods: int = 0
    since: date | None = None
    until: date | None = None
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        text = (
            f"Publicidad actualizada: {self.campaigns} campañas, {self.ads} anuncios y "
            f"{self.metric_rows} filas de métricas"
        )
        if self.since:
            text += f" (del {self.since:%d/%m} al {self.until:%d/%m})"
        if self.warnings:
            text += ". Avisos: " + "; ".join(self.warnings)
        return text


# ---------------- Conversiones ----------------


def money(value: Any) -> float | None:
    """Saldo, límite y gastado de la cuenta y presupuestos vienen en centavos."""
    if value in (None, ""):
        return None
    try:
        return int(value) / 100
    except (TypeError, ValueError):
        return None


def decimal(value: Any) -> float:
    """La inversión de /insights viene en pesos con decimales ("1234.56")."""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def integer(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def meta_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def day(value: date) -> datetime:
    return datetime(value.year, value.month, value.day)


def count_messages(actions: list[dict] | None) -> int:
    return sum(
        integer(a.get("value")) for a in actions or [] if a.get("action_type") in MESSAGE_ACTIONS
    )


def month_bounds(value: date) -> tuple[date, date]:
    first = value.replace(day=1)
    next_first = (first + timedelta(days=32)).replace(day=1)
    return first, next_first - timedelta(days=1)


def standard_periods(today: date) -> dict[str, tuple[date, date]]:
    """Períodos para los que se guarda el alcance real (no sumable día a día)."""
    month_start, _ = month_bounds(today)
    prev_start, prev_end = month_bounds(month_start - timedelta(days=1))
    return {
        "mes": (month_start, today),
        "mes_anterior": (prev_start, prev_end),
        "7d": (today - timedelta(days=6), today),
        "30d": (today - timedelta(days=29), today),
        "anio": (today.replace(month=1, day=1), today),
    }


def history_start(today: date) -> date:
    """Desde dónde tiene que haber historial: el 1 de enero (o el mes anterior, en enero)."""
    prev_start, _ = month_bounds(today.replace(day=1) - timedelta(days=1))
    return min(today.replace(month=1, day=1), prev_start)


# ---------------- Sincronización ----------------


def ensure_account(session: Session, workspace_id: int, meta_account_id: str) -> AdAccount:
    account = session.scalar(
        select(AdAccount).where(
            AdAccount.workspace_id == workspace_id, AdAccount.meta_account_id == meta_account_id
        )
    )
    if account is None:
        account = AdAccount(workspace_id=workspace_id, meta_account_id=meta_account_id)
        session.add(account)
        session.flush()
    return account


def sync_account_info(client: AdsClient, account: AdAccount, result: AdsSyncResult) -> None:
    info = client.get(account.meta_account_id, fields=ACCOUNT_FIELDS)
    account.name = info.get("name")
    account.currency = info.get("currency")
    account.timezone_name = info.get("timezone_name")
    account.account_status = info.get("account_status")
    account.disable_reason = info.get("disable_reason")
    account.is_prepay = info.get("is_prepay_account")
    account.balance = money(info.get("balance"))
    account.spend_cap = money(info.get("spend_cap")) or None  # 0 = sin límite
    account.amount_spent = money(info.get("amount_spent"))
    # El saldo de cuentas prepagas solo aparece como texto; puede requerir más permisos.
    try:
        funding = client.get(account.meta_account_id, fields="funding_source_details")
        account.funding_display = (funding.get("funding_source_details") or {}).get(
            "display_string"
        )
    except AdsApiError:
        result.warnings.append("no se pudo leer el método de pago")


def _upsert(session: Session, model, workspace_id: int, meta_id: str, cache: dict, **values):
    row = cache.get(meta_id)
    if row is None:
        row = model(workspace_id=workspace_id, meta_id=meta_id, **values)
        session.add(row)
        cache[meta_id] = row
    else:
        for key, value in values.items():
            setattr(row, key, value)
    return row


def _cache(session: Session, model, workspace_id: int) -> dict:
    return {
        r.meta_id: r
        for r in session.scalars(select(model).where(model.workspace_id == workspace_id))
    }


def sync_structure(
    session: Session, client: AdsClient, account: AdAccount, result: AdsSyncResult
) -> tuple[dict, dict, dict]:
    ws = account.workspace_id
    campaigns = _cache(session, AdCampaign, ws)
    ad_sets = _cache(session, AdSet, ws)
    ads = _cache(session, Ad, ws)

    for c in client.get_all(
        f"{account.meta_account_id}/campaigns", fields=CAMPAIGN_FIELDS, limit=200
    ):
        _upsert(
            session,
            AdCampaign,
            ws,
            c["id"],
            campaigns,
            account_id=account.id,
            name=c.get("name") or c["id"],
            objective=c.get("objective"),
            status=c.get("status"),
            effective_status=c.get("effective_status"),
            daily_budget=money(c.get("daily_budget")),
            lifetime_budget=money(c.get("lifetime_budget")),
            start_time=meta_time(c.get("start_time")),
            stop_time=meta_time(c.get("stop_time")),
        )
        result.campaigns += 1
    session.flush()

    for s in client.get_all(f"{account.meta_account_id}/adsets", fields=ADSET_FIELDS, limit=200):
        campaign = campaigns.get(s.get("campaign_id"))
        if campaign is None:
            continue
        _upsert(
            session,
            AdSet,
            ws,
            s["id"],
            ad_sets,
            campaign_id=campaign.id,
            name=s.get("name") or s["id"],
            status=s.get("status"),
            effective_status=s.get("effective_status"),
            optimization_goal=s.get("optimization_goal"),
            destination_type=s.get("destination_type"),
            daily_budget=money(s.get("daily_budget")),
            lifetime_budget=money(s.get("lifetime_budget")),
        )
        result.ad_sets += 1
    session.flush()

    for a in client.get_all(f"{account.meta_account_id}/ads", fields=AD_FIELDS, limit=200):
        ad_set = ad_sets.get(a.get("adset_id"))
        campaign = campaigns.get(a.get("campaign_id"))
        if ad_set is None or campaign is None:
            continue
        _upsert(
            session,
            Ad,
            ws,
            a["id"],
            ads,
            campaign_id=campaign.id,
            ad_set_id=ad_set.id,
            name=a.get("name") or a["id"],
            status=a.get("status"),
            effective_status=a.get("effective_status"),
            review_feedback=a.get("ad_review_feedback"),
            thumbnail_url=(a.get("creative") or {}).get("thumbnail_url"),
        )
        result.ads += 1
    session.flush()
    return campaigns, ad_sets, ads


def metrics_range(session: Session, account: AdAccount, today: date) -> tuple[date, date, bool]:
    """Desde qué día pedir métricas. Devuelve (desde, hasta, es_resincronizacion_completa)."""
    start = history_start(today)
    if account.history_since is None or account.history_since.date() > start:
        # Primera vez (o falta historia): desde el 1 de enero, para "Este año" y comparar.
        return start, today, True
    full = (
        account.last_full_resync_at is None
        or utcnow() - account.last_full_resync_at >= FULL_RESYNC_EVERY
    )
    days = FULL_RESYNC_DAYS if full else RECENT_RESYNC_DAYS
    return today - timedelta(days=days - 1), today, full


def _stub_structure(session, account, row, campaigns, ad_sets, ads) -> Ad:
    """Un anuncio con métricas que ya no aparece en el listado (borrado o archivado)."""
    ws = account.workspace_id
    campaign = campaigns.get(row["campaign_id"]) or _upsert(
        session,
        AdCampaign,
        ws,
        row["campaign_id"],
        campaigns,
        account_id=account.id,
        name=row.get("campaign_name") or row["campaign_id"],
        effective_status="ARCHIVED",
    )
    session.flush()
    ad_set = ad_sets.get(row["adset_id"]) or _upsert(
        session,
        AdSet,
        ws,
        row["adset_id"],
        ad_sets,
        campaign_id=campaign.id,
        name=row.get("adset_name") or row["adset_id"],
        effective_status="ARCHIVED",
    )
    session.flush()
    ad = _upsert(
        session,
        Ad,
        ws,
        row["ad_id"],
        ads,
        campaign_id=campaign.id,
        ad_set_id=ad_set.id,
        name=row.get("ad_name") or row["ad_id"],
        effective_status="ARCHIVED",
    )
    session.flush()
    return ad


def sync_daily_metrics(
    session: Session,
    client: AdsClient,
    account: AdAccount,
    since: date,
    until: date,
    structure: tuple[dict, dict, dict],
    result: AdsSyncResult,
) -> None:
    campaigns, ad_sets, ads = structure
    existing = {
        (m.ad_id, m.day): m
        for m in session.scalars(
            select(AdDailyMetric).where(
                AdDailyMetric.workspace_id == account.workspace_id,
                AdDailyMetric.day >= day(since),
                AdDailyMetric.day <= day(until),
            )
        )
    }
    rows = client.get_all(
        f"{account.meta_account_id}/insights",
        level="ad",
        time_increment=1,
        time_range={"since": since.isoformat(), "until": until.isoformat()},
        fields=INSIGHT_FIELDS,
        limit=500,
    )
    for row in rows:
        ad = ads.get(row.get("ad_id"))
        if ad is None:
            if not (row.get("ad_id") and row.get("adset_id") and row.get("campaign_id")):
                continue
            ad = _stub_structure(session, account, row, campaigns, ad_sets, ads)
        when = datetime.strptime(row["date_start"], "%Y-%m-%d")
        values = dict(
            spend=decimal(row.get("spend")),
            impressions=integer(row.get("impressions")),
            reach=integer(row.get("reach")),
            clicks=integer(row.get("clicks")),
            link_clicks=integer(row.get("inline_link_clicks")),
            messages=count_messages(row.get("actions")),
            actions=row.get("actions"),
        )
        metric = existing.get((ad.id, when))
        if metric is None:
            metric = AdDailyMetric(
                workspace_id=account.workspace_id,
                ad_id=ad.id,
                ad_set_id=ad.ad_set_id,
                campaign_id=ad.campaign_id,
                day=when,
                **values,
            )
            session.add(metric)
            existing[(ad.id, when)] = metric
        else:
            for key, value in values.items():
                setattr(metric, key, value)
        result.metric_rows += 1
    session.flush()


def fetch_period(
    client: AdsClient, account_meta_id: str, level: str, since: date, until: date
) -> list[dict]:
    """Alcance real de un período, por cuenta, campaña o anuncio."""
    id_field = PERIOD_LEVELS[level]
    rows = client.get_all(
        f"{account_meta_id}/insights",
        level=level,
        time_range={"since": since.isoformat(), "until": until.isoformat()},
        fields=f"{id_field},spend,impressions,reach,frequency",
        limit=500,
    )
    out = []
    for row in rows:
        meta_id = row.get(id_field) or (account_meta_id if level == "account" else None)
        if not meta_id:
            continue
        if level == "account" and not str(meta_id).startswith("act_"):
            meta_id = f"act_{meta_id}"
        out.append(
            {
                "meta_id": meta_id,
                "reach": integer(row.get("reach")),
                "frequency": decimal(row.get("frequency")) or None,
                "impressions": integer(row.get("impressions")),
                "spend": decimal(row.get("spend")),
            }
        )
    return out


def sync_periods(
    session: Session, client: AdsClient, account: AdAccount, today: date, result: AdsSyncResult
) -> None:
    ws = account.workspace_id
    for since, until in standard_periods(today).values():
        existing = {
            (p.level, p.meta_id): p
            for p in session.scalars(
                select(AdPeriodMetric).where(
                    AdPeriodMetric.workspace_id == ws,
                    AdPeriodMetric.since == day(since),
                    AdPeriodMetric.until == day(until),
                )
            )
        }
        for level in PERIOD_LEVELS:
            for values in fetch_period(client, account.meta_account_id, level, since, until):
                key = (level, values.pop("meta_id"))
                row = existing.get(key)
                if row is None:
                    row = AdPeriodMetric(
                        workspace_id=ws,
                        level=key[0],
                        meta_id=key[1],
                        since=day(since),
                        until=day(until),
                    )
                    session.add(row)
                    existing[key] = row
                for k, v in values.items():
                    setattr(row, k, v)
                result.periods += 1
    session.flush()


def sync_ads(
    session: Session,
    workspace_id: int,
    client: AdsClient,
    meta_account_id: str,
    today: date,
) -> AdsSyncResult:
    # Se guarda por etapas: SQLite bloquea toda la base mientras hay escrituras sin confirmar, y
    # las consultas a Meta tardan. Los upserts permiten repetir una etapa si algo falla después.
    result = AdsSyncResult()
    account = ensure_account(session, workspace_id, meta_account_id)
    session.commit()
    sync_account_info(client, account, result)
    session.commit()
    structure = sync_structure(session, client, account, result)
    session.commit()
    since, until, full = metrics_range(session, account, today)
    result.since, result.until = since, until
    sync_daily_metrics(session, client, account, since, until, structure, result)
    if account.history_since is None or account.history_since.date() > since:
        account.history_since = day(since)
    session.commit()
    sync_periods(session, client, account, today, result)
    account.last_synced_at = utcnow()
    account.last_sync_error = None
    if full:
        account.last_full_resync_at = utcnow()
    session.flush()
    return result

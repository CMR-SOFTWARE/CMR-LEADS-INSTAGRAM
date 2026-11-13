"""Orquesta la sincronización, las alertas y el reporte mensual automático."""

import logging
import threading
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from cmr_leads.ads.alerts import evaluate_alerts, record_sync_error
from cmr_leads.ads.client import AdsApiError, AdsClient
from cmr_leads.ads.pdf import render_report_pdf
from cmr_leads.ads.report import build_report_data, previous_month, save_report
from cmr_leads.ads.sync import (
    AdsSyncResult,
    day,
    ensure_account,
    fetch_period,
    month_bounds,
    sync_ads,
)
from cmr_leads.db.models import AdAccount, AdChecklistItem, AdPeriodMetric, AdReport
from cmr_leads.instagram.sync import BudgetExceeded, daily_budget_guard
from cmr_leads.pipeline.budget import DailyBudget
from cmr_leads.settings import Settings

log = logging.getLogger(__name__)

# Una sola sincronización a la vez: la automática y el botón no deben pisarse.
_SYNC_LOCK = threading.Lock()
SYNC_BUSY = "Ya se está actualizando desde Meta. Esperá un minuto y recargá la página"

CHECKLIST = (
    (
        "business_manager",
        "Business Manager ordenado",
        "Admins con verificación en dos pasos, al menos 2 administradores.",
    ),
    (
        "pagina_instagram",
        "Página e Instagram conectados",
        "Instagram profesional vinculado a la página desde el Business Manager. WhatsApp Business "
        "vinculado a la página.",
    ),
    (
        "cuenta_pago",
        "Cuenta publicitaria y método de pago",
        "Moneda ARS, zona horaria Buenos Aires y límite de gasto configurado.",
    ),
    (
        "dominio",
        "Dominio verificado",
        "cmrsoftwaresolutions.com, cmrnexo.com y cmrcanchas.com verificados.",
    ),
    (
        "pixel_eventos",
        "Píxel instalado y eventos funcionando",
        "PageView, ViewContent y Contact (clic en WhatsApp) probados con Pixel Helper.",
    ),
    ("catalogo", "Catálogo de productos conectado", "Solo si se van a usar anuncios de catálogo."),
)
CHECKLIST_STATUSES = {"pendiente": "Pendiente", "en_curso": "En curso", "listo": "Listo"}


def ensure_checklist(session: Session, workspace_id: int) -> list[AdChecklistItem]:
    items = {
        i.key: i
        for i in session.scalars(
            select(AdChecklistItem).where(AdChecklistItem.workspace_id == workspace_id)
        )
    }
    for order, (key, title, hint) in enumerate(CHECKLIST, 1):
        if key not in items:
            items[key] = AdChecklistItem(
                workspace_id=workspace_id, key=key, title=title, notes=hint, sort_order=order
            )
            session.add(items[key])
    session.flush()
    return sorted(items.values(), key=lambda i: i.sort_order)


def build_client(session: Session, workspace_id: int, settings: Settings) -> AdsClient:
    budget = DailyBudget(session, workspace_id, "meta_ads", settings.meta_ads_max_requests_per_day)
    return AdsClient(
        settings.meta_ads_access_token,
        settings.meta_ads_graph_api_version,
        on_request=daily_budget_guard(budget),
    )


def get_account(session: Session, workspace_id: int, settings: Settings) -> AdAccount | None:
    if not settings.ad_account_id:
        return None
    return session.scalar(
        select(AdAccount).where(
            AdAccount.workspace_id == workspace_id,
            AdAccount.meta_account_id == settings.ad_account_id,
        )
    )


def ensure_month_reach(
    session: Session, workspace_id: int, client: AdsClient, account_meta_id: str, month: date
) -> None:
    """El alcance del mes no se puede sumar: si no está guardado, se pide a Meta."""
    since, until = month_bounds(month)
    for level in ("account", "campaign", "ad"):
        exists = session.scalar(
            select(AdPeriodMetric.id)
            .where(
                AdPeriodMetric.workspace_id == workspace_id,
                AdPeriodMetric.level == level,
                AdPeriodMetric.since == day(since),
                AdPeriodMetric.until == day(until),
            )
            .limit(1)
        )
        if exists:
            continue
        for values in fetch_period(client, account_meta_id, level, since, until):
            session.add(
                AdPeriodMetric(
                    workspace_id=workspace_id,
                    level=level,
                    meta_id=values.pop("meta_id"),
                    since=day(since),
                    until=day(until),
                    **values,
                )
            )
    session.flush()


def generate_report(
    session: Session,
    workspace_id: int,
    settings: Settings,
    month: date,
    client: AdsClient | None = None,
) -> AdReport:
    if client and settings.ad_account_id:
        try:
            ensure_month_reach(session, workspace_id, client, settings.ad_account_id, month)
        except (AdsApiError, BudgetExceeded) as exc:
            log.warning("No se pudo traer el alcance del mes: %s", exc)
    data = build_report_data(session, workspace_id, settings.ad_account_id, month)
    return save_report(
        session,
        workspace_id,
        data,
        settings.meta_ads_reports_dir,
        lambda d, p: render_report_pdf(d, p, settings.company_name),
    )


def run_ads_sync(
    session: Session,
    workspace_id: int,
    settings: Settings,
    today: date | None = None,
    client: AdsClient | None = None,
) -> str:
    """Sincroniza, evalúa alertas y, si falta, genera el reporte del mes anterior."""
    if not settings.ads_configured:
        return "Falta configurar META_ADS_ACCESS_TOKEN y META_AD_ACCOUNT_ID en .env"
    if not _SYNC_LOCK.acquire(blocking=False):
        return SYNC_BUSY
    try:
        return _run_ads_sync(session, workspace_id, settings, today, client)
    finally:
        _SYNC_LOCK.release()


def _run_ads_sync(
    session: Session,
    workspace_id: int,
    settings: Settings,
    today: date | None,
    client: AdsClient | None,
) -> str:
    today = today or date.today()
    client = client or build_client(session, workspace_id, settings)
    try:
        result: AdsSyncResult = sync_ads(
            session, workspace_id, client, settings.ad_account_id, today
        )
    except (AdsApiError, BudgetExceeded) as exc:
        session.rollback()
        account = ensure_account(session, workspace_id, settings.ad_account_id)
        account.last_sync_error = str(exc)
        record_sync_error(session, workspace_id, str(exc))
        session.commit()
        return f"No se pudo actualizar la publicidad: {exc}"
    account = ensure_account(session, workspace_id, settings.ad_account_id)
    new_alerts = evaluate_alerts(
        session,
        workspace_id,
        account,
        today,
        settings.meta_ads_cost_alert_pct,
        settings.meta_ads_low_balance_ars,
    )
    message = result.summary()
    if new_alerts:
        message += f". {len(new_alerts)} alerta(s) nueva(s)"
    month = previous_month(today)
    has_report = session.scalar(
        select(AdReport.id).where(
            AdReport.workspace_id == workspace_id, AdReport.month == f"{month:%Y-%m}"
        )
    )
    # Desde el día 2, para que Meta ya haya cerrado los números del último día del mes.
    if not has_report and today.day >= 2:
        try:
            generate_report(session, workspace_id, settings, month, client)
            message += f". Reporte de {month:%m/%Y} generado"
        except Exception:  # noqa: BLE001 - el reporte no debe frenar la sincronización
            log.exception("No se pudo generar el reporte mensual automático")
    session.commit()
    return message

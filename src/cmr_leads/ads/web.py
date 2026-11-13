"""Sección "Publicidad" del panel: campañas, alertas, reportes y checklist de configuración."""

import logging
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from sqlalchemy import func, select

from cmr_leads.ads.alerts import ars
from cmr_leads.ads.client import AdsApiError
from cmr_leads.ads.metrics import (
    LEVELS,
    account_totals,
    by_month,
    daily_series,
    level_rows,
    pct_change,
)
from cmr_leads.ads.report import month_label, parse_month, previous_month
from cmr_leads.ads.service import (
    CHECKLIST_STATUSES,
    build_client,
    ensure_checklist,
    generate_report,
    get_account,
    run_ads_sync,
)
from cmr_leads.ads.sync import fetch_period, standard_periods
from cmr_leads.advice import get_advice, request_advice
from cmr_leads.db.models import AdAlert, AdChecklistItem, AdReport, utcnow
from cmr_leads.instagram.sync import BudgetExceeded
from cmr_leads.messaging.ai_writer import AiWriterError
from cmr_leads.settings import Settings
from cmr_leads.web.charts import chart, dataset

log = logging.getLogger(__name__)

PERIODS = {
    "mes": "Este mes",
    "mes_anterior": "Mes anterior",
    "7d": "Últimos 7 días",
    "30d": "Últimos 30 días",
    "anio": "Este año",
    "rango": "Elegir fechas",
}
LEVEL_LABELS = {"campanas": "Campañas", "conjuntos": "Conjuntos", "anuncios": "Anuncios"}
STATUS_LABELS = {
    "ACTIVE": ("Activa", "green"),
    "PAUSED": ("Pausada", ""),
    "CAMPAIGN_PAUSED": ("Campaña pausada", ""),
    "ADSET_PAUSED": ("Conjunto pausado", ""),
    "DISAPPROVED": ("Rechazado", "red"),
    "WITH_ISSUES": ("Con problemas", "amber"),
    "PENDING_REVIEW": ("En revisión", "amber"),
    "IN_PROCESS": ("En proceso", "amber"),
    "PENDING_BILLING_INFO": ("Falta pago", "red"),
    "ARCHIVED": ("Archivada", ""),
    "DELETED": ("Borrada", ""),
}
ACCOUNT_STATUS = {
    1: "Activa",
    2: "Deshabilitada",
    3: "Pago pendiente",
    7: "En revisión",
    8: "Pago pendiente",
    9: "Período de gracia",
    100: "Por cerrarse",
    101: "Cerrada",
}
LIVE_REACH_TTL = 600


def fmt_int(value) -> str:
    return "s/d" if value is None else f"{int(value):,}".replace(",", ".")


def fmt_money(value) -> str:
    return "s/d" if value is None else ars(value)


def fmt_ratio(value) -> str:
    return "s/d" if value is None else f"{value * 100:.1f}%".replace(".", ",")


def resolve_period(periodo: str, desde: str, hasta: str, today: date) -> tuple[str, date, date]:
    periods = standard_periods(today)
    if periodo == "rango":
        try:
            since, until = date.fromisoformat(desde), date.fromisoformat(hasta)
        except ValueError:
            since, until = periods["30d"]
        if since > until:
            since, until = until, since
        return "rango", since, min(until, today)
    key = periodo if periodo in periods else "mes"
    return key, *periods[key]


def ads_charts(series: list[dict], monthly: bool, campaigns: list, rows: list) -> dict:
    """Gráficos del resumen: inversión y mensajes en el tiempo, reparto y comparación por fila."""
    fmt = "%m/%Y" if monthly else "%d/%m"
    charts = {
        "ad-series": chart(
            "ad-series",
            "bar",
            [d["day"].strftime(fmt) for d in series],
            [
                dataset("Inversión", [round(d["spend"]) for d in series], "azul", fmt="money"),
                dataset("Mensajes", [d["messages"] for d in series], "turquesa", "line", "y1"),
            ],
        )
    }
    spent = [c for c in campaigns if c.totals.spend > 0]
    if spent:
        charts["ad-split"] = chart(
            "ad-split",
            "doughnut",
            [_short(c.name) for c in spent],
            [dataset("Inversión", [round(c.totals.spend) for c in spent], fmt="money")],
        )
    with_spend = [r for r in rows if r.totals.spend > 0]
    if with_spend:
        charts["ad-cost"] = chart(
            "ad-cost",
            "bar",
            [_short(r.name) for r in with_spend],
            [
                dataset(
                    "Inversión", [round(r.totals.spend) for r in with_spend], "azul", fmt="money"
                ),
                dataset("Mensajes", [r.totals.messages for r in with_spend], "turquesa", axis="y1"),
            ],
        )
    return charts


def _short(name: str, size: int = 32) -> str:
    return name if len(name) <= size else name[: size - 1] + "…"


def _rounded(values: dict) -> dict:
    return {k: (round(v) if isinstance(v, float) else v) for k, v in values.items()}


def ads_facts(totals, previous, changes, campaigns, ads, organic, since, until) -> dict:
    """Números para el análisis de dónde invertir (sin datos personales)."""

    def row(r):
        t = r.totals
        return {
            "nombre": r.name,
            "campana": r.parent,
            "estado": r.status,
            "inversion": round(t.spend),
            "alcance": t.reach,
            "impresiones": t.impressions,
            "clics": t.link_clicks,
            "mensajes": t.messages,
            "costo_por_mensaje": round(t.cost_per_message) if t.cost_per_message else None,
            "ctr_pct": round(t.ctr * 100, 2) if t.ctr else None,
            "presupuesto_diario": r.budget,
        }

    return {
        "periodo": f"{since:%d/%m/%Y} al {until:%d/%m/%Y}",
        "totales": _rounded(totals.as_dict()),
        "periodo_anterior": _rounded(previous.as_dict()),
        "variaciones_pct": {k: round(v * 100) for k, v in changes.items() if v is not None},
        "campanas": [row(c) for c in campaigns],
        "anuncios": [row(a) for a in ads],
        "instagram_organico_por_formato": organic,
    }


def register_ads_routes(
    app: FastAPI,
    settings: Settings,
    templates,
    db: Callable,
    render: Callable[..., HTMLResponse],
    back: Callable[..., Response],
) -> None:
    env = templates.env
    env.filters.update(money=fmt_money, num=fmt_int, ratio=fmt_ratio)
    env.globals.update(
        AD_STATUS_LABELS=STATUS_LABELS,
        AD_ACCOUNT_STATUS=ACCOUNT_STATUS,
        CHECKLIST_STATUSES=CHECKLIST_STATUSES,
    )
    live_cache: dict[tuple, tuple[float, dict[str, int]]] = {}

    def open_alerts(session, ws_id: int) -> int:
        return (
            session.scalar(
                select(func.count(AdAlert.id)).where(
                    AdAlert.workspace_id == ws_id, AdAlert.status == "abierta"
                )
            )
            or 0
        )

    def live_reach(session, ws_id: int, level: str, since: date, until: date) -> dict[str, int]:
        """Para fechas libres el alcance se pide en el momento a Meta (y se guarda 10 minutos)."""
        if not settings.ads_configured:
            return {}
        meta_level = {"campanas": "campaign", "anuncios": "ad", "cuenta": "account"}.get(level)
        if not meta_level:
            return {}
        key = (meta_level, since, until)
        cached = live_cache.get(key)
        if cached and time.monotonic() - cached[0] < LIVE_REACH_TTL:
            return cached[1]
        try:
            client = build_client(session, ws_id, settings)
            rows = fetch_period(client, settings.ad_account_id, meta_level, since, until)
        except (AdsApiError, BudgetExceeded) as exc:
            log.warning("No se pudo traer el alcance del rango: %s", exc)
            return {}
        values = {r["meta_id"]: r["reach"] for r in rows}
        live_cache[key] = (time.monotonic(), values)
        return values

    # ---------------- Resumen y campañas ----------------
    @app.get("/publicidad", response_class=HTMLResponse)
    def ads_page(
        request: Request,
        periodo: str = "mes",
        desde: str = "",
        hasta: str = "",
        nivel: str = "campanas",
        campana: str = "",
    ):
        today = date.today()
        # "Todas" llega vacío; cualquier otra cosa que no sea un número también es "todas".
        campana = int(campana) if campana.isdigit() else None
        periodo, since, until = resolve_period(periodo, desde, hasta, today)
        nivel = nivel if nivel in LEVELS else "campanas"
        length = (until - since).days + 1
        prev_until = since - timedelta(days=1)
        prev_since = prev_until - timedelta(days=length - 1)
        with db() as (session, ws):
            account = get_account(session, ws.id, settings)
            account_id = account.meta_account_id if account else None
            totals = account_totals(session, ws.id, since, until, account_id)
            previous = account_totals(session, ws.id, prev_since, prev_until, None)
            live = {}
            if periodo == "rango":
                live = live_reach(session, ws.id, nivel, since, until)
                if totals.reach is None:
                    totals.reach = live_reach(session, ws.id, "cuenta", since, until).get(
                        settings.ad_account_id
                    )
            rows = level_rows(session, ws.id, nivel, since, until, campana, live)
            campaigns = level_rows(session, ws.id, "campanas", since, until)
            series = daily_series(session, ws.id, since, until)
            monthly = (until - since).days > 62
            if monthly:
                series = by_month(series)
            advice_key = f"{periodo}|{since}|{until}|{campana or ''}"
            advice = get_advice(session, ws.id, "publicidad", advice_key)
            return render(
                request,
                "ads.html",
                active="ads",
                tab="resumen",
                configured=settings.ads_configured,
                account=account,
                alerts_open=open_alerts(session, ws.id),
                periods=PERIODS,
                periodo=periodo,
                since=since,
                until=until,
                nivel=nivel,
                levels=LEVEL_LABELS,
                campana=campana,
                campaigns=campaigns,
                totals=totals,
                changes={
                    "spend": pct_change(totals.spend, previous.spend),
                    "messages": pct_change(totals.messages, previous.messages),
                    "link_clicks": pct_change(totals.link_clicks, previous.link_clicks),
                    "cost_per_message": pct_change(
                        totals.cost_per_message, previous.cost_per_message
                    ),
                },
                rows=rows,
                series=series,
                monthly=monthly,
                charts=ads_charts(series, monthly, campaigns, rows),
                advice=advice,
                ai_ready=bool(settings.gemini_api_key),
                max_spend=max((d["spend"] for d in series), default=0),
                sync_hours=settings.meta_ads_sync_hours,
            )

    @app.post("/publicidad/analizar")
    def ads_advice(
        periodo: str = Form("mes"),
        desde: str = Form(""),
        hasta: str = Form(""),
        campana: str = Form(""),
        nivel: str = Form("campanas"),
    ):
        from cmr_leads.instagram.dashboard import build_dashboard

        periodo, since, until = resolve_period(periodo, desde, hasta, date.today())
        campaign_id = int(campana) if campana.isdigit() else None
        nivel = nivel if nivel in LEVELS else "campanas"
        target = (
            f"/publicidad?periodo={periodo}&desde={since}&hasta={until}&nivel={nivel}"
            f"&campana={campaign_id or ''}"
        )
        length = (until - since).days + 1
        prev_until = since - timedelta(days=1)
        prev_since = prev_until - timedelta(days=length - 1)
        with db() as (session, ws):
            account = get_account(session, ws.id, settings)
            account_id = account.meta_account_id if account else None
            totals = account_totals(session, ws.id, since, until, account_id)
            if not totals.spend:
                return back(target, "No hay inversión en este período para analizar")
            previous = account_totals(session, ws.id, prev_since, prev_until, None)
            changes = {
                "inversion": pct_change(totals.spend, previous.spend),
                "mensajes": pct_change(totals.messages, previous.messages),
                "costo_por_mensaje": pct_change(totals.cost_per_message, previous.cost_per_message),
            }
            campaigns = level_rows(session, ws.id, "campanas", since, until)
            ads = level_rows(session, ws.id, "anuncios", since, until, campaign_id)
            organic = [
                {"formato": f["name"], "publicaciones": f["count"], "vistas_prom": f["avg_views"]}
                for f in build_dashboard(session, ws.id, since, until)["formats"]
            ]
            facts = ads_facts(totals, previous, changes, campaigns, ads, organic, since, until)
            key = f"{periodo}|{since}|{until}|{campaign_id or ''}"
            try:
                request_advice(session, ws.id, settings, "publicidad", key, facts)
            except AiWriterError as exc:
                return back(target, f"No se pudo analizar: {exc}")
        return back(target + "#analisis", "Análisis listo")

    @app.post("/publicidad/actualizar")
    def ads_sync(next: str = Form("/publicidad")):
        with db() as (session, ws):
            message = run_ads_sync(session, ws.id, settings)
        live_cache.clear()
        target = next if next.startswith("/publicidad") else "/publicidad"
        return back(target, message)

    # ---------------- Alertas ----------------
    @app.get("/publicidad/alertas", response_class=HTMLResponse)
    def ads_alerts(request: Request, ver: str = "abiertas"):
        with db() as (session, ws):
            query = select(AdAlert).where(AdAlert.workspace_id == ws.id)
            if ver != "todas":
                query = query.where(AdAlert.status == "abierta")
            alerts = list(session.scalars(query.order_by(AdAlert.created_at.desc()).limit(200)))
            return render(
                request,
                "ads_alerts.html",
                active="ads",
                tab="alertas",
                alerts=alerts,
                ver=ver,
                alerts_open=open_alerts(session, ws.id),
                configured=settings.ads_configured,
                cost_pct=settings.meta_ads_cost_alert_pct,
                low_balance=settings.meta_ads_low_balance_ars,
            )

    @app.post("/publicidad/alertas/{alert_id}/resolver")
    def ads_alert_resolve(alert_id: int):
        with db() as (session, ws):
            alert = session.get(AdAlert, alert_id)
            if alert and alert.workspace_id == ws.id and alert.status == "abierta":
                alert.status, alert.resolved_at = "resuelta", utcnow()
        return back("/publicidad/alertas", "Alerta marcada como resuelta")

    # ---------------- Reportes ----------------
    @app.get("/publicidad/reportes", response_class=HTMLResponse)
    def ads_reports(request: Request):
        today = date.today()
        options = []
        month = today.replace(day=1)
        for _ in range(12):
            options.append((f"{month:%Y-%m}", month_label(month).capitalize()))
            month = previous_month(month)
        with db() as (session, ws):
            reports = list(
                session.scalars(
                    select(AdReport)
                    .where(AdReport.workspace_id == ws.id)
                    .order_by(AdReport.month.desc())
                )
            )
            return render(
                request,
                "ads_reports.html",
                active="ads",
                tab="reportes",
                reports=reports,
                options=options,
                default_month=f"{previous_month(today):%Y-%m}",
                alerts_open=open_alerts(session, ws.id),
                configured=settings.ads_configured,
            )

    @app.post("/publicidad/reportes/generar")
    def ads_report_generate(mes: str = Form(...)):
        try:
            month = parse_month(mes)
        except ValueError:
            return back("/publicidad/reportes", "Mes inválido")
        with db() as (session, ws):
            client = build_client(session, ws.id, settings) if settings.ads_configured else None
            report = generate_report(session, ws.id, settings, month, client)
            label = report.data["label"]
        return back(f"/publicidad/reportes/{mes}", f"Reporte de {label} listo")

    def _report(session, ws_id: int, mes: str) -> AdReport | None:
        return session.scalar(
            select(AdReport).where(AdReport.workspace_id == ws_id, AdReport.month == mes)
        )

    @app.get("/publicidad/reportes/{mes}", response_class=HTMLResponse)
    def ads_report_view(request: Request, mes: str):
        with db() as (session, ws):
            report = _report(session, ws.id, mes)
            if report is None:
                return back("/publicidad/reportes", "Ese reporte todavía no existe")
            return render(
                request,
                "ads_report.html",
                active="ads",
                tab="reportes",
                report=report,
                d=report.data,
                alerts_open=open_alerts(session, ws.id),
                configured=settings.ads_configured,
                generated=datetime.fromisoformat(report.data["generated_at"]),
            )

    @app.get("/publicidad/reportes/{mes}/pdf")
    def ads_report_pdf(mes: str):
        with db() as (session, ws):
            report = _report(session, ws.id, mes)
            path = Path(report.pdf_path) if report and report.pdf_path else None
        if path is None or not path.exists():
            return back("/publicidad/reportes", "No encontré el PDF. Generá el reporte de nuevo.")
        return FileResponse(path, media_type="application/pdf", filename=path.name)

    # ---------------- Checklist de configuración ----------------
    @app.get("/publicidad/configuracion", response_class=HTMLResponse)
    def ads_setup(request: Request):
        with db() as (session, ws):
            items = ensure_checklist(session, ws.id)
            done = sum(1 for i in items if i.status == "listo")
            return render(
                request,
                "ads_setup.html",
                active="ads",
                tab="configuracion",
                items=items,
                done=done,
                alerts_open=open_alerts(session, ws.id),
                configured=settings.ads_configured,
                account_id=settings.ad_account_id,
            )

    @app.post("/publicidad/configuracion/{key}")
    def ads_setup_update(key: str, status: str = Form(...), notes: str = Form("")):
        if status not in CHECKLIST_STATUSES:
            return back("/publicidad/configuracion", "Estado inválido")
        with db() as (session, ws):
            ensure_checklist(session, ws.id)
            item = session.scalar(
                select(AdChecklistItem).where(
                    AdChecklistItem.workspace_id == ws.id, AdChecklistItem.key == key
                )
            )
            if item is None:
                return back("/publicidad/configuracion", "Paso no encontrado")
            item.status = status
            item.notes = notes.strip() or None
            title = item.title
        return back("/publicidad/configuracion", f"{title}: {CHECKLIST_STATUSES[status]}")

    # ---------------- Sincronización automática ----------------
    def ads_loop() -> None:
        """Mientras la app está abierta, sincroniza cada X horas (y al abrir)."""
        while True:
            try:
                with db() as (session, ws):
                    account = get_account(session, ws.id, settings)
                    last = account.last_synced_at if account else None
                    due = last is None or utcnow() - last >= timedelta(
                        hours=settings.meta_ads_sync_hours
                    )
                    if due:
                        log.info(run_ads_sync(session, ws.id, settings))
                        live_cache.clear()
            except Exception:  # noqa: BLE001 - nunca debe tirar la app
                log.exception("Falló la sincronización automática de publicidad")
            time.sleep(30 * 60)  # revisa cada media hora si ya toca

    app.state.ads_loop = ads_loop

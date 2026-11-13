"""Pantalla de Instagram: filtros por período y formato, gráficos, historias y análisis."""

import logging
import time
from datetime import date, timedelta

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse

from cmr_leads.ads.web import PERIODS, resolve_period
from cmr_leads.advice import get_advice, request_advice
from cmr_leads.instagram.dashboard import FORMATS, WEEKDAYS, build_dashboard
from cmr_leads.instagram.promote import ideas_facts, ideas_for
from cmr_leads.instagram.stats import local_now
from cmr_leads.messaging.ai_writer import AiWriterError
from cmr_leads.settings import Settings
from cmr_leads.web.charts import chart, compare, dataset, metric

log = logging.getLogger(__name__)
SHORT_DAYS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]


def _label(day: date, monthly: bool) -> str:
    return day.strftime("%m/%Y") if monthly else day.strftime("%d/%m")


def _when(post) -> str:
    local = post.published_at - timedelta(hours=3) if post.published_at else None
    return f"{WEEKDAYS[local.weekday()]} {local:%H:%M}" if local else ""


def _pct(current, previous):
    if current is None or not previous:
        return None
    return (current - previous) / previous


def _caption(post, size: int = 40) -> str:
    text = ((post.caption or "Sin texto").split("\n")[0]).strip()
    return text if len(text) <= size else text[: size - 1] + "…"


def ad_spend_by_day(session, workspace_id: int, since: date, until: date, monthly: bool) -> dict:
    """Inversión en publicidad por día (o por mes), para cruzarla con los números de Instagram."""
    from cmr_leads.ads.metrics import by_month, daily_series

    rows = daily_series(session, workspace_id, since, until)
    if monthly:
        rows = by_month(rows)
    return {r["day"]: round(r["spend"]) for r in rows}


def build_charts(d: dict, ads: dict | None = None) -> dict:
    """Comparador de doble eje en el tiempo y un dato por gráfico en el resto.

    `ads` es la inversión en publicidad por día (o mes), para cruzarla con Instagram.
    """
    charts = {}
    s = d["series"]
    labels = [_label(x["day"], d["monthly"]) for x in s]
    ads = ads or {}
    metrics = [
        metric("views", "Visualizaciones", [x["views"] for x in s], "azul"),
        metric("reach", "Alcance (personas por día)", [x["reach"] for x in s], "turquesa"),
        metric("profile_views", "Visitas al perfil", [x["profile_views"] for x in s], "ig"),
        metric("clicks", "Clics a links y botones", [x["clicks"] for x in s], "alerta"),
        metric("interactions", "Interacciones", [x.get("interactions", 0) for x in s], "violeta"),
    ]
    if any(ads.values()):
        metrics.append(
            metric(
                "ad_spend",
                "Inversión en publicidad",
                [ads.get(x["day"], 0) for x in s],
                "navy",
                "money",
            )
        )
    charts["ig-time"] = compare(
        "ig-time",
        labels,
        metrics,
        left="views",
        right="ad_spend" if any(ads.values()) else "profile_views",
    )
    if d["links"]:
        charts["ig-links"] = chart(
            "ig-links",
            "bar",
            [x["name"] for x in d["links"]],
            [dataset("Toques", [x["value"] for x in d["links"]], "turquesa")],
            horizontal=True,
            highlight="max",
        )
    if d["formats"]:
        charts["ig-formats"] = chart(
            "ig-formats",
            "bar",
            [f["name"] for f in d["formats"]],
            [dataset("Vistas promedio", [f["avg_views"] for f in d["formats"]], "azul")],
            highlight="max",
        )
    for f in d["formats"]:
        t = f["times"]
        charts[f"ig-day-{f['key']}"] = chart(
            f"ig-day-{f['key']}",
            "bar",
            SHORT_DAYS,
            [dataset("Vistas promedio", [x["avg"] for x in t["days"]], "azul")],
            highlight="max",
        )
        charts[f"ig-slot-{f['key']}"] = chart(
            f"ig-slot-{f['key']}",
            "bar",
            [x["name"] for x in t["slots"]],
            [dataset("Vistas promedio", [x["avg"] for x in t["slots"]], "turquesa")],
            highlight="max",
        )
    top = [p for p in d["posts"] if (p.views or p.reach)][:10]
    if top:
        charts["ig-top"] = chart(
            "ig-top",
            "bar",
            [f"{_caption(p)} ({p.kind})" for p in top],
            [
                dataset(
                    "Visualizaciones",
                    [p.views if p.views is not None else p.reach for p in top],
                    "azul",
                )
            ],
            horizontal=True,
            highlight="max",
        )
    st = d["stories"]
    if st["total"]:
        days = st["days"]
        charts["ig-stories"] = chart(
            "ig-stories",
            "bar",
            [_label(x["day"], False) for x in days],
            [dataset("Historias subidas", [x["count"] for x in days], "ig")],
        )
        charts["ig-stories-week"] = chart(
            "ig-stories-week",
            "bar",
            SHORT_DAYS,
            [dataset("Vistas por historia", [x["avg_views"] for x in st["weekdays"]], "ig")],
            highlight="max",
        )
    return charts


def advice_facts(d: dict) -> dict:
    """Lo que se le pasa al análisis: solo números agregados y textos de publicaciones."""

    def times(t):
        return {
            "por_dia": {
                x["name"]: {"vistas_prom": x["avg"], "posts": x["count"]} for x in t["days"]
            },
            "por_franja": {
                x["name"]: {"vistas_prom": x["avg"], "posts": x["count"]} for x in t["slots"]
            },
        }

    st = d["stories"]
    return {
        "periodo": f"{d['since']:%d/%m/%Y} al {d['until']:%d/%m/%Y}",
        "cuenta": d["totals"],
        "cuenta_periodo_anterior": d["previous"],
        "links_y_botones": {x["name"]: x["value"] for x in d["links"]},
        "formatos": [
            {
                "formato": f["name"],
                "publicaciones": f["count"],
                "vistas_prom": f["avg_views"],
                "alcance_prom": f["avg_reach"],
                "interacciones_prom": f["avg_interactions"],
                "visitas_al_perfil": f["profile_visits"],
                **times(f["times"]),
            }
            for f in d["formats"]
        ],
        "historias": {
            "aclaracion": (
                f"Solo hay datos de historias desde el {st['since']:%d/%m/%Y}: Meta no deja leer "
                "historias de días anteriores. Todos los promedios de historias son desde esa fecha."
            ),
            "total": st["total"],
            "por_dia_promedio": st["per_day_avg"],
            "dias_con_historias": st["days_with_stories"],
            "vistas_prom_por_historia": st["avg_views"],
            "respuestas": st["replies"],
            "por_dia_de_semana": {
                x["name"]: {"historias_prom": x["avg_count"], "vistas_por_historia": x["avg_views"]}
                for x in st["weekdays"]
            },
        },
        "mejores_publicaciones": [
            {
                "formato": p.kind,
                "texto": (p.caption or "").split("\n")[0][:90],
                "vistas": p.views,
                "alcance": p.reach,
                "interacciones": p.interactions,
                "publicada": _when(p),
            }
            for p in d["posts"][:8]
        ],
    }


def register_instagram_routes(
    app: FastAPI, settings: Settings, templates, db, render, back
) -> None:
    def resolve(periodo, desde, hasta, formato):
        today = local_now().date()
        periodo, since, until = resolve_period(periodo, desde, hasta, today)
        formato = formato if formato in FORMATS else ""
        key = f"{periodo}|{since}|{until}|{formato}"
        return periodo, since, until, formato, key

    @app.get("/instagram", response_class=HTMLResponse)
    def instagram_page(
        request: Request, periodo: str = "30d", desde: str = "", hasta: str = "", formato: str = ""
    ):
        from cmr_leads.instagram.analysis import build_report

        periodo, since, until, formato, key = resolve(periodo, desde, hasta, formato)
        with db() as (session, ws):
            d = build_dashboard(session, ws.id, since, until, formato)
            ads = ad_spend_by_day(session, ws.id, since, until, d["monthly"])
            ideas = ideas_for(session, ws.id, local_now().date())
            report = build_report(session, ws.id)
            advice = get_advice(session, ws.id, "instagram", key)
            t, p = d["totals"], d["previous"]
            return render(
                request,
                "instagram.html",
                d=d,
                report=report,
                charts=build_charts(d, ads),
                ideas=ideas,
                periods=PERIODS,
                periodo=periodo,
                formats=FORMATS,
                formato=formato,
                advice=advice,
                ai_ready=bool(settings.gemini_api_key),
                changes={
                    k: _pct(t[k], p[k])
                    for k in (
                        "views",
                        "profile_views",
                        "website_clicks",
                        "link_taps",
                        "interactions",
                        "reach_avg",
                    )
                },
                configured=settings.instagram_configured,
                dms_configured=bool(settings.facebook_page_id),
                stories_minutes=settings.instagram_stories_minutes,
                ig_offset=timedelta(hours=3),
            )

    @app.post("/instagram/analizar")
    def instagram_advice(
        periodo: str = Form("30d"),
        desde: str = Form(""),
        hasta: str = Form(""),
        formato: str = Form(""),
    ):
        periodo, since, until, formato, key = resolve(periodo, desde, hasta, formato)
        target = f"/instagram?periodo={periodo}&desde={since}&hasta={until}&formato={formato}"
        with db() as (session, ws):
            d = build_dashboard(session, ws.id, since, until, formato)
            if not d["totals"]["days"] and not d["post_count"]:
                return back(target, "No hay datos en este período para analizar")
            try:
                facts = advice_facts(d)
                facts["que_promocionar"] = ideas_facts(
                    ideas_for(session, ws.id, local_now().date())
                )
                request_advice(session, ws.id, settings, "instagram", key, facts)
            except AiWriterError as exc:
                return back(target, f"No se pudo analizar: {exc}")
        return back(target + "#analisis", "Análisis listo")

    def stories_loop() -> None:
        """Historias: Meta solo las muestra 24 h, así que se leen seguido con la app abierta."""
        from cmr_leads.instagram.client import GraphClient
        from cmr_leads.instagram.stats import StatsResult, sync_stats, sync_stories
        from cmr_leads.instagram.sync import daily_budget_guard
        from cmr_leads.pipeline.budget import DailyBudget

        last_full = None
        while True:
            try:
                with db() as (session, ws):
                    budget = DailyBudget(
                        session, ws.id, "meta_stats", settings.meta_max_stats_requests_per_day
                    )
                    client = GraphClient(
                        settings.meta_access_token,
                        settings.meta_graph_api_version,
                        on_request=daily_budget_guard(budget),
                    )
                    ig_id = settings.instagram_business_account_id
                    full_due = settings.instagram_stats_hours and (
                        last_full is None
                        or time.monotonic() - last_full >= settings.instagram_stats_hours * 3600
                    )
                    if full_due:
                        # Días (completa el año de a tandas), publicaciones e historias.
                        last_full = time.monotonic()
                        log.info(sync_stats(session, ws.id, client, ig_id).summary())
                    else:
                        sync_stories(session, ws.id, client, ig_id, StatsResult())
            except Exception:  # noqa: BLE001 - nunca debe tirar la app
                log.exception("Falló la lectura de historias de Instagram")
            time.sleep(max(15, settings.instagram_stories_minutes) * 60)

    app.state.stories_loop = stories_loop

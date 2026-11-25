"""Pantalla de Instagram: filtros por período y formato, gráficos, historias y análisis."""

import logging
import time
from datetime import date, timedelta

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response

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


def followers_vs_ads(d: dict, ads: dict) -> dict | None:
    """Seguidores ganados por día con publicidad activa y sin publicidad (solo vista diaria)."""
    if d["monthly"] or not any(ads.values()):
        return None
    with_ads = [x["follows"] for x in d["series"] if ads.get(x["day"], 0) > 0]
    without = [x["follows"] for x in d["series"] if ads.get(x["day"], 0) <= 0]
    if not with_ads or not without:
        return None
    return {
        "with_ads": round(sum(with_ads) / len(with_ads), 1),
        "without": round(sum(without) / len(without), 1),
        "days_with": len(with_ads),
        "days_without": len(without),
    }


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
        metric("follows", "Seguidores ganados", [x.get("follows", 0) for x in s], "turquesa"),
        metric(
            "followers_total",
            "Seguidores totales (aprox.)",
            [x.get("followers_total") for x in s],
            "navy",
        ),
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
    if d["followers"]["days_measured"]:
        charts["ig-follows"] = compare(
            "ig-follows",
            labels,
            metrics,
            left="follows",
            right="followers_total",
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
    fl = d["followers"]
    return {
        "seguidores": {
            "total_actual": fl["current"],
            "ganados_en_el_periodo": fl["gained"],
            "ganados_periodo_anterior": fl["previous"],
            "cada_100_visitas_al_perfil": fl["per_100_visits"],
            "por_formato_cada_1000_vistas": {f["name"]: f["per_1000"] for f in fl["formats"]},
            "publicaciones_que_mas_trajeron": [
                {"texto": _caption(x, 60), "seguidores": x.follows} for x in fl["top"]
            ],
            "con_publicidad_vs_sin": fl.get("ads"),
        },
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

    @app.post("/instagram/importar")
    def instagram_import(files: list[UploadFile]):
        from cmr_leads.instagram.meta_export import (
            MetaExportError,
            import_daily_export,
            import_meta_export,
            is_daily_export,
        )

        messages = []
        with db() as (session, ws):
            for upload in files:
                raw = upload.file.read()
                if not raw:
                    continue
                try:
                    if is_daily_export(raw):
                        messages.append(
                            import_daily_export(session, ws.id, raw, local_now().date())
                        )
                    else:
                        messages.append(import_meta_export(session, ws.id, raw).summary())
                except MetaExportError as exc:
                    messages.append(f"{upload.filename}: {exc}")
            session.commit()
        if not messages:
            return back("/instagram", "Elegí los archivos CSV que exporta Meta.")
        return back("/instagram", " ".join(dict.fromkeys(messages)))

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
            from cmr_leads.instagram.calendar import story_recommendation

            story_plan = story_recommendation(session, ws.id, local_now().date())
            report = build_report(session, ws.id)
            advice = get_advice(session, ws.id, "instagram", key)
            d["followers"]["ads"] = followers_vs_ads(d, ads)
            t, p = d["totals"], d["previous"]
            return render(
                request,
                "instagram.html",
                d=d,
                report=report,
                charts=build_charts(d, ads),
                ideas=ideas,
                story_plan=story_plan,
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
                        "follows",
                    )
                },
                configured=settings.instagram_configured,
                dms_configured=bool(settings.facebook_page_id),
                stories_minutes=settings.instagram_stories_minutes,
                ig_offset=timedelta(hours=3),
            )

    def calendar_month(mes: str, today: date) -> date:
        try:
            year, month = (int(x) for x in mes.split("-")[:2])
            return date(year, month, 1)
        except ValueError:
            return today.replace(day=1)

    def calendar_for(session, ws_id: int, mes: str) -> dict:
        from sqlalchemy import select

        from cmr_leads.db.models import Ad
        from cmr_leads.instagram.calendar import build_calendar

        today = local_now().date()
        names = list(session.scalars(select(Ad.name).where(Ad.workspace_id == ws_id)))
        return build_calendar(session, ws_id, calendar_month(mes, today), today, names)

    SCHEDULE_HINT = {
        "reel": "Video del reel (MP4 o MOV, vertical 9:16)",
        "carrusel": "Fotos o videos del carrusel (de 2 a 10, en orden)",
        "imagen": "Foto (JPG, PNG o WEBP; se pasa a JPG)",
        "historia": "Foto o video de la historia (vertical 9:16)",
    }
    SCHEDULE_ACCEPT = {
        "reel": "video/mp4,video/quicktime",
        "carrusel": "image/jpeg,image/png,image/webp,video/mp4,video/quicktime",
        "imagen": "image/jpeg,image/png,image/webp",
        "historia": "image/jpeg,image/png,image/webp,video/mp4,video/quicktime",
    }
    STATUS_LABEL = {
        "pendiente": "Programada",
        "publicando": "Publicando",
        "publicada": "Publicada",
        "error": "Error",
        "vencida": "No salió",
        "cancelada": "Cancelada",
    }
    STATUS_CHIP = {
        "pendiente": "accent",
        "publicando": "accent",
        "publicada": "green",
        "error": "red",
        "vencida": "amber",
        "cancelada": "",
    }

    def parse_when(dia: str, hora: str):
        from datetime import datetime

        try:
            local = datetime.strptime(f"{dia} {hora[:5]}", "%Y-%m-%d %H:%M")
        except ValueError:
            return None
        return local + timedelta(hours=3)  # hora de Argentina -> UTC

    @app.get("/instagram/programar", response_class=HTMLResponse)
    def instagram_schedule_page(
        request: Request,
        formato: str = "reel",
        dia: str = "",
        hora: str = "",
        origen: str = "",
        id: int | None = None,
    ):
        from sqlalchemy import select

        from cmr_leads.db.models import Ad, InstagramScheduled
        from cmr_leads.instagram.calendar import next_slot
        from cmr_leads.instagram.publish import KINDS

        now = local_now()
        formato = formato if formato in KINDS else "reel"
        if hora.isdigit():
            hora = f"{int(hora):02d}:00"
        with db() as (session, ws):
            suggested = ""
            if not dia:
                names = list(session.scalars(select(Ad.name).where(Ad.workspace_id == ws.id)))
                slot = next_slot(session, ws.id, formato, now.replace(tzinfo=None), names)
                if slot:
                    d, h, suggested = slot
                    dia, hora = d.isoformat(), f"{h:02d}:00"
                else:
                    suggested = (
                        "El calendario no tiene lugares libres para este formato este mes ni el "
                        "próximo (ya los fijaste vos): elegí el día y la hora."
                    )
            rows = list(
                session.scalars(
                    select(InstagramScheduled)
                    .where(
                        InstagramScheduled.workspace_id == ws.id,
                        InstagramScheduled.status != "cancelada",
                    )
                    .order_by(InstagramScheduled.publish_at.desc())
                    .limit(40)
                )
            )
            for r in rows:
                r.local = r.publish_at - timedelta(hours=3)
            rows.sort(key=lambda r: (r.status == "publicada", r.publish_at))
            return render(
                request,
                "instagram_schedule.html",
                active="instagram",
                configured=settings.instagram_publish_configured,
                today=now.date(),
                kinds=KINDS,
                hint=SCHEDULE_HINT,
                accept=SCHEDULE_ACCEPT,
                status_label=STATUS_LABEL,
                status_chip=STATUS_CHIP,
                grace=settings.instagram_publish_grace_minutes,
                items=rows,
                upcoming=sum(1 for r in rows if r.status == "pendiente"),
                overdue=sum(1 for r in rows if r.status == "vencida"),
                form={
                    "formato": formato,
                    "dia": dia or now.date().isoformat(),
                    "hora": hora or "19:00",
                    "origen": origen,
                    "id": id,
                    "suggested": suggested,
                },
            )

    @app.post("/instagram/programar")
    def instagram_schedule(
        files: list[UploadFile],
        formato: str = Form(...),
        dia: str = Form(...),
        hora: str = Form(...),
        texto: str = Form(""),
        origen: str = Form(""),
        id: str = Form(""),
    ):
        from datetime import datetime

        from cmr_leads.db.models import InstagramPlanItem, InstagramScheduled
        from cmr_leads.instagram.publish import (
            KINDS,
            MAX_FILE_BYTES,
            store_files,
            validate_files,
        )

        if formato not in KINDS:
            return back("/instagram/programar", "Elegí un formato.")
        publish_at = parse_when(dia, hora)
        if publish_at is None:
            return back(f"/instagram/programar?formato={formato}", "Revisá el día y la hora.")
        if publish_at <= local_now().replace(tzinfo=None) + timedelta(hours=3):
            return back(f"/instagram/programar?formato={formato}", "Esa hora ya pasó.")
        uploads = [(f.filename or "archivo", f.file.read()) for f in files if f.filename]
        error = validate_files(formato, [name for name, _raw in uploads])
        heavy = [name for name, raw in uploads if len(raw) > MAX_FILE_BYTES]
        if not error and heavy:
            error = (
                f"{heavy[0]} pesa más de 50 MB, el máximo del almacenamiento. "
                "Exportalo más liviano (1080p alcanza para Instagram)."
            )
        if error:
            return back(f"/instagram/programar?formato={formato}", error)
        local = publish_at - timedelta(hours=3)
        with db() as (session, ws):
            plan = None
            if formato != "historia":
                plan = session.get(InstagramPlanItem, int(id)) if id.isdigit() else None
                if plan is None or plan.workspace_id != ws.id:
                    origin = None
                    if origen:
                        try:
                            origin = datetime.strptime(origen, "%Y-%m-%d")
                        except ValueError:
                            origin = None
                    plan = InstagramPlanItem(workspace_id=ws.id, origin_day=origin)
                    session.add(plan)
                plan.day = datetime(local.year, local.month, local.day)
                plan.hour, plan.kind, plan.removed = local.hour, formato, False
                session.flush()
            item = InstagramScheduled(
                workspace_id=ws.id,
                plan_item_id=plan.id if plan else None,
                kind=formato,
                caption=texto.strip() or None,
                publish_at=publish_at,
                files=[],
                status="pendiente",
            )
            session.add(item)
            session.flush()
            try:
                item.files = store_files(settings.instagram_publish_dir / str(item.id), uploads)
            except Exception:  # noqa: BLE001 - archivo roto o formato raro
                session.rollback()
                return back(
                    f"/instagram/programar?formato={formato}",
                    "No se pudo leer el archivo. Probá con otro (JPG, PNG, MP4).",
                )
            session.commit()
        return back(
            "/instagram/programar",
            f"Programado para el {local:%d/%m} a las {local:%H:%M}. Dejá la app abierta a esa hora.",
        )

    @app.post("/instagram/programadas/{item_id}/{accion}")
    def instagram_schedule_action(
        item_id: int, accion: str, dia: str = Form(""), hora: str = Form("")
    ):
        from datetime import datetime

        from cmr_leads.db.models import InstagramPlanItem, InstagramScheduled
        from cmr_leads.instagram.publish import cancel

        with db() as (session, ws):
            item = session.get(InstagramScheduled, item_id)
            if item is None or item.workspace_id != ws.id:
                return back("/instagram/programar", "No encontré esa publicación.")
            if item.status not in ("pendiente", "vencida", "error"):
                return back("/instagram/programar", "Esa publicación ya no se puede cambiar.")
            now_utc = local_now().replace(tzinfo=None) + timedelta(hours=3)
            if accion == "cancelar":
                cancel(session, item, settings.instagram_publish_dir)
                msg = "Publicación cancelada."
            elif accion == "publicar":
                item.publish_at, item.status, item.error = now_utc, "pendiente", None
                msg = "Se publica en el próximo minuto. Dejá la app abierta."
            elif accion == "reprogramar":
                publish_at = parse_when(dia, hora)
                if publish_at is None or publish_at <= now_utc:
                    return back(
                        "/instagram/programar", "Elegí un día y una hora que no hayan pasado."
                    )
                item.publish_at, item.status, item.error = publish_at, "pendiente", None
                plan = (
                    session.get(InstagramPlanItem, item.plan_item_id) if item.plan_item_id else None
                )
                if plan is not None:
                    local = publish_at - timedelta(hours=3)
                    plan.day = datetime(local.year, local.month, local.day)
                    plan.hour = local.hour
                msg = "Listo, cambiada la hora."
            else:
                return back("/instagram/programar", "Acción desconocida.")
            session.commit()
        return back("/instagram/programar", msg)

    def publish_loop() -> None:
        """Cada minuto: publica lo que ya tocaba (la app tiene que estar abierta)."""
        from cmr_leads.instagram.client import GraphClient
        from cmr_leads.instagram.publish import SupabaseStorage, folder_for, publish_item, run_due

        while True:
            try:
                if settings.instagram_publish_configured:
                    client = GraphClient(
                        settings.meta_access_token, settings.meta_graph_api_version
                    )
                    storage = SupabaseStorage(
                        settings.supabase_url,
                        settings.supabase_service_key,
                        settings.supabase_bucket,
                    )
                    with db() as (session, ws):
                        done = run_due(
                            session,
                            ws.id,
                            local_now().replace(tzinfo=None) + timedelta(hours=3),
                            settings.instagram_publish_grace_minutes,
                            lambda item, client=client, storage=storage: publish_item(
                                item,
                                folder_for(settings.instagram_publish_dir, item),
                                client,
                                settings.instagram_business_account_id,
                                storage,
                            ),
                        )
                        for line in done:
                            log.info("Programadas: %s", line)
            except Exception:  # noqa: BLE001 - nunca debe tirar la app
                log.exception("Falló el publicador de Instagram")
            time.sleep(60)

    app.state.publish_loop = publish_loop

    @app.get("/instagram/calendario", response_class=HTMLResponse)
    def instagram_calendar(request: Request, mes: str = ""):
        with db() as (session, ws):
            c = calendar_for(session, ws.id, mes)
            return render(request, "instagram_calendar.html", active="instagram", c=c)

    @app.post("/instagram/calendario/cambio")
    def instagram_calendar_change(
        accion: str = Form(...),
        id: int | None = Form(None),
        origen: str = Form(""),
        dia: str = Form(""),
        hora: int | None = Form(None),
        formato: str = Form(""),
        mes: str = Form(""),
    ):
        """Mover, editar, quitar o agregar publicaciones, y bloquear días del calendario."""
        from datetime import datetime

        from sqlalchemy import delete, select

        from cmr_leads.db.models import InstagramPlanItem
        from cmr_leads.instagram.calendar import BLOCKED, SHORT

        def parse(value: str) -> datetime | None:
            try:
                return datetime.strptime(value, "%Y-%m-%d") if value else None
            except ValueError:
                return None

        today = datetime.combine(local_now().date(), datetime.min.time())
        day, origin = parse(dia), parse(origen)
        if accion not in {
            "mover",
            "quitar",
            "recomendado",
            "agregar",
            "bloquear",
            "desbloquear",
            "reiniciar",
        }:
            return JSONResponse({"error": "Acción desconocida"}, status_code=400)
        if accion in {"mover", "agregar"}:
            if day is None or formato not in SHORT or formato == "historia":
                return JSONResponse({"error": "Faltan el día o el formato"}, status_code=400)
            if day < today:
                return JSONResponse(
                    {"error": "No se puede planificar en un día que ya pasó"}, status_code=400
                )
            if hora is None or not 0 <= hora <= 23:
                hora = 19
        if accion in {"bloquear", "desbloquear"} and day is None:
            return JSONResponse({"error": "Falta el día"}, status_code=400)

        with db() as (session, ws):
            item = session.get(InstagramPlanItem, id) if id else None
            if item is not None and item.workspace_id != ws.id:
                item = None
            from cmr_leads.db.models import InstagramScheduled

            sched = (
                session.scalars(
                    select(InstagramScheduled).where(
                        InstagramScheduled.plan_item_id == item.id,
                        InstagramScheduled.status.in_(("pendiente", "vencida", "error")),
                    )
                ).first()
                if item is not None
                else None
            )
            if sched is not None and accion in {"quitar", "recomendado"}:
                return JSONResponse(
                    {"error": "Está programada: cancelala desde Programar."}, status_code=400
                )
            if sched is not None and accion == "mover":
                minutes = (sched.publish_at + timedelta(hours=-3)).minute
                local = day.replace(hour=hora, minute=minutes)
                sched.publish_at = local + timedelta(hours=3)
                formato = sched.kind  # el formato lo definen los archivos subidos
                if sched.status == "vencida":
                    sched.status, sched.error = "pendiente", None
            if accion == "mover":
                if item is None:  # era una recomendación: queda fijada donde la pusiste
                    item = InstagramPlanItem(workspace_id=ws.id, origin_day=origin)
                    session.add(item)
                item.day, item.hour, item.kind, item.removed = day, hora, formato, False
            elif accion == "agregar":
                session.add(InstagramPlanItem(workspace_id=ws.id, day=day, hour=hora, kind=formato))
            elif accion == "quitar":
                if item is None and origin is not None and formato in SHORT:
                    session.add(
                        InstagramPlanItem(
                            workspace_id=ws.id,
                            day=origin,
                            kind=formato,
                            origin_day=origin,
                            removed=True,
                        )
                    )
                elif item is not None and item.origin_day is not None:
                    item.day, item.removed = item.origin_day, True
                elif item is not None:
                    session.delete(item)
            elif accion == "recomendado" and item is not None:
                session.delete(item)
            elif accion == "bloquear":
                session.add(InstagramPlanItem(workspace_id=ws.id, day=day, kind=BLOCKED))
            elif accion == "desbloquear":
                session.execute(
                    delete(InstagramPlanItem).where(
                        InstagramPlanItem.workspace_id == ws.id,
                        InstagramPlanItem.kind == BLOCKED,
                        InstagramPlanItem.day == day,
                    )
                )
            elif accion == "reiniciar":
                c = calendar_for(session, ws.id, mes)
                first, last = c["weeks"][0][0]["date"], c["weeks"][-1][-1]["date"]
                start = datetime.combine(first, datetime.min.time())
                end = datetime.combine(last, datetime.min.time())
                from cmr_leads.db.models import InstagramScheduled

                keep = set(
                    session.scalars(
                        select(InstagramScheduled.plan_item_id).where(
                            InstagramScheduled.workspace_id == ws.id,
                            InstagramScheduled.status != "cancelada",
                        )
                    )
                )
                for row in session.scalars(
                    select(InstagramPlanItem).where(InstagramPlanItem.workspace_id == ws.id)
                ):
                    if row.id in keep:  # lo programado no se borra al reiniciar
                        continue
                    days = [d for d in (row.day, row.origin_day) if d is not None]
                    if any(start <= d <= end for d in days):
                        session.delete(row)
        return JSONResponse({"ok": True})

    @app.get("/instagram/calendario.ics")
    def instagram_calendar_ics(mes: str = ""):
        from cmr_leads.instagram.calendar import to_ics

        with db() as (session, ws):
            c = calendar_for(session, ws.id, mes)
        name = f"calendario-instagram-{c['month']:%Y-%m}.ics"
        return Response(
            to_ics(c, settings.company_name),
            media_type="text/calendar; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
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
                    # Las historias primero y con su propio cupo: si se pierden no vuelven.
                    stories_budget = DailyBudget(
                        session, ws.id, "meta_stories", settings.meta_max_stories_requests_per_day
                    )
                    stories_client = GraphClient(
                        settings.meta_access_token,
                        settings.meta_graph_api_version,
                        on_request=daily_budget_guard(stories_budget),
                    )
                    sync_stories(session, ws.id, stories_client, ig_id, StatsResult())
                    session.commit()
                    full_due = settings.instagram_stats_hours and (
                        last_full is None
                        or time.monotonic() - last_full >= settings.instagram_stats_hours * 3600
                    )
                    if full_due:
                        # Días (completa el año de a tandas), publicaciones e historias.
                        last_full = time.monotonic()
                        result = sync_stats(
                            session,
                            ws.id,
                            client,
                            ig_id,
                            include_stories=False,
                            budget_left=budget.remaining,
                        )
                        log.info(result.summary())
            except Exception:  # noqa: BLE001 - nunca debe tirar la app
                log.exception("Falló la lectura de historias de Instagram")
            time.sleep(max(15, settings.instagram_stories_minutes) * 60)

    app.state.stories_loop = stories_loop

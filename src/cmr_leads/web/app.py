"""Panel web (FastAPI + Jinja2). Correr con `cmr-leads web`."""

import csv
import hashlib
import shutil
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus, urlencode, urlparse

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from cmr_leads.db.models import Lead, Workspace
from cmr_leads.db.session import get_or_create_workspace, make_engine, make_session_factory
from cmr_leads.domain.enums import Channel, EnrichmentState, LeadStatus, Priority, SourceType
from cmr_leads.domain.schemas import RawProfile, SourceRecord
from cmr_leads.exports.exporter import export_csv, export_xlsx
from cmr_leads.messaging.generator import generate_message
from cmr_leads.pipeline.dedup import find_possible_duplicates
from cmr_leads.pipeline.ingest import ingest
from cmr_leads.pipeline.learning import (
    FEEDBACK_REASONS,
    RULE_LABELS,
    analyze,
)
from cmr_leads.pipeline.scoring import (
    apply_score,
    get_weight,
    load_scoring_config,
    local_overrides_path,
    read_overrides,
    save_overrides,
)
from cmr_leads.services import (
    CONTACTABLE_STATUSES,
    LeadError,
    follow_ups_due,
    get_lead,
    leads_query,
    mark_contacted,
    mark_do_not_contact,
    set_feedback,
    set_status,
    source_stats,
)
from cmr_leads.settings import Settings, get_settings
from cmr_leads.sources.csv_import import CsvSource
from cmr_leads.sources.instagram_export import InstagramExportSource

WEB_DIR = Path(__file__).parent
PAGE_SIZE = 40

STATUS_LABELS = {
    LeadStatus.NEW: "Nuevo",
    LeadStatus.APPROVED: "Aprobado",
    LeadStatus.REJECTED: "Descartado",
    LeadStatus.CONTACTED: "Contactado",
    LeadStatus.REPLIED: "Respondió",
    LeadStatus.MEETING: "Reunión",
    LeadStatus.WON: "Cliente",
    LeadStatus.LOST: "Perdido",
}
SOURCE_LABELS = {
    SourceType.CMR_FOLLOWING: "Seguidos de CMR",
    SourceType.CMR_FOLLOWERS: "Seguidores de CMR",
    SourceType.DISCOVERED_FROM_FOLLOWING: "Seguidos de otro perfil",
    SourceType.DISCOVERED_FROM_FOLLOWERS: "Seguidores de otro perfil",
    SourceType.MANUAL: "Carga manual",
    SourceType.CSV_IMPORT: "CSV",
}
SERVICE_LABELS = {
    "pagina_web": "Página web",
    "tienda_online": "Tienda online",
    "sistema_turnos": "Sistema de turnos",
    "mejora_web": "Mejorar su web",
    "sistema_a_medida": "Sistema a medida",
}
CHANNEL_LABELS = {
    Channel.WHATSAPP: "WhatsApp",
    Channel.INSTAGRAM_DM: "Instagram",
    Channel.EMAIL: "Email",
    Channel.PHONE: "Teléfono",
    Channel.IN_PERSON: "En persona",
    Channel.OTHER: "Otro",
}


def _initials(lead: Lead) -> str:
    words = [
        w
        for w in (lead.display_name or lead.instagram_username or "?")
        .replace(".", " ")
        .replace("_", " ")
        .split()
        if w
    ]
    return "".join(w[0] for w in words[:2]).upper() or "?"


def _avatar_hue(lead: Lead) -> int:
    key = (lead.instagram_username or lead.display_name or str(lead.id)).encode()
    return int(hashlib.md5(key, usedforsecurity=False).hexdigest()[:4], 16) % 360


def _search_links(lead: Lead) -> dict[str, str]:
    query = " ".join(filter(None, [lead.display_name or lead.instagram_username, lead.city]))
    return {
        "google": f"https://www.google.com/search?q={quote_plus(query)}",
        "maps": f"https://www.google.com/maps/search/{quote_plus(query)}",
    }


def _profile_links(lead: Lead) -> list[dict[str, str]]:
    links = []
    if lead.instagram_url:
        links.append(
            {"kind": "instagram", "label": f"@{lead.instagram_username}", "url": lead.instagram_url}
        )
    if lead.whatsapp_e164:
        links.append(
            {
                "kind": "whatsapp",
                "label": "WhatsApp",
                "url": f"https://wa.me/{lead.whatsapp_e164.lstrip('+')}",
            }
        )
    if lead.phone_e164:
        links.append({"kind": "phone", "label": lead.phone_e164, "url": f"tel:{lead.phone_e164}"})
    if lead.website:
        links.append(
            {"kind": "web", "label": lead.website_domain or "Sitio web", "url": lead.website}
        )
    if lead.email:
        links.append({"kind": "email", "label": lead.email, "url": f"mailto:{lead.email}"})
    return links


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="CMR Lead Finder", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.session_factory = make_session_factory(make_engine(settings.database_url))
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

    templates = Jinja2Templates(directory=WEB_DIR / "templates")
    env = templates.env
    env.globals.update(
        STATUS_LABELS=STATUS_LABELS,
        SOURCE_LABELS=SOURCE_LABELS,
        SERVICE_LABELS=SERVICE_LABELS,
        CHANNEL_LABELS=CHANNEL_LABELS,
        FEEDBACK_REASONS=FEEDBACK_REASONS,
        RULE_LABELS=RULE_LABELS,
        LeadStatus=LeadStatus,
        Priority=Priority,
        company=settings.company_name,
    )
    env.filters.update(
        initials=_initials,
        hue=_avatar_hue,
        profile_links=_profile_links,
        search_links=_search_links,
        date=lambda d: d.strftime("%d/%m/%Y") if d else "",
        pct=lambda v: "—" if v is None else f"{v * 100:.0f}%",
    )

    def scoring_path() -> Path:
        return settings.scoring_config

    def scoring_config():
        return load_scoring_config(scoring_path())

    @contextmanager
    def db() -> Iterator[tuple[Session, Workspace]]:
        factory: sessionmaker[Session] = app.state.session_factory
        with factory() as session:
            workspace = get_or_create_workspace(session, settings.workspace)
            try:
                yield session, workspace
                session.commit()
            except Exception:
                session.rollback()
                raise

    def render(request: Request, name: str, **context) -> HTMLResponse:
        context.setdefault("msg", request.query_params.get("msg"))
        context.setdefault("active", name.split(".")[0])
        return templates.TemplateResponse(request, name, context)

    def back(url: str, msg: str | None = None) -> RedirectResponse:
        if msg:
            url += ("&" if "?" in url else "?") + urlencode({"msg": msg})
        return RedirectResponse(url, status_code=303)

    def safe_next(value: str | None, default: str) -> str:
        # Solo redirecciones internas.
        if value and value.startswith("/") and not value.startswith("//"):
            return value
        return default

    def rescore_all(session: Session, workspace: Workspace) -> int:
        config = scoring_config()
        leads = list(session.scalars(select(Lead).where(Lead.workspace_id == workspace.id)))
        for lead in leads:
            apply_score(lead, config)
        return len(leads)

    @app.middleware("http")
    async def same_origin_posts(request: Request, call_next):
        # Evita que otra página web dispare acciones contra el panel local (CSRF).
        if request.method == "POST":
            origin = request.headers.get("origin") or request.headers.get("referer")
            if origin and urlparse(origin).netloc != request.url.netloc:
                return Response("Origen no permitido", status_code=403)
        return await call_next(request)

    # ---------------- Inicio ----------------
    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        with db() as (session, ws):
            base = select(func.count(Lead.id)).where(Lead.workspace_id == ws.id)
            active = base.where(Lead.excluded.is_(False), Lead.do_not_contact.is_(False))
            stats = {
                "total": session.scalar(base) or 0,
                "interesantes": session.scalar(active.where(Lead.priority == Priority.HIGH)) or 0,
                "por_revisar": session.scalar(active.where(Lead.status == LeadStatus.NEW)) or 0,
                "aprobados": session.scalar(base.where(Lead.status == LeadStatus.APPROVED)) or 0,
                "contactados": session.scalar(base.where(Lead.contacted_at.is_not(None))) or 0,
                "pendientes_api": session.scalar(
                    base.where(Lead.enrichment_state == EnrichmentState.PENDING)
                )
                or 0,
            }
            top = list(session.scalars(leads_query(ws.id, status=LeadStatus.NEW).limit(6)))
            follow_ups = follow_ups_due(session, ws.id)
            sources = source_stats(session, ws.id)
            return render(
                request,
                "dashboard.html",
                stats=stats,
                top=top,
                follow_ups=follow_ups,
                sources=sources,
                instagram_configured=settings.instagram_configured,
            )

    # ---------------- Leads ----------------
    @app.get("/leads", response_class=HTMLResponse)
    def leads_list(
        request: Request,
        q: str = "",
        priority: str = "",
        status: str = "",
        source: str = "",
        service: str = "",
        view: str = "",
        page: int = 1,
    ):
        with db() as (session, ws):
            query = leads_query(
                ws.id,
                status=status or None,
                priority=priority or None,
                source=source or None,
                include_excluded=view == "excluidos",
            )
            if view == "excluidos":
                query = query.where((Lead.excluded.is_(True)) | (Lead.do_not_contact.is_(True)))
            if service:
                query = query.where(Lead.recommended_service == service)
            if q.strip():
                like = f"%{q.strip().lower()}%"
                query = query.where(
                    func.lower(Lead.display_name).like(like)
                    | func.lower(Lead.instagram_username).like(like)
                    | func.lower(Lead.bio).like(like)
                    | func.lower(Lead.city).like(like)
                )
            total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
            page = max(page, 1)
            leads = list(session.scalars(query.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)))
            filters = {
                "q": q,
                "priority": priority,
                "status": status,
                "source": source,
                "service": service,
                "view": view,
            }
            return render(
                request,
                "leads.html",
                leads=leads,
                total=total,
                page=page,
                pages=max(1, -(-total // PAGE_SIZE)),
                filters=filters,
                query_string=urlencode({k: v for k, v in filters.items() if v}),
            )

    @app.get("/leads/nuevo", response_class=HTMLResponse)
    def lead_new_form(request: Request):
        return render(request, "lead_new.html", active="leads")

    @app.post("/leads/nuevo")
    def lead_new(
        instagram: str = Form(""),
        name: str = Form(""),
        whatsapp: str = Form(""),
        phone: str = Form(""),
        email: str = Form(""),
        website: str = Form(""),
        city: str = Form(""),
        bio: str = Form(""),
        source_ref: str = Form(""),
        notes: str = Form(""),
    ):
        if not any([instagram, name, whatsapp, phone, email, website]):
            return back("/leads/nuevo", "Completá al menos Instagram, nombre o un contacto.")
        profile = RawProfile(
            instagram_username=instagram or None,
            display_name=name or None,
            whatsapp=whatsapp or None,
            phone=phone or None,
            email=email or None,
            website=website or None,
            city=city or None,
            bio=bio or None,
            notes=notes or None,
        )
        with db() as (session, ws):
            result = ingest(
                session,
                ws,
                SourceRecord(
                    profile=profile, source=SourceType.MANUAL, source_ref=source_ref.strip()
                ),
                settings.default_country,
            )
            apply_score(result.lead, scoring_config())
            lead_id = result.lead.id
        msg = "Lead creado" if result.created else "Ya existía: se completaron sus datos"
        return back(f"/leads/{lead_id}", msg)

    @app.get("/leads/{lead_id}", response_class=HTMLResponse)
    def lead_detail(request: Request, lead_id: int):
        with db() as (session, ws):
            try:
                lead = get_lead(session, ws.id, lead_id)
            except LeadError:
                return back("/leads", f"No existe el lead #{lead_id}")
            draft = None
            if lead.status in CONTACTABLE_STATUSES and not lead.do_not_contact:
                draft = generate_message(lead, settings)
            config = scoring_config()
            return render(
                request,
                "lead_detail.html",
                lead=lead,
                draft=draft,
                active="leads",
                maxes=config.dimension_max,
            )

    @app.post("/leads/{lead_id}/estado")
    def lead_status(
        lead_id: int, status: LeadStatus = Form(...), note: str = Form(""), next: str = Form("")
    ):
        with db() as (session, ws):
            lead = get_lead(session, ws.id, lead_id)
            set_status(session, lead, status, note or None)
        return back(safe_next(next, f"/leads/{lead_id}"), f"Estado: {STATUS_LABELS[status]}")

    def _apply_feedback(
        session: Session, ws: Workspace, lead: Lead, value: int, reason: str
    ) -> str:
        set_feedback(session, lead, value, reason or None)
        msg = "¡Gracias! El algoritmo lo va a tener en cuenta"
        if value == -1 and reason in ("ya_es_cliente", "competidor") and lead.instagram_username:
            overrides = read_overrides(scoring_path())
            usernames = overrides.setdefault("exclusions", {}).setdefault(
                "usernames", list(scoring_config().exclusions.usernames)
            )
            if lead.instagram_username not in usernames:
                usernames.append(lead.instagram_username)
                save_overrides(scoring_path(), overrides)
            apply_score(lead, scoring_config())
            msg = f"@{lead.instagram_username} quedó excluido para siempre"
        return msg

    @app.post("/leads/{lead_id}/feedback")
    def lead_feedback(
        lead_id: int, value: int = Form(...), reason: str = Form(""), next: str = Form("")
    ):
        with db() as (session, ws):
            lead = get_lead(session, ws.id, lead_id)
            msg = _apply_feedback(session, ws, lead, value, reason)
        return back(safe_next(next, f"/leads/{lead_id}"), msg)

    @app.post("/leads/{lead_id}/no-contactar")
    def lead_dnc(lead_id: int, next: str = Form("")):
        with db() as (session, ws):
            mark_do_not_contact(session, get_lead(session, ws.id, lead_id))
        return back(safe_next(next, f"/leads/{lead_id}"), "Marcado como 'no contactar'")

    @app.post("/leads/{lead_id}/contactado")
    def lead_contacted(lead_id: int, channel: Channel = Form(...), message: str = Form("")):
        with db() as (session, ws):
            try:
                mark_contacted(session, get_lead(session, ws.id, lead_id), channel, message or None)
            except LeadError as exc:
                return back(f"/leads/{lead_id}", str(exc))
        return back(f"/leads/{lead_id}", "Contacto registrado. Seguimiento en 5 días")

    @app.post("/leads/{lead_id}/notas")
    def lead_notes(lead_id: int, notes: str = Form("")):
        with db() as (session, ws):
            get_lead(session, ws.id, lead_id).notes = notes.strip() or None
        return back(f"/leads/{lead_id}", "Notas guardadas")

    # ---------------- Revisión rápida ----------------
    @app.get("/revisar", response_class=HTMLResponse)
    def review(request: Request, skip: str = ""):
        skipped = [int(x) for x in skip.split(",") if x.isdigit()]
        with db() as (session, ws):
            query = leads_query(ws.id, status=LeadStatus.NEW).where(Lead.feedback.is_(None))
            if skipped:
                query = query.where(Lead.id.not_in(skipped))
            remaining = session.scalar(select(func.count()).select_from(query.subquery())) or 0
            lead = session.scalars(query.limit(1)).first()
            return render(
                request,
                "review.html",
                lead=lead,
                remaining=remaining,
                skip=",".join(map(str, skipped)),
                maxes=scoring_config().dimension_max,
            )

    @app.post("/revisar/{lead_id}")
    def review_action(
        lead_id: int, action: str = Form(...), reason: str = Form(""), skip: str = Form("")
    ):
        with db() as (session, ws):
            lead = get_lead(session, ws.id, lead_id)
            if action == "aprobar":
                set_status(session, lead, LeadStatus.APPROVED)
                _apply_feedback(session, ws, lead, 1, reason or "buen_perfil")
                msg = f"{lead.label} aprobado"
            elif action == "descartar":
                set_status(session, lead, LeadStatus.REJECTED, FEEDBACK_REASONS.get(reason))
                msg = _apply_feedback(session, ws, lead, -1, reason or "otro")
                if "excluido" not in msg:
                    msg = f"{lead.label} descartado"
            else:
                skip = ",".join(filter(None, [skip, str(lead_id)]))
                msg = None
        url = "/revisar" + (f"?skip={skip}" if skip else "")
        return back(url, msg)

    # ---------------- Algoritmo ----------------
    @app.get("/algoritmo", response_class=HTMLResponse)
    def algorithm(request: Request):
        with db() as (session, ws):
            config = scoring_config()
            leads = list(session.scalars(select(Lead).where(Lead.workspace_id == ws.id)))
            report = analyze(leads, config)
            sections = {
                "fit": [r for r in RULE_LABELS if r.startswith("fit.")],
                "need": [r for r in RULE_LABELS if r.startswith("need.")],
                "reach": [r for r in RULE_LABELS if r.startswith("reach.")],
            }
            insights = {i.rule: i for i in report.insights}
            return render(
                request,
                "algorithm.html",
                report=report,
                config=config,
                sections=sections,
                insights=insights,
                has_overrides=local_overrides_path(scoring_path()).exists(),
                duplicates=find_possible_duplicates(session, ws.id)[:10],
            )

    def _save_weights(values: dict[str, int], thresholds: dict[str, int] | None = None) -> None:
        overrides = read_overrides(scoring_path())
        for rule, value in values.items():
            section, name = rule.split(".", 1)
            overrides.setdefault(section, {})[name] = max(0, min(int(value), 100))
        if thresholds:
            overrides.setdefault("thresholds", {}).update(thresholds)
        save_overrides(scoring_path(), overrides)

    @app.post("/algoritmo/pesos")
    async def algorithm_weights(request: Request):
        form = await request.form()
        config = scoring_config()
        values = {}
        for rule in RULE_LABELS:
            raw = form.get(rule)
            if (
                raw not in (None, "")
                and str(raw).lstrip("-").isdigit()
                and int(raw) != get_weight(config, rule)
            ):
                values[rule] = int(raw)
        thresholds = {}
        for key in ("interesante", "posible"):
            raw = form.get(f"threshold_{key}")
            if raw and str(raw).isdigit() and int(raw) != config.thresholds.get(key):
                thresholds[key] = int(raw)
        if values or thresholds:
            _save_weights(values, thresholds)
        with db() as (session, ws):
            count = rescore_all(session, ws)
        return back("/algoritmo", f"Pesos guardados y {count} leads re-puntuados")

    @app.post("/algoritmo/aplicar")
    def algorithm_apply():
        with db() as (session, ws):
            leads = list(session.scalars(select(Lead).where(Lead.workspace_id == ws.id)))
            report = analyze(leads, scoring_config())
        if not report.suggestions:
            return back("/algoritmo", "No hay sugerencias para aplicar")
        _save_weights({i.rule: i.suggested_weight for i in report.suggestions})
        with db() as (session, ws):
            rescore_all(session, ws)
        return back("/algoritmo", f"Se aplicaron {len(report.suggestions)} ajustes")

    @app.post("/algoritmo/restaurar")
    def algorithm_reset():
        overrides = read_overrides(scoring_path())
        kept = {"exclusions": overrides["exclusions"]} if "exclusions" in overrides else {}
        save_overrides(scoring_path(), kept)
        with db() as (session, ws):
            rescore_all(session, ws)
        return back("/algoritmo", "Pesos restaurados a los valores originales")

    # ---------------- Importar / acciones ----------------
    @app.get("/importar", response_class=HTMLResponse)
    def import_page(request: Request):
        with db() as (session, ws):
            pending = (
                session.scalar(
                    select(func.count(Lead.id)).where(
                        Lead.workspace_id == ws.id, Lead.enrichment_state == EnrichmentState.PENDING
                    )
                )
                or 0
            )
        return render(
            request,
            "import.html",
            pending=pending,
            instagram_configured=settings.instagram_configured,
        )

    def _ingest_source(source, label: str) -> str:
        created = merged = 0
        with db() as (session, ws):
            config = scoring_config()
            for record in source.discover():
                result = ingest(session, ws, record, settings.default_country)
                created += result.created
                merged += not result.created
                apply_score(result.lead, config)
        return f"{label}: {created} nuevos, {merged} ya existentes"

    def _save_upload(upload: UploadFile) -> Path:
        """Guarda el archivo en una carpeta temporal conservando el nombre (el export lo usa)."""
        name = Path(upload.filename or "archivo").name.replace("..", "")
        folder = Path(tempfile.mkdtemp(prefix="cmr_upload_"))
        path = folder / name
        with open(path, "wb") as fh:
            shutil.copyfileobj(upload.file, fh)
        return path

    @app.post("/importar/instagram")
    def import_instagram(file: UploadFile, account: str = Form("")):
        name = (file.filename or "").lower()
        if not name.endswith((".zip", ".json")):
            return back("/importar", "Subí el .zip del export o un .json de seguidores/seguidos")
        path = _save_upload(file)
        try:
            msg = _ingest_source(
                InstagramExportSource(path, account.strip().lstrip("@")), "Instagram"
            )
            if msg.startswith("Instagram: 0 nuevos, 0"):
                msg = (
                    "No se encontraron seguidores/seguidos. ¿Elegiste el formato JSON al exportar?"
                )
        except (ValueError, KeyError, zipfile.BadZipFile) as exc:
            msg = f"No se pudo leer el archivo: {exc}"
        finally:
            shutil.rmtree(path.parent, ignore_errors=True)
        return back("/importar", msg)

    @app.post("/importar/csv")
    def import_csv(file: UploadFile):
        if not (file.filename or "").lower().endswith(".csv"):
            return back("/importar", "Subí un archivo .csv")
        path = _save_upload(file)
        try:
            msg = _ingest_source(CsvSource(path), "CSV")
        except (ValueError, UnicodeDecodeError, csv.Error) as exc:
            msg = f"No se pudo leer el CSV: {exc}"
        finally:
            shutil.rmtree(path.parent, ignore_errors=True)
        return back("/importar", msg)

    @app.post("/acciones/enriquecer")
    def action_enrich(limit: int = Form(20)):
        from cmr_leads.enrichment.instagram_discovery import BusinessDiscoveryClient
        from cmr_leads.enrichment.website import fetch_website_signals
        from cmr_leads.pipeline.enrich import enrich_pending

        lookup = None
        if settings.instagram_configured:
            lookup = BusinessDiscoveryClient(
                settings.meta_access_token,
                settings.instagram_business_account_id,
                settings.meta_graph_api_version,
            )
        fetcher = None
        if settings.website_fetch_enabled:

            def fetcher(url: str):
                return fetch_website_signals(url, timeout=settings.website_timeout_seconds)

        with db() as (session, ws):
            summary = enrich_pending(
                session,
                ws,
                lookup,
                settings.meta_max_lookups_per_day,
                settings.default_country,
                limit=min(max(limit, 1), 100),
                website_fetcher=fetcher,
            )
            config = scoring_config()
            for lead_id in dict.fromkeys(summary.lead_ids):
                if (lead := session.get(Lead, lead_id)) is not None:
                    apply_score(lead, config)
        parts = [
            f"{summary.found} encontrados",
            f"{summary.not_found} no comerciales",
            f"{summary.websites_checked} webs",
        ]
        if summary.skipped_no_api:
            parts.append(f"{summary.skipped_no_api} esperan la API de Instagram")
        if summary.stopped_reason:
            parts.append(f"se detuvo: {summary.stopped_reason}")
        return back("/importar", " · ".join(parts))

    @app.post("/acciones/puntuar")
    def action_score():
        with db() as (session, ws):
            count = rescore_all(session, ws)
        return back("/importar", f"{count} leads re-puntuados")

    @app.get("/exportar")
    def export(format: str = "xlsx", status: str = ""):
        fmt = "csv" if format == "csv" else "xlsx"
        path = Path(tempfile.gettempdir()) / f"cmr_leads_{datetime.now():%Y%m%d_%H%M%S}.{fmt}"
        with db() as (session, ws):
            leads = list(session.scalars(leads_query(ws.id, status=status or None)))
            (export_csv if fmt == "csv" else export_xlsx)(leads, path)
        return FileResponse(path, filename=path.name)

    return app

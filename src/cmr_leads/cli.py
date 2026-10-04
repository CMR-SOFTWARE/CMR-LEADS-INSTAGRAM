"""CLI de CMR Lead Finder: `cmr-leads --help`."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from sqlalchemy import select
from sqlalchemy.orm import Session

from cmr_leads.db.models import Lead, Workspace
from cmr_leads.db.session import (
    get_or_create_workspace,
    make_engine,
    make_session_factory,
    run_migrations,
)
from cmr_leads.domain.enums import Channel, EnrichmentState, LeadStatus, SourceType
from cmr_leads.domain.schemas import RawProfile, SourceRecord
from cmr_leads.exports.exporter import export_csv, export_xlsx
from cmr_leads.messaging.generator import generate_message
from cmr_leads.pipeline.dedup import find_possible_duplicates, merge_leads
from cmr_leads.pipeline.enrich import PROVIDER_BUSINESS_DISCOVERY, enrich_pending
from cmr_leads.pipeline.ingest import ingest
from cmr_leads.pipeline.scoring import apply_score, load_scoring_config
from cmr_leads.services import (
    CONTACTABLE_STATUSES,
    LeadError,
    get_lead,
    leads_query,
    mark_contacted,
    mark_do_not_contact,
    set_status,
    source_stats,
)
from cmr_leads.settings import get_settings
from cmr_leads.sources.csv_import import CsvSource
from cmr_leads.sources.instagram_export import InstagramExportSource

app = typer.Typer(
    help="Prospección de potenciales clientes locales para CMR.", no_args_is_help=True
)
import_app = typer.Typer(help="Importar leads desde fuentes.", no_args_is_help=True)
leads_app = typer.Typer(help="Ver y gestionar leads.", no_args_is_help=True)
app.add_typer(import_app, name="import")
app.add_typer(leads_app, name="leads")

console = Console()


@contextmanager
def workspace_session() -> Iterator[tuple[Session, Workspace]]:
    settings = get_settings()
    factory = make_session_factory(make_engine(settings.database_url))
    with factory() as session:
        workspace = get_or_create_workspace(session, settings.workspace)
        try:
            yield session, workspace
            session.commit()
        except Exception:
            session.rollback()
            raise


def _fail(message: str) -> None:
    console.print(f"[red]{message}[/red]")
    raise typer.Exit(1)


def _score_leads(session: Session, leads: list[Lead]) -> None:
    config = load_scoring_config(get_settings().scoring_config)
    for lead in leads:
        apply_score(lead, config)


def _ingest_all(source, label: str) -> None:
    settings = get_settings()
    created = merged = 0
    with workspace_session() as (session, workspace):
        touched: dict[int, Lead] = {}
        for record in source.discover():
            result = ingest(session, workspace, record, settings.default_country)
            created += result.created
            merged += not result.created
            touched[result.lead.id] = result.lead
        _score_leads(session, [lead for lead in touched.values() if lead in session])
    console.print(f"[green]{label}:[/green] {created} nuevos, {merged} ya existentes (fusionados).")
    console.print("Siguiente paso: [bold]cmr-leads enrich[/bold]")


@app.command()
def init() -> None:
    """Crea/actualiza la base de datos (migraciones) y el workspace."""
    settings = get_settings()
    run_migrations(settings.database_url)
    with workspace_session() as (_, workspace):
        console.print(
            f"[green]Base lista[/green] ({settings.database_url}) — workspace '{workspace.slug}'."
        )
    if not settings.instagram_configured:
        console.print(
            "[yellow]Instagram API no configurada:[/yellow] completá META_ACCESS_TOKEN e "
            "INSTAGRAM_BUSINESS_ACCOUNT_ID en .env para enriquecer perfiles."
        )


@import_app.command("instagram-export")
def import_instagram_export(
    path: Annotated[Path, typer.Argument(exists=True, help=".zip, carpeta o .json del export")],
    account: Annotated[str, typer.Option(help="Username de la cuenta exportada")] = "",
) -> None:
    """Importa seguidores/seguidos desde el export oficial de Instagram."""
    _ingest_all(InstagramExportSource(path, account), "Export de Instagram")


@import_app.command("csv")
def import_csv(path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)]) -> None:
    """Importa leads desde un CSV (ver README para las columnas)."""
    _ingest_all(CsvSource(path), "CSV")


@app.command()
def add(
    username: Annotated[str | None, typer.Argument(help="@usuario de Instagram")] = None,
    name: Annotated[str | None, typer.Option(help="Nombre del negocio")] = None,
    phone: Annotated[str | None, typer.Option()] = None,
    whatsapp: Annotated[str | None, typer.Option()] = None,
    email: Annotated[str | None, typer.Option()] = None,
    website: Annotated[str | None, typer.Option()] = None,
    city: Annotated[str | None, typer.Option()] = None,
    note: Annotated[str | None, typer.Option()] = None,
    source_ref: Annotated[str, typer.Option(help="Perfil u origen que llevó a este lead")] = "",
) -> None:
    """Carga un lead a mano."""
    if not any([username, name, phone, whatsapp, email, website]):
        _fail("Indicá al menos un username, nombre, teléfono, email o sitio.")
    profile = RawProfile(
        instagram_username=username,
        display_name=name,
        phone=phone,
        whatsapp=whatsapp,
        email=email,
        website=website,
        city=city,
        notes=note,
    )
    settings = get_settings()
    with workspace_session() as (session, workspace):
        result = ingest(
            session,
            workspace,
            SourceRecord(profile=profile, source=SourceType.MANUAL, source_ref=source_ref),
            settings.default_country,
        )
        _score_leads(session, [result.lead])
        verb = "creado" if result.created else "ya existía; se actualizó"
        console.print(f"Lead #{result.lead.id} {result.lead.label} {verb}.")


@app.command()
def enrich(
    limit: Annotated[int, typer.Option(help="Máximo de leads a procesar")] = 20,
    website: Annotated[bool, typer.Option(help="Analizar el sitio web público")] = True,
) -> None:
    """Completa datos con Business Discovery (Instagram) y el sitio web público."""
    from cmr_leads.enrichment.instagram_discovery import BusinessDiscoveryClient
    from cmr_leads.enrichment.website import fetch_website_signals

    settings = get_settings()
    lookup = None
    if settings.instagram_configured:
        lookup = BusinessDiscoveryClient(
            settings.meta_access_token,
            settings.instagram_business_account_id,
            settings.meta_graph_api_version,
        )
    fetcher = None
    if website and settings.website_fetch_enabled:

        def fetcher(url: str):
            return fetch_website_signals(url, timeout=settings.website_timeout_seconds)

    with workspace_session() as (session, workspace):
        summary = enrich_pending(
            session,
            workspace,
            lookup,
            settings.meta_max_lookups_per_day,
            settings.default_country,
            limit=limit,
            website_fetcher=fetcher,
        )
        leads = [session.get(Lead, i) for i in dict.fromkeys(summary.lead_ids)]
        _score_leads(session, [lead for lead in leads if lead is not None])

    console.print(
        f"Encontrados: {summary.found} · No comerciales/inexistentes: {summary.not_found} · "
        f"Errores: {summary.errors} · Sitios analizados: {summary.websites_checked}"
    )
    if summary.skipped_no_api:
        console.print(
            f"[yellow]{summary.skipped_no_api} leads de Instagram quedan pendientes:[/yellow] "
            "configurá la API de Instagram en .env."
        )
    if summary.stopped_reason:
        console.print(f"[yellow]Se detuvo:[/yellow] {summary.stopped_reason}")


@app.command()
def score(all_: Annotated[bool, typer.Option("--all", help="Re-puntuar todos")] = False) -> None:
    """Calcula el score de los leads (todos con --all, o los que no tienen score)."""
    with workspace_session() as (session, workspace):
        query = select(Lead).where(Lead.workspace_id == workspace.id)
        if not all_:
            query = query.where(Lead.business_score.is_(None))
        leads = list(session.scalars(query))
        _score_leads(session, leads)
    console.print(f"{len(leads)} leads puntuados.")


def _fmt_contact(lead: Lead) -> str:
    if lead.whatsapp_e164:
        return f"WA {lead.whatsapp_e164}"
    if lead.phone_e164:
        return f"Tel {lead.phone_e164}"
    return lead.email or "-"


@leads_app.command("list")
def leads_list(
    min_score: Annotated[int | None, typer.Option()] = None,
    status: Annotated[LeadStatus | None, typer.Option()] = None,
    priority: Annotated[str | None, typer.Option(help="baja | posible | interesante")] = None,
    source: Annotated[SourceType | None, typer.Option()] = None,
    include_excluded: Annotated[bool, typer.Option("--include-excluded")] = False,
    limit: Annotated[int, typer.Option()] = 50,
) -> None:
    """Lista leads ordenados por score."""
    with workspace_session() as (session, workspace):
        query = leads_query(workspace.id, min_score, status, priority, source, include_excluded)
        leads = list(session.scalars(query.limit(limit)))
        table = Table(title=f"Leads ({len(leads)})")
        for column in (
            "ID",
            "Negocio",
            "Instagram",
            "Rubro",
            "Score",
            "Prioridad",
            "Contacto",
            "Servicio",
            "Estado",
        ):
            table.add_column(column)
        for lead in leads:
            score_text = "-" if lead.business_score is None else str(lead.business_score)
            if lead.excluded:
                score_text = "[dim]excl.[/dim]"
            table.add_row(
                str(lead.id),
                lead.display_name or "-",
                f"@{lead.instagram_username}" if lead.instagram_username else "-",
                lead.business_type or "-",
                score_text,
                lead.priority or "-",
                _fmt_contact(lead),
                lead.recommended_service or "-",
                lead.status,
            )
        console.print(table)


def _show(lead: Lead) -> None:
    lines = [
        f"[bold]{lead.label}[/bold]  (#{lead.id})",
        f"Instagram: {lead.instagram_url or '-'}  ·  seguidores: {lead.followers or '-'}",
        f"Rubro: {lead.business_type or '-'}  ·  Categoría: {lead.category or '-'}  ·  Ciudad: {lead.city or '-'}",
        f"Web: {lead.website or '-'}{'  (solo link en bio)' if lead.has_link_in_bio_only else ''}",
        f"WhatsApp: {lead.whatsapp_e164 or '-'}{' ✓ publicado' if lead.whatsapp_confirmed else ''}"
        f"  ·  Tel: {lead.phone_e164 or '-'}  ·  Email: {lead.email or '-'}",
        f"Bio: {lead.bio or '-'}",
        "",
        f"Score: [bold]{lead.business_score if lead.business_score is not None else '-'}[/bold] "
        f"(fit {lead.fit_score} · need {lead.need_score} · reach {lead.reach_score}) → {lead.priority}",
    ]
    if lead.excluded:
        lines.append(f"[red]Excluido:[/red] {lead.exclusion_reason}")
    for item in lead.score_breakdown or []:
        lines.append(f"  {item['points']:+d} [{item['dimension']}] {item['reason']}")
    lines += [
        "",
        f"Servicio sugerido: {lead.recommended_service or '-'}",
        f"Estado: {lead.status}{'  ·  NO CONTACTAR' if lead.do_not_contact else ''}"
        f"  ·  Enriquecimiento: {lead.enrichment_state}",
        "Fuentes: "
        + ", ".join(
            f"{s.source}{':' + s.source_ref if s.source_ref else ''}" for s in lead.sources
        ),
    ]
    if lead.notes:
        lines.append(f"Notas: {lead.notes}")
    for interaction in lead.interactions:
        lines.append(
            f"  {interaction.created_at:%Y-%m-%d} {interaction.channel} {interaction.direction} {interaction.outcome or ''}"
        )
    console.print(Panel("\n".join(lines), expand=False))


@leads_app.command("show")
def leads_show(lead_id: int) -> None:
    """Muestra el detalle y el desglose del score de un lead."""
    with workspace_session() as (session, workspace):
        try:
            _show(get_lead(session, workspace.id, lead_id))
        except LeadError as exc:
            _fail(str(exc))


@leads_app.command("duplicates")
def leads_duplicates(threshold: Annotated[float, typer.Option()] = 90.0) -> None:
    """Posibles duplicados por nombre parecido (para revisar y fusionar a mano)."""
    with workspace_session() as (session, workspace):
        pairs = find_possible_duplicates(session, workspace.id, threshold)
        if not pairs:
            console.print("Sin posibles duplicados.")
        for pair in pairs:
            console.print(
                f"#{pair.a.id} {pair.a.label}  ↔  #{pair.b.id} {pair.b.label}  ({pair.similarity:.0f}%)"
            )


@leads_app.command("merge")
def leads_merge(keep_id: int, drop_id: int) -> None:
    """Fusiona el lead DROP_ID dentro de KEEP_ID."""
    with workspace_session() as (session, workspace):
        try:
            keep = get_lead(session, workspace.id, keep_id)
            drop = get_lead(session, workspace.id, drop_id)
        except LeadError as exc:
            _fail(str(exc))
        merge_leads(session, keep, drop)
        _score_leads(session, [keep])
    console.print(f"Lead #{drop_id} fusionado en #{keep_id}.")


@app.command()
def review(min_score: Annotated[int, typer.Option()] = 40) -> None:
    """Revisión interactiva: aprobar, rechazar o marcar como 'no contactar'."""
    with workspace_session() as (session, workspace):
        query = leads_query(workspace.id, min_score=min_score, status=LeadStatus.NEW).where(
            Lead.enrichment_state != EnrichmentState.PENDING
        )
        leads = list(session.scalars(query))
        if not leads:
            console.print("No hay leads para revisar.")
            return
        for index, lead in enumerate(leads, 1):
            console.rule(f"{index}/{len(leads)}")
            _show(lead)
            choice = typer.prompt(
                "[a]probar  [r]echazar  [n]o contactar  [s]altar  [q]salir", default="s"
            ).lower()
            if choice == "q":
                break
            if choice == "a":
                set_status(session, lead, LeadStatus.APPROVED)
            elif choice == "r":
                set_status(
                    session, lead, LeadStatus.REJECTED, typer.prompt("Motivo", default="") or None
                )
            elif choice == "n":
                mark_do_not_contact(session, lead)
            session.commit()


@app.command()
def status(
    lead_id: int, new_status: LeadStatus, note: Annotated[str | None, typer.Option()] = None
) -> None:
    """Cambia el estado de un lead (ej.: replied, meeting, won, lost)."""
    with workspace_session() as (session, workspace):
        try:
            set_status(session, get_lead(session, workspace.id, lead_id), new_status, note)
        except LeadError as exc:
            _fail(str(exc))
    console.print(f"Lead #{lead_id} → {new_status}")


@app.command()
def dnc(lead_id: int, note: Annotated[str | None, typer.Option()] = None) -> None:
    """Marca un lead como 'no contactar' (permanente)."""
    with workspace_session() as (session, workspace):
        try:
            mark_do_not_contact(session, get_lead(session, workspace.id, lead_id), note)
        except LeadError as exc:
            _fail(str(exc))
    console.print(f"Lead #{lead_id} marcado como no contactar.")


@app.command()
def message(
    lead_id: int,
    force: Annotated[bool, typer.Option(help="Generar aunque no esté aprobado")] = False,
) -> None:
    """Genera un BORRADOR de mensaje + link para enviarlo a mano."""
    settings = get_settings()
    with workspace_session() as (session, workspace):
        try:
            lead = get_lead(session, workspace.id, lead_id)
        except LeadError as exc:
            _fail(str(exc))
        if lead.do_not_contact:
            _fail(f"{lead.label} está marcado como 'no contactar'.")
        if lead.status not in CONTACTABLE_STATUSES and not force:
            _fail(f"{lead.label} está en estado '{lead.status}'. Aprobalo primero (o usá --force).")
        draft = generate_message(lead, settings)
    console.print(
        Panel(draft.text, title=f"Borrador para {lead.label} ({draft.template})", expand=False)
    )
    if draft.link:
        console.print(f"Canal sugerido: [bold]{draft.channel}[/bold] → {draft.link}")
    console.print(
        "Revisá y editá el mensaje antes de enviarlo. Después: "
        f"[bold]cmr-leads contacted {lead_id} --channel {draft.channel}[/bold]"
    )


@app.command()
def contacted(
    lead_id: int,
    channel: Annotated[Channel, typer.Option()] = Channel.WHATSAPP,
    note: Annotated[str | None, typer.Option(help="Mensaje enviado o comentario")] = None,
) -> None:
    """Registra que se contactó al lead (programa seguimiento a 5 días)."""
    with workspace_session() as (session, workspace):
        try:
            lead = get_lead(session, workspace.id, lead_id)
            mark_contacted(session, lead, channel, note)
        except LeadError as exc:
            _fail(str(exc))
    console.print(f"Contacto registrado. Seguimiento: {lead.next_follow_up_at:%Y-%m-%d}.")


@app.command()
def export(
    fmt: Annotated[str, typer.Option("--format", help="csv | xlsx")] = "csv",
    min_score: Annotated[int | None, typer.Option()] = None,
    status: Annotated[LeadStatus | None, typer.Option()] = None,
    include_excluded: Annotated[bool, typer.Option("--include-excluded")] = False,
    output: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Exporta leads a CSV o Excel (carpeta exports/)."""
    if fmt not in ("csv", "xlsx"):
        _fail("Formato inválido: usá csv o xlsx.")
    path = output or Path("exports") / f"leads_{datetime.now():%Y%m%d_%H%M}.{fmt}"
    with workspace_session() as (session, workspace):
        leads = list(
            session.scalars(
                leads_query(workspace.id, min_score, status, include_excluded=include_excluded)
            )
        )
        (export_csv if fmt == "csv" else export_xlsx)(leads, path)
    console.print(f"{len(leads)} leads exportados a {path}")


@app.command()
def stats() -> None:
    """Resultados por fuente y uso de la API."""
    from cmr_leads.pipeline.budget import DailyBudget

    settings = get_settings()
    with workspace_session() as (session, workspace):
        table = Table(title="Leads por fuente")
        for column in (
            "Fuente",
            "Total",
            "Interesantes",
            "Aprobados",
            "Contactados",
            "Respondieron",
        ):
            table.add_column(column, no_wrap=column == "Fuente")
        for row in source_stats(session, workspace.id):
            table.add_row(
                row.source,
                str(row.total),
                str(row.interesting),
                str(row.approved),
                str(row.contacted),
                str(row.replied),
            )
        console.print(table)
        budget = DailyBudget(
            session, workspace.id, PROVIDER_BUSINESS_DISCOVERY, settings.meta_max_lookups_per_day
        )
        console.print(
            f"Business Discovery: {budget.used()}/{budget.limit} consultas en las últimas 24 h."
        )


if __name__ == "__main__":
    app()

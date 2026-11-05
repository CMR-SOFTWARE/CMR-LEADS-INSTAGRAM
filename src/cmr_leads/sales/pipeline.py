"""Reglas del embudo: etapas, motivo de pérdida, contactos, seguimientos y negocios trabados."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from cmr_leads.db.models import Interaction, Lead, Meeting, utcnow
from cmr_leads.domain.enums import Channel, LeadStatus, LostReason, WebStatus
from cmr_leads.sales.config import SalesConfig
from cmr_leads.services import LeadError, mark_contacted, set_status

# Orden del embudo (Descartado/Perdido quedan afuera: son salidas).
FUNNEL = [
    LeadStatus.NEW,
    LeadStatus.APPROVED,
    LeadStatus.CONTACTED,
    LeadStatus.REPLIED,
    LeadStatus.MEETING,
    LeadStatus.PROPOSAL,
    LeadStatus.WON,
]
STAGE_LABELS = {
    LeadStatus.NEW: "Nuevo",
    LeadStatus.APPROVED: "Calificado",
    LeadStatus.CONTACTED: "Contactado",
    LeadStatus.REPLIED: "Respondió",
    LeadStatus.MEETING: "Reunión",
    LeadStatus.PROPOSAL: "Propuesta",
    LeadStatus.WON: "Ganado",
    LeadStatus.LOST: "Perdido",
    LeadStatus.REJECTED: "Descartado",
}
LOST_REASON_LABELS = {
    LostReason.PRICE: "Precio",
    LostReason.TIMING: "Timing / más adelante",
    LostReason.NOT_INTERESTED: "No le interesa",
    LostReason.CHOSE_OTHER: "Eligió a otro",
    LostReason.NO_RESPONSE: "Sin respuesta",
}
WEB_STATUS_LABELS = {WebStatus.NONE: "No tiene", WebStatus.OK: "Sí", WebStatus.OLD: "Vieja"}


def stage_index(status: str) -> int:
    return FUNNEL.index(status) if status in FUNNEL else -1


def change_stage(
    session: Session,
    lead: Lead,
    stage: LeadStatus,
    *,
    lost_reason: str | None = None,
    note: str | None = None,
) -> None:
    """Cambia la etapa. Perder un negocio exige motivo."""
    if stage == LeadStatus.LOST:
        if lost_reason not in {r.value for r in LostReason}:
            raise LeadError("Para marcarlo como perdido elegí un motivo")
        lead.lost_reason = lost_reason
        note = note or LOST_REASON_LABELS[LostReason(lost_reason)]
    elif stage != lead.status:
        lead.lost_reason = None
    set_status(session, lead, stage, note)
    if stage in (LeadStatus.WON, LeadStatus.LOST, LeadStatus.REJECTED):
        lead.next_follow_up_at = None
        lead.next_step = None


def register_contact(
    session: Session,
    lead: Lead,
    channel: Channel,
    config: SalesConfig,
    message: str | None = None,
    template: str | None = None,
) -> Interaction:
    """Registra un mensaje enviado y agenda el próximo seguimiento (día 3, día 7, cierre)."""
    interaction = mark_contacted(session, lead, channel, message, template)
    outbound = [i for i in lead.interactions if i.direction == "outbound"]
    first = min(i.created_at for i in outbound)
    days = sorted(config.follow_up_days)
    sent = len(outbound)
    if sent <= len(days):
        lead.next_follow_up_at = first + timedelta(days=days[sent - 1])
        lead.next_step = f"Seguimiento {sent} (día {days[sent - 1]})"
    else:
        lead.next_follow_up_at = first + timedelta(days=config.close_after_days)
        lead.next_step = "Cerrar si no responde (sin respuesta)"
    session.flush()
    return interaction


def register_meeting(
    session: Session,
    lead: Lead,
    scheduled_at: datetime,
    notes: str | None,
    next_steps: str | None,
    next_step_at: datetime | None = None,
) -> Meeting:
    meeting = Meeting(scheduled_at=scheduled_at, notes=notes, next_steps=next_steps)
    lead.meetings.append(meeting)
    if stage_index(lead.status) < stage_index(LeadStatus.MEETING):
        set_status(session, lead, LeadStatus.MEETING, "reunión registrada")
    if next_steps:
        lead.next_step = next_steps[:300]
    lead.next_follow_up_at = next_step_at or (
        scheduled_at if scheduled_at > utcnow() else lead.next_follow_up_at
    )
    session.flush()
    return meeting


@dataclass
class Alert:
    lead: Lead
    kind: str  # "seguimiento" | "trabado" | "cerrar"
    detail: str
    days: int


def follow_up_alert(lead: Lead, now: datetime) -> Alert | None:
    """Seguimiento vencido: tiene fecha de próximo paso en el pasado y sigue activo."""
    if (
        lead.status
        not in (LeadStatus.CONTACTED, LeadStatus.REPLIED, LeadStatus.MEETING, LeadStatus.PROPOSAL)
        or lead.do_not_contact
    ):
        return None
    if lead.next_follow_up_at is None or lead.next_follow_up_at > now:
        return None
    days = (now - lead.next_follow_up_at).days
    detail = lead.next_step or "Seguimiento pendiente"
    kind = "cerrar" if detail.startswith("Cerrar") else "seguimiento"
    return Alert(lead, kind, detail, days)


def stuck_alert(lead: Lead, now: datetime, config: SalesConfig) -> Alert | None:
    """Negocio trabado: superó los días máximos de su etapa."""
    limit = config.stuck_days.get(lead.status)
    if limit is None or lead.do_not_contact:
        return None
    since = lead.stage_entered_at or lead.updated_at or lead.first_seen_at
    days = (now - since).days
    if days <= limit:
        return None
    label = STAGE_LABELS.get(lead.status, lead.status)
    return Alert(lead, "trabado", f"{days} días en {label} (máx. {limit})", days)


def web_status_of(lead: Lead) -> WebStatus:
    """Sí / no / vieja, a partir de la web y del análisis de la home (si se hizo)."""
    if not lead.website or lead.has_link_in_bio_only:
        return WebStatus.NONE
    signals = (lead.raw_data or {}).get("website") or {}
    if signals and (
        not signals.get("reachable")
        or signals.get("mobile_viewport") is False
        or signals.get("https") is False
    ):
        return WebStatus.OLD
    return WebStatus.OK

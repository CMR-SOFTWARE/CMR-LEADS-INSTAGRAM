"""Cola de emails por lotes. Nada se envía solo: vos aprobás el lote (con tope diario) y cada
email se abre en tu programa de correo (mailto:) para que lo mandes vos. Sin Gmail API."""

from dataclasses import dataclass
from datetime import date
from urllib.parse import quote

from sqlalchemy.orm import Session

from cmr_leads.db.models import Lead
from cmr_leads.domain.enums import LeadStatus
from cmr_leads.messaging.generator import generate_message
from cmr_leads.sales.repository import SqlSalesRepository
from cmr_leads.settings import Settings

QUEUE_KEY = "email_queue"


@dataclass
class QueuedEmail:
    lead: Lead
    subject: str
    body: str

    @property
    def mailto(self) -> str:
        return f"mailto:{self.lead.email}?subject={quote(self.subject)}&body={quote(self.body)}"


def _queue(lead: Lead) -> dict:
    return (lead.raw_data or {}).get(QUEUE_KEY) or {}


def candidates(session: Session, workspace_id: int) -> list[Lead]:
    """Calificados con email que todavía no se contactaron ni están en la cola."""
    repo = SqlSalesRepository(session, workspace_id)
    return [
        lead
        for lead in repo.leads_in_stages([LeadStatus.APPROVED])
        if lead.email and not _queue(lead)
    ]


def approved_today(session: Session, workspace_id: int, today: date) -> list[Lead]:
    repo = SqlSalesRepository(session, workspace_id)
    return [
        lead
        for lead in repo.leads_in_stages([LeadStatus.APPROVED, LeadStatus.CONTACTED])
        if _queue(lead).get("approved_on") == today.isoformat()
    ]


def approve_batch(
    session: Session, workspace_id: int, lead_ids: list[int], today: date, daily_limit: int
) -> tuple[int, int]:
    """Aprueba hasta completar el tope del día. Devuelve (aprobados, fuera_de_tope)."""
    repo = SqlSalesRepository(session, workspace_id)
    room = max(0, daily_limit - len(approved_today(session, workspace_id, today)))
    approved = 0
    for lead_id in lead_ids:
        lead = repo.get_lead(lead_id)
        if lead is None or not lead.email or lead.do_not_contact or _queue(lead):
            continue
        if approved >= room:
            break
        lead.raw_data = {**(lead.raw_data or {}), QUEUE_KEY: {"approved_on": today.isoformat()}}
        approved += 1
    repo.flush()
    return approved, max(0, len(lead_ids) - approved)


def build_email(lead: Lead, settings: Settings) -> QueuedEmail:
    body = (lead.raw_data or {}).get("ai_message") or generate_message(lead, settings).text
    subject = f"Una idea para {lead.display_name or 'su negocio'}"
    return QueuedEmail(lead, subject[:90], body)

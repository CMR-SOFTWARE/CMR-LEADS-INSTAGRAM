"""Casos de uso sobre leads (los usa la CLI y, más adelante, la API web)."""

from collections import Counter
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from cmr_leads.db.models import Interaction, Lead, LeadSource, StatusChange, utcnow
from cmr_leads.domain.enums import Channel, LeadStatus

FOLLOW_UP_DAYS = 3
CONTACTABLE_STATUSES = {
    LeadStatus.APPROVED,
    LeadStatus.CONTACTED,
    LeadStatus.REPLIED,
    LeadStatus.MEETING,
    LeadStatus.PROPOSAL,
}


class LeadError(Exception):
    pass


def get_lead(session: Session, workspace_id: int, lead_id: int) -> Lead:
    lead = session.get(Lead, lead_id)
    if lead is None or lead.workspace_id != workspace_id:
        raise LeadError(f"No existe el lead #{lead_id}")
    return lead


def leads_query(
    workspace_id: int,
    min_score: int | None = None,
    status: str | None = None,
    priority: str | None = None,
    source: str | None = None,
    include_excluded: bool = False,
) -> Select[tuple[Lead]]:
    query = select(Lead).where(Lead.workspace_id == workspace_id)
    if not include_excluded:
        query = query.where(Lead.excluded.is_(False), Lead.do_not_contact.is_(False))
    if min_score is not None:
        query = query.where(Lead.business_score >= min_score)
    if status:
        query = query.where(Lead.status == status)
    if priority:
        query = query.where(Lead.priority == priority)
    if source:
        query = query.where(Lead.sources.any(LeadSource.source == source))
    # Primero la probabilidad de venta que estimó la IA; si no hay, el puntaje del algoritmo.
    ranking = func.coalesce(Lead.ai_score, Lead.business_score)
    return query.order_by(ranking.desc().nulls_last(), Lead.id)


def set_status(session: Session, lead: Lead, status: LeadStatus, note: str | None = None) -> None:
    if lead.status == status:
        return
    lead.status_changes.append(StatusChange(from_status=lead.status, to_status=status, note=note))
    lead.status = status
    lead.stage_entered_at = utcnow()
    lead.updated_at = utcnow()
    session.flush()


def mark_do_not_contact(session: Session, lead: Lead, note: str | None = None) -> None:
    lead.do_not_contact = True
    if lead.status in (LeadStatus.NEW, LeadStatus.APPROVED):
        set_status(session, lead, LeadStatus.REJECTED, note or "no contactar")
    elif lead.status in (LeadStatus.CONTACTED, LeadStatus.REPLIED):
        set_status(session, lead, LeadStatus.LOST, note or "no contactar")
    lead.next_follow_up_at = None
    session.flush()


DUPLICATE_CONTACT_MINUTES = 10


def recent_contact(lead: Lead, channel: Channel, minutes: int = DUPLICATE_CONTACT_MINUTES):
    """El envío del mismo canal hecho hace menos de `minutes` (doble clic, reintento), o None."""
    since = utcnow() - timedelta(minutes=minutes)
    for i in lead.interactions:
        if i.direction == "outbound" and i.channel == channel and i.created_at >= since:
            return i
    return None


def mark_contacted(
    session: Session,
    lead: Lead,
    channel: Channel,
    message: str | None = None,
    template: str | None = None,
) -> Interaction:
    if lead.do_not_contact:
        raise LeadError(f"{lead.label} está marcado como 'no contactar'")
    interaction = Interaction(
        channel=channel, direction="outbound", message=message, template=template
    )
    lead.interactions.append(interaction)
    lead.contacted_at = utcnow()
    lead.next_follow_up_at = utcnow() + timedelta(days=FOLLOW_UP_DAYS)
    if lead.status in (LeadStatus.NEW, LeadStatus.APPROVED):
        set_status(session, lead, LeadStatus.CONTACTED)
    session.flush()
    return interaction


@dataclass
class SourceStats:
    source: str
    total: int
    interesting: int
    approved: int
    contacted: int
    replied: int


def source_stats(session: Session, workspace_id: int) -> list[SourceStats]:
    """Qué fuentes generan mejores leads (un lead cuenta en cada fuente que lo trajo)."""
    rows = session.execute(
        select(LeadSource.source, Lead)
        .join(Lead, Lead.id == LeadSource.lead_id)
        .where(Lead.workspace_id == workspace_id)
    ).all()
    buckets: dict[str, Counter] = {}
    for source, lead in rows:
        counter = buckets.setdefault(source, Counter())
        counter["total"] += 1
        counter["interesting"] += lead.priority == "interesante" and not lead.excluded
        counter["approved"] += lead.status != LeadStatus.NEW and lead.status != LeadStatus.REJECTED
        counter["contacted"] += lead.contacted_at is not None
        counter["replied"] += lead.status in (
            LeadStatus.REPLIED,
            LeadStatus.MEETING,
            LeadStatus.WON,
        )
    return [
        SourceStats(
            source, c["total"], c["interesting"], c["approved"], c["contacted"], c["replied"]
        )
        for source, c in sorted(buckets.items())
    ]


def set_feedback(
    session: Session, lead: Lead, value: int | None, reason: str | None = None
) -> None:
    """Feedback para entrenar el scoring: 1 = buen lead, -1 = mal lead, None = borrar."""
    lead.feedback = value
    lead.feedback_reason = reason if value is not None else None
    lead.feedback_at = utcnow() if value is not None else None
    session.flush()


def follow_ups_due(session: Session, workspace_id: int) -> list[Lead]:
    return list(
        session.scalars(
            select(Lead)
            .where(
                Lead.workspace_id == workspace_id,
                Lead.next_follow_up_at.is_not(None),
                Lead.next_follow_up_at <= utcnow(),
                Lead.status == LeadStatus.CONTACTED,
                Lead.do_not_contact.is_(False),
            )
            .order_by(Lead.next_follow_up_at)
        )
    )

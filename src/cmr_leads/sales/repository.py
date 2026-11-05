"""Acceso a datos del embudo de ventas.

Las pantallas y reglas de ventas hablan con `SalesRepository` (un Protocol), nunca con
SQLAlchemy directo. Hoy la implementación es SQLite local; para pasar a Supabase/Postgres
alcanza con escribir otra clase que cumpla el mismo contrato (o apuntar DATABASE_URL a Postgres,
ya que la implementación usa SQLAlchemy sin nada específico de SQLite).
"""

from datetime import datetime
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from cmr_leads.db.models import Interaction, Lead, LeadSource, Meeting, Proposal, StatusChange
from cmr_leads.domain.enums import LeadStatus


class SalesRepository(Protocol):
    def get_lead(self, lead_id: int) -> Lead | None: ...
    def all_leads(self) -> list[Lead]: ...
    def leads_in_stages(self, stages: list[str]) -> list[Lead]: ...
    def add_meeting(self, lead: Lead, meeting: Meeting) -> Meeting: ...
    def add_proposal(self, lead: Lead, proposal: Proposal) -> Proposal: ...
    def get_proposal(self, proposal_id: int) -> Proposal | None: ...
    def meetings_between(self, start: datetime, end: datetime) -> list[Meeting]: ...
    def proposals_between(self, start: datetime, end: datetime) -> list[Proposal]: ...
    def stage_changes_between(self, start: datetime, end: datetime) -> list[StatusChange]: ...
    def interactions_between(self, start: datetime, end: datetime) -> list[Interaction]: ...
    def sources_by_lead(self) -> dict[int, list[str]]: ...
    def flush(self) -> None: ...


class SqlSalesRepository:
    def __init__(self, session: Session, workspace_id: int) -> None:
        self.session = session
        self.workspace_id = workspace_id

    def _leads(self):
        return select(Lead).where(Lead.workspace_id == self.workspace_id)

    def get_lead(self, lead_id: int) -> Lead | None:
        lead = self.session.get(Lead, lead_id)
        return lead if lead is not None and lead.workspace_id == self.workspace_id else None

    def all_leads(self) -> list[Lead]:
        query = self._leads().options(selectinload(Lead.status_changes))
        return list(self.session.scalars(query))

    def leads_in_stages(self, stages: list[str]) -> list[Lead]:
        query = self._leads().where(Lead.status.in_(stages), Lead.do_not_contact.is_(False))
        ranking = func.coalesce(Lead.ai_score, Lead.business_score)
        return list(self.session.scalars(query.order_by(ranking.desc().nulls_last(), Lead.id)))

    def add_meeting(self, lead: Lead, meeting: Meeting) -> Meeting:
        lead.meetings.append(meeting)
        self.session.flush()
        return meeting

    def add_proposal(self, lead: Lead, proposal: Proposal) -> Proposal:
        lead.proposals.append(proposal)
        self.session.flush()
        return proposal

    def get_proposal(self, proposal_id: int) -> Proposal | None:
        proposal = self.session.get(Proposal, proposal_id)
        if proposal is None or proposal.lead.workspace_id != self.workspace_id:
            return None
        return proposal

    def _child_between(self, model, column, start: datetime, end: datetime):
        query = (
            select(model)
            .join(Lead, Lead.id == model.lead_id)
            .where(Lead.workspace_id == self.workspace_id, column >= start, column < end)
            .order_by(column)
        )
        return list(self.session.scalars(query))

    def meetings_between(self, start: datetime, end: datetime) -> list[Meeting]:
        return self._child_between(Meeting, Meeting.scheduled_at, start, end)

    def proposals_between(self, start: datetime, end: datetime) -> list[Proposal]:
        return self._child_between(Proposal, Proposal.created_at, start, end)

    def stage_changes_between(self, start: datetime, end: datetime) -> list[StatusChange]:
        return self._child_between(StatusChange, StatusChange.changed_at, start, end)

    def interactions_between(self, start: datetime, end: datetime) -> list[Interaction]:
        return self._child_between(Interaction, Interaction.created_at, start, end)

    def sources_by_lead(self) -> dict[int, list[str]]:
        rows = self.session.execute(
            select(LeadSource.lead_id, LeadSource.source)
            .join(Lead, Lead.id == LeadSource.lead_id)
            .where(Lead.workspace_id == self.workspace_id)
        )
        result: dict[int, list[str]] = {}
        for lead_id, source in rows:
            result.setdefault(lead_id, []).append(source)
        return result

    def flush(self) -> None:
        self.session.flush()


ACTIVE_STAGES = [
    LeadStatus.APPROVED,
    LeadStatus.CONTACTED,
    LeadStatus.REPLIED,
    LeadStatus.MEETING,
    LeadStatus.PROPOSAL,
]

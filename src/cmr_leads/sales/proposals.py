"""Propuestas en ARS armadas desde config/precios.yaml (nunca con precios inventados)."""

from sqlalchemy.orm import Session

from cmr_leads.db.models import Lead, Proposal, utcnow
from cmr_leads.domain.enums import LeadStatus, ProposalStatus
from cmr_leads.sales.config import PriceList
from cmr_leads.sales.pipeline import stage_index
from cmr_leads.services import set_status


class ProposalError(Exception):
    pass


def _amount(value: str | int | None) -> int:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return int(digits) if digits else 0


def create_proposal(
    session: Session,
    lead: Lead,
    prices: PriceList,
    plan_key: str,
    variant: str | None = None,
    one_time: str | int | None = None,
    notes: str | None = None,
) -> Proposal:
    plan = prices.planes.get(plan_key)
    if plan is None:
        raise ProposalError(f"El plan '{plan_key}' no está en config/precios.yaml")
    monthly, title = plan.monthly, plan.nombre
    if variant:
        chosen = next((v for v in plan.variantes if v.nombre == variant), None)
        if chosen is None:
            raise ProposalError(f"La variante '{variant}' no existe en {plan.nombre}")
        monthly, title = chosen.monthly, f"{plan.nombre} — {chosen.nombre}"
    one_time_ars = _amount(one_time) or plan.one_time
    if not monthly and not one_time_ars:
        raise ProposalError("Ese plan no tiene precio cargado en config/precios.yaml")
    proposal = Proposal(
        plan=plan_key,
        items=[{"titulo": title, "incluye": plan.incluye}],
        monthly_ars=monthly,
        one_time_ars=one_time_ars,
        notes=(notes or "").strip() or None,
        status=ProposalStatus.DRAFT,
    )
    lead.proposals.append(proposal)
    lead.deal_amount_ars = monthly + one_time_ars
    if stage_index(lead.status) < stage_index(LeadStatus.PROPOSAL):
        set_status(session, lead, LeadStatus.PROPOSAL, f"propuesta: {title}")
    session.flush()
    return proposal


def set_proposal_status(session: Session, proposal: Proposal, status: str) -> None:
    if status not in {s.value for s in ProposalStatus}:
        raise ProposalError(f"Estado de propuesta inválido: {status}")
    proposal.status = status
    if status == ProposalStatus.SENT and proposal.sent_at is None:
        proposal.sent_at = utcnow()
    if status == ProposalStatus.ACCEPTED:
        lead = proposal.lead
        lead.deal_amount_ars = proposal.monthly_ars + proposal.one_time_ars
        set_status(session, lead, LeadStatus.WON, "propuesta aceptada")
        lead.next_follow_up_at = None
        lead.next_step = None
    session.flush()

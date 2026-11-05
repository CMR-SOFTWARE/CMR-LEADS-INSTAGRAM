from datetime import timedelta

import pytest

from cmr_leads.db.models import Lead, utcnow
from cmr_leads.domain.enums import Channel, LeadStatus
from cmr_leads.sales.config import SalesConfig, load_prices
from cmr_leads.sales.pipeline import (
    change_stage,
    follow_up_alert,
    register_contact,
    register_meeting,
    stuck_alert,
)
from cmr_leads.sales.proposals import ProposalError, create_proposal, set_proposal_status
from cmr_leads.services import LeadError

from .conftest import ROOT


def _lead(session, workspace, **kw):
    lead = Lead(workspace_id=workspace.id, display_name="Peluquería Sofi", **kw)
    session.add(lead)
    session.flush()
    return lead


def test_lost_requires_reason(session, workspace):
    lead = _lead(session, workspace)
    with pytest.raises(LeadError, match="motivo"):
        change_stage(session, lead, LeadStatus.LOST)
    change_stage(session, lead, LeadStatus.LOST, lost_reason="precio")
    assert lead.status == LeadStatus.LOST and lead.lost_reason == "precio"
    assert lead.stage_entered_at is not None


def test_contact_schedules_day_3_then_7_then_close(session, workspace):
    lead = _lead(session, workspace, status=LeadStatus.APPROVED)
    config = SalesConfig()
    register_contact(session, lead, Channel.WHATSAPP, config, "hola")
    first = lead.contacted_at
    assert lead.status == LeadStatus.CONTACTED
    assert lead.next_follow_up_at.date() == (first + timedelta(days=3)).date()
    register_contact(session, lead, Channel.WHATSAPP, config, "seguimiento 1")
    assert "Seguimiento 2" in lead.next_step
    register_contact(session, lead, Channel.WHATSAPP, config, "seguimiento 2")
    assert lead.next_step.startswith("Cerrar")
    alert = follow_up_alert(lead, utcnow() + timedelta(days=20))
    assert alert and alert.kind == "cerrar"


def test_stuck_and_meeting(session, workspace):
    lead = _lead(session, workspace, status=LeadStatus.REPLIED)
    lead.stage_entered_at = utcnow() - timedelta(days=10)
    alert = stuck_alert(lead, utcnow(), SalesConfig())
    assert alert and alert.kind == "trabado"
    register_meeting(session, lead, utcnow() + timedelta(days=1), "Charla", "Mandar propuesta")
    assert lead.status == LeadStatus.MEETING and lead.next_step == "Mandar propuesta"
    assert stuck_alert(lead, utcnow(), SalesConfig()) is None


def test_proposal_uses_price_config_and_closes_won(session, workspace):
    prices = load_prices(ROOT / "config" / "precios.yaml")
    lead = _lead(session, workspace, status=LeadStatus.MEETING)
    with pytest.raises(ProposalError):
        create_proposal(session, lead, prices, "plan_inventado")
    proposal = create_proposal(session, lead, prices, "cmr_nexo", "Profesional (hasta 3)")
    assert proposal.monthly_ars == 17500
    assert lead.status == LeadStatus.PROPOSAL and lead.deal_amount_ars == 17500
    set_proposal_status(session, proposal, "aceptada")
    assert lead.status == LeadStatus.WON

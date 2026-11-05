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


def test_export_and_import_drafts(session, workspace, tmp_path):
    import csv

    from cmr_leads.sales.drafts import export_pending, import_drafts

    lead = _lead(session, workspace, status=LeadStatus.APPROVED, recommended_service="pagina_web")
    _lead(session, workspace)  # nuevo: no se exporta
    out = tmp_path / "p.csv"
    assert export_pending(session, workspace.id, out) == 1
    with out.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["plan"] and rows[0]["problema"] == "sin_web"
    rows[0]["borrador"] = "Hola, vi su negocio"
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    assert import_drafts(session, workspace.id, out) == (1, 0)
    assert lead.raw_data["ai_message"] == "Hola, vi su negocio"


def test_metrics_funnel_closes_and_counts_real_data(session, workspace):
    from cmr_leads.sales.metrics import compute_metrics
    from cmr_leads.sales.repository import SqlSalesRepository

    repo = SqlSalesRepository(session, workspace.id)
    empty = compute_metrics(repo, None, utcnow() + timedelta(days=1))
    assert empty.empty and empty.reply_rate is None  # sin datos: vacío, nunca de ejemplo

    config = SalesConfig()
    won = _lead(session, workspace, status=LeadStatus.APPROVED)
    register_contact(session, won, Channel.WHATSAPP, config)
    change_stage(session, won, LeadStatus.REPLIED)
    change_stage(session, won, LeadStatus.WON)
    won.deal_amount_ars = 80000
    lost = _lead(session, workspace, status=LeadStatus.APPROVED)
    register_contact(session, lost, Channel.WHATSAPP, config)
    change_stage(session, lost, LeadStatus.LOST, lost_reason="sin_respuesta")
    _lead(session, workspace)  # nuevo sin trabajar
    session.flush()

    m = compute_metrics(repo, utcnow() - timedelta(days=1), utcnow() + timedelta(days=1))
    funnel = {step.stage: step.count for step in m.funnel}
    assert funnel["new"] == 3 and funnel["contacted"] == 2 and funnel["replied"] == 1
    assert funnel["won"] == 1
    counts = [step.count for step in m.funnel]
    assert counts == sorted(counts, reverse=True)  # el embudo siempre baja
    assert m.contacted == 2 and m.reply_rate == 0.5 and m.won_ars == 80000
    assert m.lost_reasons == [("Sin respuesta", 1)]


def test_briefing_lists_people_to_contact_and_overdue(session, workspace):
    from cmr_leads.sales.briefing import build_briefing
    from cmr_leads.sales.repository import SqlSalesRepository

    ready = _lead(session, workspace, status=LeadStatus.APPROVED, whatsapp_e164="+5493364000000")
    late = _lead(session, workspace, status=LeadStatus.APPROVED, whatsapp_e164="+5493364000001")
    register_contact(session, late, Channel.WHATSAPP, SalesConfig())
    late.next_follow_up_at = utcnow() - timedelta(days=2)
    session.flush()
    b = build_briefing(SqlSalesRepository(session, workspace.id), SalesConfig(), utcnow())
    assert [lead.id for lead in b.to_contact] == [ready.id]
    assert [a.lead.id for a in b.follow_ups] == [late.id]

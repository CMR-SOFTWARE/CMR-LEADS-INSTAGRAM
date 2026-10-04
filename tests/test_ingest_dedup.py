from cmr_leads.db.models import Lead, LeadSource
from cmr_leads.domain.enums import Channel, SourceType
from cmr_leads.domain.schemas import RawProfile, SourceRecord
from cmr_leads.pipeline.dedup import find_possible_duplicates
from cmr_leads.pipeline.ingest import ingest
from cmr_leads.services import mark_contacted


def _record(source=SourceType.MANUAL, ref="", **profile):
    return SourceRecord(profile=RawProfile(**profile), source=source, source_ref=ref)


def test_same_username_from_two_sources_is_one_lead(session, workspace):
    first = ingest(
        session,
        workspace,
        _record(SourceType.CMR_FOLLOWERS, "cmr", instagram_username="@Pepe.Pizza"),
        "AR",
    )
    second = ingest(
        session,
        workspace,
        _record(SourceType.CMR_FOLLOWING, "cmr", instagram_username="pepe.pizza"),
        "AR",
    )
    assert first.created and not second.created
    assert first.lead.id == second.lead.id
    assert {s.source for s in first.lead.sources} == {"cmr_followers", "cmr_following"}
    assert session.query(Lead).count() == 1


def test_same_phone_with_and_without_nine_is_duplicate(session, workspace):
    a = ingest(session, workspace, _record(display_name="Taller", phone="0336 15 4123456"), "AR")
    b = ingest(
        session,
        workspace,
        _record(source=SourceType.CSV_IMPORT, ref="x.csv", whatsapp="+543364123456"),
        "AR",
    )
    assert a.lead.id == b.lead.id
    assert a.lead.whatsapp_e164 == "+5493364123456"


def test_same_domain_is_duplicate_but_aggregators_are_not(session, workspace):
    a = ingest(
        session, workspace, _record(display_name="A", website="https://www.negocio.com.ar"), "AR"
    )
    b = ingest(
        session, workspace, _record(display_name="B", website="negocio.com.ar/contacto"), "AR"
    )
    c = ingest(session, workspace, _record(display_name="C", website="https://linktr.ee/c"), "AR")
    d = ingest(session, workspace, _record(display_name="D", website="https://linktr.ee/d"), "AR")
    assert a.lead.id == b.lead.id
    assert c.lead.id != d.lead.id
    assert c.lead.has_link_in_bio_only


def test_new_data_links_two_existing_leads_and_merges_them(session, workspace):
    ig = ingest(
        session,
        workspace,
        _record(SourceType.CMR_FOLLOWERS, "cmr", instagram_username="gym.fuerza"),
        "AR",
    )
    csv = ingest(
        session,
        workspace,
        _record(SourceType.CSV_IMPORT, "f.csv", display_name="Gym Fuerza", phone="3364123456"),
        "AR",
    )
    mark_contacted(session, csv.lead, Channel.PHONE, "llamado")
    assert ig.lead.id != csv.lead.id
    # Llega info que une ambos: mismo username y mismo teléfono.
    merged = ingest(
        session, workspace, _record(instagram_username="gym.fuerza", phone="+5493364123456"), "AR"
    )
    assert session.query(Lead).count() == 1
    lead = merged.lead
    assert lead.id == ig.lead.id
    assert lead.display_name == "Gym Fuerza"
    assert {s.source for s in lead.sources} == {"cmr_followers", "csv_import", "manual"}
    assert len(lead.interactions) == 1
    assert session.query(LeadSource).count() == 3


def test_contacts_extracted_from_bio_on_ingest(session, workspace):
    result = ingest(
        session,
        workspace,
        _record(instagram_username="dulce", bio="Pedidos wa.me/5493364123456"),
        "AR",
    )
    assert result.lead.whatsapp_e164 == "+5493364123456"
    assert result.lead.whatsapp_confirmed


def test_fuzzy_possible_duplicates(session, workspace):
    ingest(
        session, workspace, _record(display_name="Panadería La Espiga", city="San Nicolás"), "AR"
    )
    ingest(
        session, workspace, _record(display_name="Panaderia la espiga", city="San Nicolas"), "AR"
    )
    ingest(session, workspace, _record(display_name="Ferretería Central", city="San Nicolás"), "AR")
    pairs = find_possible_duplicates(session, workspace.id)
    assert len(pairs) == 1


def test_phone_equal_to_whatsapp_is_not_duplicated(session, workspace):
    result = ingest(
        session,
        workspace,
        _record(display_name="Lu", phone="3364-555666", bio="WhatsApp 3364-555666"),
        "AR",
    )
    assert result.lead.whatsapp_e164 == "+5493364555666"
    assert result.lead.phone_e164 is None

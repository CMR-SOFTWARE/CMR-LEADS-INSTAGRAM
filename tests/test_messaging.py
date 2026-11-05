from pathlib import Path
from urllib.parse import unquote

from cmr_leads.db.models import Lead
from cmr_leads.domain.enums import Channel
from cmr_leads.messaging.generator import generate_message
from cmr_leads.settings import Settings


def _settings():
    return Settings(
        _env_file=None,
        sender_name="Tomás",
        company_name="CMR Software Solutions",
        company_city="San Nicolás",
        templates_path=Path("no-existe.yaml"),  # prueba las plantillas .j2 de respaldo
    )


def test_whatsapp_draft_for_appointments():
    lead = Lead(
        display_name="Estética Sofi",
        business_type="estetica",
        recommended_service="sistema_turnos",
        whatsapp_e164="+5493364123456",
        is_local=True,
        has_link_in_bio_only=False,
    )
    draft = generate_message(lead, _settings())
    assert draft.channel == Channel.WHATSAPP
    assert "Estética Sofi" in draft.text
    assert "turnos" in draft.text
    assert "acá de San Nicolás" in draft.text
    assert "no les vuelvo a escribir" in draft.text
    assert draft.link.startswith("https://wa.me/5493364123456?text=")
    assert unquote(draft.link.split("text=")[1]) == draft.text


def test_instagram_dm_fallback_and_generic_template():
    lead = Lead(
        instagram_username="ferre.centro",
        recommended_service="sistema_a_medida",
        has_link_in_bio_only=False,
    )
    draft = generate_message(lead, _settings())
    assert draft.channel == Channel.INSTAGRAM_DM
    assert draft.link == "https://ig.me/m/ferre.centro"
    assert "@ferre.centro" in draft.text
    assert "acá de" not in draft.text


def test_link_in_bio_template_mentions_it():
    lead = Lead(
        display_name="Ropa Mía",
        recommended_service="pagina_web",
        has_link_in_bio_only=True,
        website="https://linktr.ee/ropamia",
    )
    draft = generate_message(lead, _settings())
    assert "link de la bio" in draft.text


def test_yaml_templates_by_plan_rubro_and_problem():
    from cmr_leads.messaging.generator import detect_problem

    settings = Settings(_env_file=None, company_name="CMR", company_city="San Nicolás")
    gym = Lead(
        display_name="Gym Fuerza",
        business_type="deportes",
        recommended_service="sistema_turnos",
        instagram_username="gym.fuerza",
        bio="Reservá tu turno por DM",
        whatsapp_e164="+5493364123456",
    )
    assert detect_problem(gym) == "turnos_por_mensaje"
    draft = generate_message(gym, settings)
    assert draft.template == "sistema_turnos/deportes"
    assert "los turnos los toman por mensaje" in draft.text
    assert "reserva de clases" in draft.text
    assert "{" not in draft.text
    optica = Lead(display_name="Óptica Centro", recommended_service="pagina_web")
    text = generate_message(optica, settings).text
    assert "todavía no tienen página web" in text

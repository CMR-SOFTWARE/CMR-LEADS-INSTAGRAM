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


def _yaml_settings():
    return Settings(_env_file=None, company_name="CMR", company_city="San Nicolás")


def test_detect_problem_still_reads_the_bio():
    from cmr_leads.messaging.generator import detect_problem

    gym = Lead(display_name="Gym Fuerza", instagram_username="gym.fuerza", bio="Turnos por DM")
    assert detect_problem(gym) == "turnos_por_mensaje"


def test_templates_follow_the_rules_in_every_version():
    import yaml

    from cmr_leads.messaging.generator import MAX_WORDS, _fill

    config = yaml.safe_load(Path("config/plantillas.yaml").read_text(encoding="utf-8"))
    bases = {"estetica", "gimnasio", "salud", "gastronomia", "canchas", "comercio_sin_web"}
    assert bases <= set(config["plantillas"])
    texts = [t for options in config["plantillas"].values() for t in options.values()]
    texts += list(config["seguimientos"].values())
    for name in ("Estética Sofi", "Lic. Ana Pérez"):
        lead = Lead(display_name=name)
        for template in texts:
            text = _fill(template, lead, config["firma"])
            assert len(text.split()) <= MAX_WORDS, text
            assert "¿" not in text and "¡" not in text and "{" not in text
            assert "promo" not in text.lower() and "video" not in text.lower()
    for template in config["plantillas"]["gastronomia"].values():
        assert "turno" not in template


def test_template_by_rubro_with_signature_and_ab_versions():
    settings = _yaml_settings()
    first = Lead(id=1, display_name="Estética Sofi", business_type="estetica")
    second = Lead(id=2, display_name="Estética Sofi", business_type="estetica")
    a, b = generate_message(first, settings), generate_message(second, settings)
    assert {a.template, b.template} == {"estetica/a", "estetica/b"}
    assert a.text != b.text
    assert generate_message(first, settings).template == a.template  # siempre la misma
    for draft in (a, b):
        assert "Soy Tomi de CMR, de acá de San Nicolás" in draft.text
        assert "Canito Skin" in draft.text
        assert draft.text.endswith("?")
    resto = Lead(id=3, display_name="La Parrilla", business_type="gastronomia")
    assert "turno" not in generate_message(resto, settings).text.lower()
    canchas = Lead(id=4, display_name="Pádel Norte", recommended_service="cmr_canchas")
    assert generate_message(canchas, settings).template.startswith("canchas/")
    ferre = Lead(id=5, display_name="Ferretería Sol", business_type="comercio")
    assert generate_message(ferre, settings).template.startswith("comercio_sin_web/")


def test_professionals_are_spoken_to_in_singular():
    from cmr_leads.messaging.ai_writer import build_lead_facts

    settings = _yaml_settings()
    lic = Lead(id=2, display_name="Lic. Ana Pérez - Psicóloga", business_type="salud")
    text = generate_message(lic, settings).text
    assert "Te " in text and "Les " not in text and "andás" in text
    assert "hablale de vos" in build_lead_facts(lic)
    consultorio = Lead(id=2, display_name="Consultorio Norte", business_type="salud")
    assert "Les " in generate_message(consultorio, settings).text
    assert "hablale de vos" not in build_lead_facts(consultorio)


def test_style_rules_and_template_labels():
    from cmr_leads.messaging.generator import apply_style, follow_up_message, template_label

    assert apply_style("¡Hola! ¿Les muestro ?") == "Hola! Les muestro?"
    assert template_label("estetica/b") == "Estética · versión B"
    assert template_label("ia") == "Redactado con IA"
    assert template_label("seguimiento/1") == "Seguimiento 1"
    assert template_label("cmr_nexo") == "cmr_nexo (anterior)"  # envíos viejos, antes del A/B
    text = follow_up_message(Lead(display_name="Gym Fuerza"), _yaml_settings(), 1)
    assert "Tomi de CMR" in text and "su negocio" in text and "¿" not in text

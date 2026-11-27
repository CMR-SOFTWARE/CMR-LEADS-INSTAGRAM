from cmr_leads.domain.enums import EnrichmentState, Priority
from cmr_leads.pipeline.scoring import ScoringInput, score


def _input(**kwargs):
    defaults = {"enrichment_state": EnrichmentState.DONE, "is_business_account": True}
    return ScoringInput(**{**defaults, **kwargs})


def test_local_business_without_website_is_interesting(scoring_config):
    result = score(
        _input(
            username="estetica.sofi",
            display_name="Estética Sofi",
            bio="Centro de estética en San Nicolás. Turnos por WhatsApp",
            whatsapp_e164="+5493364123456",
            whatsapp_confirmed=True,
        ),
        scoring_config,
    )
    assert not result.excluded
    assert result.business_type == "estetica"
    assert result.is_local
    assert result.priority == Priority.HIGH
    assert result.recommended_service == "cmr_nexo"
    assert result.total == result.fit + result.need + result.reach
    assert any("sin sitio web" in item["reason"] for item in result.breakdown)


def test_dimensions_are_capped(scoring_config):
    result = score(
        _input(
            username="x",
            display_name="Local",
            bio="tienda negocio servicios san nicolas pedidos turnos ropa",
            category="Tienda de ropa",
            whatsapp_e164="+5493364123456",
            whatsapp_confirmed=True,
            email="a@b.com",
        ),
        scoring_config,
    )
    assert result.fit <= 40 and result.need <= 40 and result.reach <= 20
    assert result.total <= 100


def test_competitor_is_excluded(scoring_config):
    result = score(_input(bio="Agencia digital: diseño web y marketing digital"), scoring_config)
    assert result.excluded
    assert "competidor" in result.exclusion_reason


def test_creator_category_is_excluded(scoring_config):
    result = score(_input(category="Creador digital", bio="Viajes y lifestyle"), scoring_config)
    assert result.excluded


def test_not_found_on_business_discovery_is_excluded(scoring_config):
    result = score(
        _input(enrichment_state=EnrichmentState.NOT_FOUND, is_business_account=False),
        scoring_config,
    )
    assert result.excluded
    assert "no es cuenta comercial" in result.exclusion_reason


def test_manual_username_exclusion(scoring_config):
    scoring_config.exclusions.usernames.append("@cliente.actual")
    try:
        result = score(_input(username="cliente.actual"), scoring_config)
    finally:
        scoring_config.exclusions.usernames.remove("@cliente.actual")
    assert result.excluded


def test_link_in_bio_only_counts_as_need(scoring_config):
    result = score(_input(display_name="Estudio Mía", link_in_bio_only=True), scoring_config)
    assert any("link en bio" in item["reason"] for item in result.breakdown)
    assert result.recommended_service == "pagina_web"


def test_bad_website_suggests_improvement(scoring_config):
    result = score(
        _input(
            display_name="Inmobiliaria Centro",
            has_website=True,
            website_signals={"reachable": True, "https": False, "mobile_viewport": False},
        ),
        scoring_config,
    )
    assert result.recommended_service == "mejora_web"
    assert result.need == 10


def test_area_code_marks_local(scoring_config):
    result = score(_input(display_name="Taller Ruiz", phone_e164="+543364421234"), scoring_config)
    assert result.is_local


def test_keyword_matching_uses_word_boundaries(scoring_config):
    # "barbara" no debe matchear "bar"; "cafeteria" sí.
    result = score(_input(display_name="Barbara López"), scoring_config)
    assert result.business_type is None


def test_recommends_own_products_by_rubro(scoring_config):
    canchas = score(_input(display_name="Pádel Norte", category="Club de pádel"), scoring_config)
    nexo = score(_input(display_name="Peluquería Sofi", category="Peluquería"), scoring_config)
    gym = score(_input(display_name="Gimnasio Ironman", category="Gimnasio"), scoring_config)
    assert canchas.recommended_service == "cmr_canchas"
    assert nexo.recommended_service == "cmr_nexo"
    assert gym.recommended_service == "cmr_nexo"


def test_shops_get_web_plans_not_nexo(scoring_config):
    optica = score(_input(display_name="Óptica Centro", category="Óptica"), scoring_config)
    ropa = score(_input(display_name="Tienda Lola", category="Tienda de ropa"), scoring_config)
    assert optica.recommended_service == "pagina_web"
    assert ropa.recommended_service == "tienda_online"


def test_ai_offer_overrides_rule_based_plan(scoring_config):
    from cmr_leads.db.models import Lead
    from cmr_leads.pipeline.scoring import apply_score

    lead = Lead(
        display_name="Óptica Centro",
        category="Óptica",
        raw_data={"ai_offer": {"product": "tienda_online"}},
    )
    apply_score(lead, scoring_config)
    assert lead.recommended_service == "tienda_online"


def test_competitor_system_lowers_score_and_is_marked():
    from pathlib import Path

    from cmr_leads.pipeline.scoring import ScoringInput, load_scoring_config, score

    config = load_scoring_config(Path("config/scoring.yaml"))
    base = dict(
        display_name="Glam Estética",
        bio="Estética integral. Sacá tu turno",
        category="Salón de belleza",
        is_business_account=True,
        whatsapp_e164="+5493364000000",
    )
    free = score(ScoringInput(**base), config)
    agendapro = score(
        ScoringInput(**base, has_website=True, website="https://glam.agendapro.com/ar"), config
    )
    fresha = score(
        ScoringInput(**base, website_signals={"links": ["https://www.fresha.com/a/mb-barber"]}),
        config,
    )
    tacto = score(
        ScoringInput(
            **base,
            has_website=True,
            website="https://excelsia.com.ar",
            website_signals={"text": "Sitio desarrollado por Tacto Web"},
        ),
        config,
    )
    assert free.competitor_system is None
    assert (agendapro.competitor_system, fresha.competitor_system, tacto.competitor_system) == (
        "AgendaPro",
        "Fresha",
        "Tacto Web",
    )
    assert fresha.need < free.need and fresha.total < free.total
    assert any("ya tiene sistema" in i["reason"] for i in agendapro.breakdown)

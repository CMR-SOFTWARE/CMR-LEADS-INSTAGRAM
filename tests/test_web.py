import shutil
from urllib.parse import unquote_plus

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import selectinload

from cmr_leads.db.models import Lead
from cmr_leads.db.session import run_migrations
from cmr_leads.settings import Settings
from cmr_leads.web.app import create_app

from .conftest import FIXTURES, ROOT


@pytest.fixture
def client(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    shutil.copy(ROOT / "config" / "scoring.yaml", config_dir / "scoring.yaml")
    url = f"sqlite:///{tmp_path / 'web.db'}"
    run_migrations(url)
    settings = Settings(
        _env_file=None,
        database_url=url,
        scoring_config=config_dir / "scoring.yaml",
        website_fetch_enabled=False,
        backup_dir=tmp_path / "backups",
    )
    app = create_app(settings)
    with TestClient(app, base_url="http://testserver") as test_client:
        test_client.app_state = app.state
        yield test_client


def _lead(client, **filters) -> Lead:
    with client.app_state.session_factory() as session:
        return (
            session.query(Lead).options(selectinload(Lead.interactions)).filter_by(**filters).one()
        )


def _msg(response) -> str:
    return unquote_plus(str(response.url))


def _add(client, **data):
    response = client.post("/leads/nuevo", data=data, follow_redirects=False)
    assert response.status_code == 303
    return int(response.headers["location"].split("/")[2].split("?")[0])


def test_pages_render_empty(client):
    for url in ("/", "/leads", "/revisar", "/algoritmo", "/importar", "/leads/nuevo"):
        response = client.get(url)
        assert response.status_code == 200, url
        assert "CMR" in response.text


def test_add_lead_shows_profile_links(client):
    lead_id = _add(
        client,
        instagram="@estetica.sofi",
        name="Estética Sofi",
        whatsapp="3364-123456",
        city="San Nicolás",
        bio="Turnos de estética",
    )
    page = client.get(f"/leads/{lead_id}").text
    assert "https://www.instagram.com/estetica.sofi/" in page
    assert "https://wa.me/5493364123456" in page
    assert "google.com/search" in page
    listing = client.get("/leads").text
    assert "Estética Sofi" in listing and "https://www.instagram.com/estetica.sofi/" in listing


def test_approve_generates_message_and_contact(client):
    lead_id = _add(client, name="Gym Fuerza", whatsapp="3364-123456", bio="Reservas de clases")
    assert "Aprobá el lead" in client.get(f"/leads/{lead_id}").text
    client.post(f"/leads/{lead_id}/estado", data={"status": "approved"})
    page = client.get(f"/leads/{lead_id}").text
    assert "Abrir en WhatsApp" in page and "Gym Fuerza" in page
    client.post(f"/leads/{lead_id}/contactado", data={"channel": "whatsapp", "message": "Hola!"})
    lead = _lead(client, id=lead_id)
    assert lead.status == "contacted" and lead.interactions[0].message == "Hola!"


def test_review_flow_records_feedback(client):
    a = _add(client, name="Panadería Sol", city="San Nicolás")
    b = _add(client, instagram="cliente.viejo", name="Cliente Viejo")
    assert "Revisión rápida" in client.get("/revisar").text
    client.post(f"/revisar/{a}", data={"action": "aprobar"})
    response = client.post(f"/revisar/{b}", data={"action": "descartar", "reason": "ya_es_cliente"})
    assert "excluido" in _msg(response)
    assert _lead(client, id=a).feedback == 1
    lead_b = _lead(client, id=b)
    assert lead_b.feedback == -1 and lead_b.status == "rejected" and lead_b.excluded
    assert "No hay más leads" in client.get("/revisar").text


def test_skip_in_review(client):
    a = _add(client, name="Uno")
    _add(client, name="Dos")
    response = client.post(f"/revisar/{a}", data={"action": "saltar"})
    assert f"skip={a}" in str(response.url)


def test_algorithm_learns_and_applies_suggestions(client):
    # Leads con WhatsApp: aprobados. Sin WhatsApp: descartados.
    for i in range(6):
        lead_id = _add(client, name=f"Bueno {i}", whatsapp=f"33641234{i:02d}")
        client.post(f"/revisar/{lead_id}", data={"action": "aprobar"})
        lead_id = _add(client, name=f"Malo {i}", email=f"malo{i}@x.com")
        client.post(f"/revisar/{lead_id}", data={"action": "descartar", "reason": "no_necesita"})
    page = client.get("/algoritmo").text
    assert "Sugerencias" in page and "WhatsApp publicado" in page
    client.post("/algoritmo/aplicar")
    with client.app_state.session_factory():
        from cmr_leads.pipeline.scoring import load_scoring_config

        config = load_scoring_config(client.app.state.settings.scoring_config)
    assert config.reach.whatsapp_confirmed == 25
    assert config.reach.email == 3
    client.post("/algoritmo/restaurar")
    config = load_scoring_config(client.app.state.settings.scoring_config)
    assert config.reach.whatsapp_confirmed == 20


def test_manual_weights_rescore(client):
    lead_id = _add(client, name="Sin web", city="San Nicolás")
    before = _lead(client, id=lead_id).business_score
    client.post("/algoritmo/pesos", data={"need.no_website": "30"})
    assert _lead(client, id=lead_id).business_score == before + 10


def test_import_instagram_json_and_csv(client):
    with open(FIXTURES / "followers_1.json", "rb") as fh:
        response = client.post(
            "/importar/instagram",
            data={"account": "cmr"},
            files={"file": ("followers_1.json", fh, "application/json")},
        )
    assert "2 nuevos" in _msg(response)
    csv_body = "nombre;whatsapp;ciudad\nTaller Ruiz;3364-555666;San Nicolás\n".encode()
    response = client.post("/importar/csv", files={"file": ("r.csv", csv_body, "text/csv")})
    assert response.status_code == 200
    assert _lead(client, display_name="Taller Ruiz").whatsapp_e164 == "+5493364555666"


def test_export_xlsx(client):
    _add(client, name="Algo")
    response = client.get("/exportar?format=xlsx")
    assert response.status_code == 200
    assert response.content[:2] == b"PK"


def test_cross_origin_post_is_blocked(client):
    response = client.post("/acciones/puntuar", headers={"origin": "https://malicioso.com"})
    assert response.status_code == 403


def test_open_redirect_is_ignored(client):
    lead_id = _add(client, name="X")
    response = client.post(
        f"/leads/{lead_id}/estado",
        data={"status": "approved", "next": "//evil.com"},
        follow_redirects=False,
    )
    assert response.headers["location"].startswith(f"/leads/{lead_id}")


def test_google_search_from_panel(client, monkeypatch):
    import cmr_leads.sources.google_places as gp

    class FakeClient:
        def __init__(self, api_key):
            assert api_key == "k"

        def search_text(self, query, page_token=None):
            assert query == "gimnasio en San Nicolás de los Arroyos, Buenos Aires"
            return [
                {
                    "id": "g1",
                    "displayName": {"text": "Gym Uno"},
                    "nationalPhoneNumber": "0336 442-1234",
                    "businessStatus": "OPERATIONAL",
                }
            ], None

    assert "Google Maps apagado" in client.get("/importar").text
    response = client.post("/importar/google", data={"quick": "gimnasio"})
    assert "Falta configurar" in _msg(response)

    monkeypatch.setattr(gp, "GooglePlacesClient", FakeClient)
    client.app.state.settings.google_places_api_key = "k"
    response = client.post("/importar/google", data={"quick": "gimnasio", "max_results": "20"})
    assert "1 nuevos" in _msg(response)
    lead = _lead(client, google_place_id="g1")
    assert lead.display_name == "Gym Uno"
    assert "Google Maps" in client.get("/leads?source=google_places").text


def test_google_search_several_rubros_and_areas(client, monkeypatch):
    import cmr_leads.sources.google_places as gp

    queries = []

    class FakeClient:
        def __init__(self, api_key):
            pass

        def search_text(self, query, page_token=None):
            queries.append(query)
            n = len(queries)
            return [{"id": f"p{n}", "displayName": {"text": f"Negocio {n}"}}], None

    monkeypatch.setattr(gp, "GooglePlacesClient", FakeClient)
    client.app.state.settings.google_places_api_key = "k"
    response = client.post(
        "/importar/google",
        data={
            "query": "peluquería, barbería",
            "area": "Ramallo, Buenos Aires\nSan Pedro, Buenos Aires",
        },
    )
    assert queries == [
        "peluquería en Ramallo, Buenos Aires",
        "barbería en Ramallo, Buenos Aires",
        "peluquería en San Pedro, Buenos Aires",
        "barbería en San Pedro, Buenos Aires",
    ]
    assert "4 búsquedas: 4 nuevos" in _msg(response)


def test_reply_flow_from_panel(client):
    lead_id = _add(client, name="Pelu Lu", whatsapp="3364-123456")
    client.post(f"/leads/{lead_id}/estado", data={"status": "approved"})
    client.post(
        f"/leads/{lead_id}/contactado",
        data={"channel": "whatsapp", "message": "Hola", "template": "pagina_web.j2"},
    )
    response = client.post(f"/leads/{lead_id}/respuesta", data={"text": "Hola! cuánto sale?"})
    assert "Pide info" in _msg(response)
    page = client.get(f"/leads/{lead_id}").text
    assert "Pide info / precio" in page and "cuánto sale?" in page
    chat = "18/10/26, 10:02 - Tomás: Hola\n18/10/26, 11:00 - Pelu Lu: Por ahora no\n"
    response = client.post(
        f"/leads/{lead_id}/chat-whatsapp",
        data={"my_name": "Tomás"},
        files={"file": ("Chat de WhatsApp con Pelu Lu.txt", chat.encode(), "text/plain")},
    )
    assert "1 respuesta" in _msg(response)
    stats = client.get("/respuestas").text
    assert "Tasa de respuesta" in stats and "pagina_web" in stats or "Plan Web" in stats
    outbound = [i for i in _lead(client, id=lead_id).interactions if i.direction == "outbound"]
    assert outbound[0].template == "pagina_web"


def test_instagram_page_without_api(client):
    page = client.get("/instagram").text
    assert "Falta conectar la API de Instagram" in page
    response = client.post("/instagram/actualizar")
    assert "Falta configurar" in _msg(response)


def test_map_shows_leads_by_status_and_locates_missing(client, monkeypatch):
    import cmr_leads.sources.google_places as gp

    class FakeClient:
        def __init__(self, api_key):
            pass

        def search_text(self, query, page_token=None):
            return [
                {
                    "id": "m1",
                    "displayName": {"text": "Gym Norte"},
                    "location": {"latitude": -33.33, "longitude": -60.22},
                },
                {"id": "m2", "displayName": {"text": "Gym Sur"}},
            ], None

        def get_location(self, place_id):
            assert place_id == "m2"
            return (-33.34, -60.21)

    monkeypatch.setattr(gp, "GooglePlacesClient", FakeClient)
    client.app.state.settings.google_places_api_key = "k"
    client.post("/importar/google", data={"query": "gimnasio"})
    assert _lead(client, google_place_id="m1").latitude == -33.33

    page = client.get("/mapa").text
    assert "Gym Norte" in page and "Ubicar 1 locales" in page

    response = client.post("/mapa/ubicar")
    assert "1 locales ubicados" in _msg(response)
    assert _lead(client, google_place_id="m2").longitude == -60.21
    assert "Gym Sur" in client.get("/mapa").text


def test_daily_backup_runs_once_per_day(tmp_path):
    from datetime import date

    from cmr_leads.sales.backup import daily_backup

    db_file = tmp_path / "x.db"
    run_migrations(f"sqlite:///{db_file}")
    url = f"sqlite:///{db_file}"
    first = daily_backup(url, tmp_path / "b", keep=2, today=date(2026, 11, 1))
    assert first and first.exists()
    assert daily_backup(url, tmp_path / "b", keep=2, today=date(2026, 11, 1)) is None
    daily_backup(url, tmp_path / "b", keep=2, today=date(2026, 11, 2))
    daily_backup(url, tmp_path / "b", keep=2, today=date(2026, 11, 3))
    assert sorted(p.name for p in (tmp_path / "b").iterdir()) == [
        "cmr_leads_2026-11-02.db",
        "cmr_leads_2026-11-03.db",
    ]


def test_pipeline_flow_from_panel(client):
    client.post("/leads/nuevo", data={"instagram": "pelu.sofi", "name": "Pelu Sofi"})
    lead = _lead(client, instagram_username="pelu.sofi")
    lid = lead.id
    page = client.get(f"/leads/{lid}").text
    assert "Embudo" in page and "Reuniones" in page and "Propuestas" in page

    # WhatsApp registra solo (fetch) -> 204 y seguimiento a 3 días
    r = client.post(
        f"/leads/{lid}/contactado",
        data={"channel": "whatsapp", "message": "hola"},
        headers={"X-Requested-With": "fetch"},
        follow_redirects=False,
    )
    assert r.status_code == 204
    assert _lead(client, id=lid).status == "contacted"

    # Perdido sin motivo no se guarda
    r = client.post(f"/leads/{lid}/embudo", data={"status": "lost"})
    assert "motivo" in _msg(r) and _lead(client, id=lid).status == "contacted"

    client.post(
        f"/leads/{lid}/reunion",
        data={"scheduled_at": "2026-11-10T10:00", "next_steps": "Propuesta"},
    )
    assert _lead(client, id=lid).status == "meeting"

    r = client.post(f"/leads/{lid}/propuesta", data={"plan": "pagina_web", "one_time": "$ 150.000"})
    assert "/propuestas/" in str(r.url)
    assert "Guardar como PDF" in r.text and "80.000" in r.text and "150.000" in r.text
    assert _lead(client, id=lid).status == "proposal"


def test_email_queue_needs_approval_and_respects_limit(client):
    for i in range(3):
        client.post("/leads/nuevo", data={"name": f"Negocio {i}", "email": f"n{i}@mail.com"})
    with client.app_state.session_factory() as session:
        ids = [lead.id for lead in session.query(Lead).all()]
        for lead in session.query(Lead).all():
            lead.status = "approved"
        session.commit()
    assert "Para aprobar" in client.get("/emails").text
    r = client.post("/emails/aprobar", data={"lead_id": [str(i) for i in ids]})
    assert "3 emails aprobados" in _msg(r)
    page = client.get("/emails").text
    assert "mailto:" in page and "Ya lo mandé" in page


def test_sales_dashboard_and_briefing_render(client):
    page = client.get("/ventas").text
    assert "Equipo de ventas" in page and "Todavía no hay actividad" in page
    assert "Tu día" in client.get("/").text
    for periodo in ("7d", "mes", "todo", "cualquiera"):
        assert client.get(f"/ventas?periodo={periodo}").status_code == 200


def test_journey_page_guides_and_undo(client):
    client.post("/leads/nuevo", data={"name": "Pelu Ana", "whatsapp": "+54 9 336 400-0099"})
    page = client.get("/jornada").text
    assert "Mi jornada" in page and "Lo que hizo el piloto hoy" in page
    assert "Pelu Ana" in page and "Calificar" in page
    lid = _lead(client, display_name="Pelu Ana").id
    r = client.post(f"/revisar/{lid}", data={"action": "aprobar", "next": "/jornada?paso=revisar"})
    assert "/jornada" in str(r.url)
    page = client.get("/jornada?paso=enviar").text
    assert "Abrir en WhatsApp" in page and "data-register" in page
    assert client.get(f"/jornada?paso=enviar&skip={lid}").status_code == 200

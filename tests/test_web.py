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

    assert "Falta la API key" in client.get("/importar").text
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

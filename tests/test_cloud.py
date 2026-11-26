"""Nube: login con los usuarios del gestor, tareas para pg_cron y estado en la base."""

import hashlib
import shutil

import pytest

from cmr_leads.web import auth

TOMI = hashlib.sha256(b"clave-tomi").hexdigest()
CHIPI = hashlib.sha256(b"otra").hexdigest()
GESTOR_JS = f"""
const USUARIOS = {{ tomi: {{ nombre: 'Tomi' }} }};
// contraseñas hasheadas (sha-256)
const HASHES = {{
  tomi:  '{TOMI}',
  chipi: '{CHIPI}'
}};
"""


def test_hashes_from_gestor_and_env():
    hashes = auth.parse_hashes(GESTOR_JS)
    assert set(hashes) == {"tomi", "chipi"}
    assert auth.check_password(hashes, " Tomi ", "clave-tomi") == "tomi"
    assert auth.check_password(hashes, "tomi", "mal") is None
    assert auth.check_password(hashes, "nadie", "clave-tomi") is None
    env = f"tomi:{hashlib.sha256(b'x').hexdigest()},roto:123"
    assert list(auth.parse_hashes(env)) == ["tomi"]


def test_signed_cookie():
    cookie = auth.make_cookie("secreto", "tomi", now=1000)
    assert auth.read_cookie("secreto", cookie, now=2000) == "tomi"
    assert auth.read_cookie("otro", cookie, now=2000) is None  # firma de otra clave
    assert auth.read_cookie("secreto", cookie.replace("tomi", "gena"), now=2000) is None
    assert auth.read_cookie("secreto", cookie, now=1000 + auth.MAX_AGE + 1) is None  # vencida
    assert auth.read_cookie("secreto", "basura", now=2000) is None


@pytest.fixture
def cloud(tmp_path):
    from fastapi.testclient import TestClient

    from cmr_leads.db.session import run_migrations
    from cmr_leads.settings import Settings
    from cmr_leads.web.app import create_app

    from .conftest import ROOT

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
        meta_ads_sync_hours=0,
        login_required=True,
        login_hashes=f"tomi:{hashlib.sha256(b'clave-tomi').hexdigest()}",
        session_secret="secreto-de-prueba",
        cron_secret="clave-cron",
        run_background=False,
    )
    with TestClient(create_app(settings), base_url="http://testserver") as client:
        yield client


def test_login_protects_everything(cloud):
    r = cloud.get("/leads?x=1", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login?siguiente=")
    assert cloud.post("/acciones/puntuar", follow_redirects=False).status_code == 401
    assert cloud.get("/static/style.css").status_code == 200  # estilos sin login
    assert cloud.get("/login").status_code == 200

    bad = cloud.post("/login", data={"usuario": "tomi", "clave": "mal"}, follow_redirects=False)
    assert bad.status_code == 401 and "incorrectos" in bad.text
    ok = cloud.post(
        "/login",
        data={"usuario": "tomi", "clave": "clave-tomi", "siguiente": "/leads"},
        follow_redirects=False,
    )
    assert ok.status_code == 303 and ok.headers["location"] == "/leads"
    page = cloud.get("/leads", follow_redirects=False)
    assert page.status_code == 200 and "Salir (Tomi)" in page.text
    evil = cloud.post(
        "/login",
        data={"usuario": "tomi", "clave": "clave-tomi", "siguiente": "//otro-sitio.com"},
        follow_redirects=False,
    )
    assert evil.headers["location"] == "/"  # solo redirecciones internas

    cloud.get("/logout")
    assert cloud.get("/leads", follow_redirects=False).status_code == 303


def test_tasks_need_the_cron_key(cloud):
    assert cloud.post("/tareas/publicar").status_code == 401
    wrong = {"Authorization": "Bearer otra"}
    assert cloud.post("/tareas/publicar", headers=wrong).status_code == 401
    key = {"Authorization": "Bearer clave-cron"}
    assert cloud.post("/tareas/no-existe", headers=key).status_code == 404
    r = cloud.post("/tareas/publicar", headers=key)
    assert r.status_code == 200  # sin Supabase configurado no hace nada, pero responde


def test_state_in_database(session, workspace, tmp_path):
    from cmr_leads.db.state import DbState

    legacy = tmp_path / "viejo.json"
    legacy.write_text('{"last_check": "ayer"}', encoding="utf-8")
    store = DbState(session, workspace.id, "instagram_dms", legacy)
    assert store.load() == {"last_check": "ayer"}  # toma el archivo viejo la primera vez
    store.save({"last_check": "hoy"})
    legacy.unlink()
    assert DbState(session, workspace.id, "instagram_dms").load() == {"last_check": "hoy"}


def test_cloud_paths(monkeypatch):
    from cmr_leads.settings import Settings

    monkeypatch.setenv("VERCEL", "1")
    s = Settings(_env_file=None)
    assert s.needs_login and not s.background_enabled
    assert "cmr_leads" in str(s.instagram_publish_dir) and "data" not in s.backup_dir.parts[:1]


def test_works_under_leads_path(tmp_path):
    """Detrás de cmrsoftwaresolutions.com/leads: enlaces, redirecciones y formularios."""
    from fastapi.testclient import TestClient

    from cmr_leads.db.session import run_migrations
    from cmr_leads.settings import Settings
    from cmr_leads.web.app import create_app

    from .conftest import ROOT

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
        meta_ads_sync_hours=0,
        login_required=True,
        login_hashes=f"tomi:{TOMI}",
        session_secret="s",
        run_background=False,
        base_path="/leads",
    )
    with TestClient(create_app(settings), base_url="http://testserver") as c:
        r = c.get("/leads/instagram", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"].startswith("/leads/login?siguiente=%2Finstagram")
        page = c.get("/leads/login")
        assert 'action="/leads/login"' in page.text
        landing = {"Origin": "https://cmrsoftwaresolutions.com"}
        ok = c.post(
            "/leads/login",
            data={"usuario": "tomi", "clave": "clave-tomi", "siguiente": "/instagram"},
            headers=landing,
            follow_redirects=False,
        )
        assert ok.headers["location"] == "/leads/instagram"
        lead = c.post(
            "/leads/login",
            data={"usuario": "tomi", "clave": "clave-tomi", "siguiente": "/leads/5"},
            headers=landing,
            follow_redirects=False,
        )
        assert lead.headers["location"] == "/leads/leads/5"  # la ficha del lead, con prefijo
        home = c.get("/leads/", follow_redirects=False)
        assert home.status_code == 200
        assert 'href="/leads/leads"' in home.text and 'href="/leads/static/icons.svg' in home.text
        assert 'href="/instagram"' not in home.text  # ningún enlace se escapa de /leads
        assert 'window.CMR_BASE = "/leads"' in home.text
        assert c.get("/leads/static/style.css").status_code == 200
        assert c.get("/", follow_redirects=False).status_code == 200  # directo también anda
        other = {"Origin": "https://otro-sitio.com"}
        assert c.post("/leads/acciones/puntuar", headers=other).status_code == 403

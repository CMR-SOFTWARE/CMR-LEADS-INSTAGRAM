from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from cmr_leads.db.models import InstagramPlanItem, InstagramScheduled
from cmr_leads.instagram.publish import (
    CUT_MESSAGE,
    run_due,
    upload_keys,
    valid_key,
    validate_files,
)
from cmr_leads.settings import Settings

NOW = datetime(2026, 11, 20, 22, 0)


class FakeStorage:
    def __init__(self, existing=None):
        self.deleted, self.existing = [], existing

    def public_url(self, key):
        return f"https://storage.test/{key}"

    def signed_upload(self, key):
        return f"https://storage.test/sign/{key}?token=t"

    def exists(self, key):
        return self.existing is None or key in self.existing

    def delete(self, keys):
        self.deleted += keys


class FakeGraph:
    """Meta de mentira: `statuses` es lo que va respondiendo al preguntar si terminó."""

    def __init__(self, statuses=None, fail_on=None):
        self.posts, self.statuses, self.n, self.fail_on = [], list(statuses or []), 0, fail_on

    def post(self, path, **data):
        if self.fail_on and self.fail_on in path:
            raise RuntimeError("Meta se cayó")
        self.posts.append((path, data))
        self.n += 1
        return {"id": f"id{self.n}"}

    def get(self, path, **params):
        if params.get("fields") == "permalink":
            return {"permalink": f"https://instagram.com/p/{path}"}
        return {"status_code": self.statuses.pop(0) if self.statuses else "FINISHED"}


def test_validate_files():
    assert validate_files("reel", ["a.mp4"]) is None
    assert "un solo video" in validate_files("reel", ["a.jpg"])
    assert validate_files("imagen", ["a.png"]) is None
    assert "entre 2 y 10" in validate_files("carrusel", ["a.jpg"])
    assert validate_files("carrusel", ["a.jpg", "b.mp4"]) is None
    assert "no soportado" in validate_files("imagen", ["a.gif"])
    assert validate_files("historia", []) == "Subí al menos un archivo."


def test_upload_keys_are_random_and_checked():
    keys = upload_keys(["foto.png", "video.MOV"])
    assert keys[0].endswith("/01.jpg") and keys[1].endswith("/02.mov")
    assert keys[0].split("/")[1] == keys[1].split("/")[1]  # misma carpeta
    assert keys[0] != upload_keys(["foto.png"])[0]  # carpeta al azar
    assert all(valid_key(k) for k in keys)
    assert not valid_key("p/corto/01.jpg")
    assert not valid_key("otra/abcdefghijklmnopqrstu/01.jpg")
    assert not valid_key("p/abcdefghijklmnopqrstu/../x.jpg")


def _item(session, workspace, kind, files, publish_at=None):
    item = InstagramScheduled(
        workspace_id=workspace.id,
        kind=kind,
        caption="Hola",
        publish_at=publish_at or NOW - timedelta(minutes=1),
        files=files,
        status="pendiente",
    )
    session.add(item)
    session.commit()
    return item


def _tick(session, workspace, graph, storage, minutes=0):
    return run_due(
        session, workspace.id, NOW + timedelta(minutes=minutes), 30, graph, "IG", storage
    )


def test_carousel_is_published_in_steps(session, workspace):
    item = _item(session, workspace, "carrusel", ["p/x/01.jpg", "p/x/02.mp4"])
    graph, storage = FakeGraph(), FakeStorage()
    assert _tick(session, workspace, graph, storage) == ["Carrusel 1: preparando"]
    children = [d for _p, d in graph.posts if d.get("is_carousel_item")]
    assert children[0]["image_url"].endswith("p/x/01.jpg")
    assert children[1]["media_type"] == "VIDEO"
    assert item.status == "publicando" and item.containers["children"] == ["id1", "id2"]

    _tick(session, workspace, graph, storage, 1)  # partes listas: se arma el carrusel
    carousel = next(d for _p, d in graph.posts if d.get("media_type") == "CAROUSEL")
    assert carousel["children"] == "id1,id2" and carousel["caption"] == "Hola"
    assert item.status == "publicando"

    assert _tick(session, workspace, graph, storage, 2) == ["Carrusel 1: publicada"]
    assert graph.posts[-1] == ("IG/media_publish", {"creation_id": "id3"})
    assert (item.status, item.media_id) == ("publicada", "id4")
    assert item.permalink.endswith("id4") and item.containers is None
    assert storage.deleted == ["p/x/01.jpg", "p/x/02.mp4"]  # no quedan archivos públicos


def test_reel_waits_while_meta_processes(session, workspace):
    item = _item(session, workspace, "reel", ["p/x/01.mp4"])
    graph = FakeGraph(["IN_PROGRESS", "IN_PROGRESS", "FINISHED"])
    _tick(session, workspace, graph, FakeStorage())
    assert graph.posts[0][1]["media_type"] == "REELS"
    _tick(session, workspace, graph, FakeStorage(), 1)
    _tick(session, workspace, graph, FakeStorage(), 2)
    assert item.status == "publicando"  # Meta sigue procesando: se espera sin bloquear
    _tick(session, workspace, graph, FakeStorage(), 3)
    assert item.status == "publicada"


def test_meta_error_and_timeout(session, workspace):
    broken = _item(session, workspace, "imagen", ["p/x/01.jpg"])
    slow = _item(session, workspace, "reel", ["p/y/01.mp4"])
    graph = FakeGraph(["ERROR", "IN_PROGRESS"])
    _tick(session, workspace, graph, FakeStorage())
    _tick(session, workspace, graph, FakeStorage(), 1)
    assert broken.status == "error" and "no pudo procesar" in broken.error
    graph.statuses = ["IN_PROGRESS"] * 5
    _tick(session, workspace, graph, FakeStorage(), 25)
    assert slow.status == "error" and "tardó demasiado" in slow.error


def test_cut_while_publishing_is_not_retried(session, workspace):
    item = _item(session, workspace, "imagen", ["p/x/01.jpg"])
    graph = FakeGraph(fail_on="media_publish")
    _tick(session, workspace, graph, FakeStorage())
    _tick(session, workspace, graph, FakeStorage(), 1)  # se cae justo al publicar
    assert item.status == "error"
    # Si quedó marcado "publicando" a mitad de publicar, no se reintenta solo.
    item.status, item.containers = "publicando", {"started": NOW.isoformat(), "publishing": True}
    session.commit()
    _tick(session, workspace, FakeGraph(), FakeStorage(), 2)
    assert item.status == "error" and item.error == CUT_MESSAGE


def test_late_and_future_items(session, workspace):
    late = _item(session, workspace, "imagen", ["p/x/01.jpg"], NOW - timedelta(hours=5))
    future = _item(session, workspace, "imagen", ["p/x/01.jpg"], NOW + timedelta(hours=1))
    _tick(session, workspace, FakeGraph(), FakeStorage())
    assert late.status == "vencida"  # no publica sola si se pasó mucho la hora
    assert future.status == "pendiente"


@pytest.fixture
def sched_web(tmp_path, monkeypatch):
    import shutil

    from fastapi.testclient import TestClient

    import cmr_leads.instagram.publish as publish
    from cmr_leads.db.session import run_migrations
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
        supabase_url="https://storage.test",
        supabase_service_key="clave",
    )
    storage = FakeStorage()
    monkeypatch.setattr(publish, "SupabaseStorage", lambda *a, **k: storage)
    app = create_app(settings)
    with TestClient(app, base_url="http://testserver") as client:
        client.tmp, client.storage = tmp_path, storage
        yield client


def test_schedule_flow(sched_web):
    from cmr_leads.instagram.stats import local_now

    day = local_now().date() + timedelta(days=3)
    page = sched_web.get("/instagram/programar?formato=imagen")
    assert page.status_code == 200 and "programar.js" in page.text

    links = sched_web.post("/instagram/subidas", json={"formato": "imagen", "nombres": ["f.jpg"]})
    assert links.status_code == 200
    key = links.json()["archivos"][0]["key"]
    assert valid_key(key) and "sign" in links.json()["archivos"][0]["url"]
    bad = sched_web.post("/instagram/subidas", json={"formato": "reel", "nombres": ["f.jpg"]})
    assert bad.status_code == 400 and "video" in bad.json()["error"]

    import json

    r = sched_web.post(
        "/instagram/programar",
        data={
            "formato": "imagen",
            "dia": day.isoformat(),
            "hora": "19:30",
            "texto": "Hola",
            "archivos": json.dumps([key]),
        },
        follow_redirects=False,
    )
    assert r.status_code == 303 and "Programado" in r.headers["location"]
    hacked = sched_web.post(
        "/instagram/programar",
        data={"formato": "imagen", "dia": day.isoformat(), "hora": "19:30",
              "archivos": json.dumps(["../../otro/archivo.jpg"])},
        follow_redirects=False,
    )  # fmt: skip
    assert "Programado" not in hacked.headers["location"]

    from cmr_leads.db.session import make_engine, make_session_factory

    engine = make_engine(f"sqlite:///{sched_web.tmp / 'web.db'}")
    with make_session_factory(engine)() as s:
        item = s.scalars(select(InstagramScheduled)).one()
        plan = s.get(InstagramPlanItem, item.plan_item_id)
        assert item.publish_at == datetime(day.year, day.month, day.day, 22, 30)  # 19:30 AR
        assert (plan.kind, plan.hour, plan.day.date()) == ("imagen", 19, day)
        assert item.files == [key]
        item_id = item.id

    cal = sched_web.get(f"/instagram/calendario?mes={day:%Y-%m}")
    assert "⏰ Imagen · 19:30" in cal.text

    past = sched_web.post(
        "/instagram/programar",
        data={"formato": "imagen", "dia": "2020-01-01", "hora": "10:00", "archivos": "[]"},
        follow_redirects=False,
    )
    assert "pas%C3%B3" in past.headers["location"] or "pasó" in past.headers["location"]

    r = sched_web.post(f"/instagram/programadas/{item_id}/cancelar", follow_redirects=False)
    assert r.status_code == 303
    with make_session_factory(engine)() as s:
        item = s.get(InstagramScheduled, item_id)
        assert item.status == "cancelada" and item.plan_item_id is None
        assert s.scalars(select(InstagramPlanItem)).first() is None
    assert sched_web.storage.deleted == [key]  # se borró de Supabase

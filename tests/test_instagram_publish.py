import io
from datetime import datetime, timedelta

import pytest
from PIL import Image
from sqlalchemy import select

from cmr_leads.db.models import InstagramPlanItem, InstagramScheduled
from cmr_leads.instagram.publish import (
    PublishError,
    publish_item,
    run_due,
    store_files,
    validate_files,
)
from cmr_leads.settings import Settings


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (2000, 1000), (0, 180, 180, 255)).save(buf, "PNG")
    return buf.getvalue()


class FakeStorage:
    def __init__(self):
        self.uploaded, self.deleted = [], []

    def upload(self, path, key):
        assert path.exists()
        self.uploaded.append(key)
        return f"https://storage.test/{key}"

    def delete(self, keys):
        self.deleted += keys


class FakeGraph:
    def __init__(self, statuses=None):
        self.posts, self.statuses, self.n = [], list(statuses or []), 0

    def post(self, path, **data):
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


def test_store_files_converts_images_to_jpg(tmp_path):
    names = store_files(tmp_path, [("foto.png", _png()), ("video.MP4", b"fake")])
    assert names == ["01.jpg", "02.mp4"]
    with Image.open(tmp_path / "01.jpg") as img:
        assert img.format == "JPEG" and img.width == 1440


def _item(session, workspace, kind, files, publish_at=None):
    item = InstagramScheduled(
        workspace_id=workspace.id,
        kind=kind,
        caption="Hola",
        publish_at=publish_at or datetime(2026, 11, 20, 22),
        files=files,
        status="pendiente",
    )
    session.add(item)
    session.flush()
    return item


def test_publish_carousel(session, workspace, tmp_path):
    item = _item(session, workspace, "carrusel", ["01.jpg", "02.mp4"])
    for name in item.files:
        (tmp_path / name).write_bytes(b"x")
    graph, storage = FakeGraph(), FakeStorage()
    media_id, link = publish_item(item, tmp_path, graph, "IG", storage, sleep=lambda s: None)
    children = [d for _p, d in graph.posts if d.get("is_carousel_item")]
    assert children[0]["image_url"].endswith("01.jpg")
    assert children[1]["media_type"] == "VIDEO"
    carousel = next(d for _p, d in graph.posts if d.get("media_type") == "CAROUSEL")
    assert carousel["children"] == "id1,id2" and carousel["caption"] == "Hola"
    assert graph.posts[-1] == ("IG/media_publish", {"creation_id": "id3"})
    assert media_id == "id4" and link.endswith("id4")
    assert sorted(storage.deleted) == sorted(storage.uploaded)  # no quedan archivos públicos


def test_publish_reel_waits_until_video_is_ready(session, workspace, tmp_path):
    item = _item(session, workspace, "reel", ["01.mp4"])
    (tmp_path / "01.mp4").write_bytes(b"x")
    waits = []
    graph = FakeGraph(["IN_PROGRESS", "IN_PROGRESS", "FINISHED"])
    publish_item(item, tmp_path, graph, "IG", FakeStorage(), sleep=waits.append)
    assert graph.posts[0][1]["media_type"] == "REELS" and len(waits) == 2


def test_publish_error_still_deletes_public_files(session, workspace, tmp_path):
    item = _item(session, workspace, "imagen", ["01.jpg"])
    (tmp_path / "01.jpg").write_bytes(b"x")
    storage = FakeStorage()
    with pytest.raises(PublishError):
        publish_item(item, tmp_path, FakeGraph(["ERROR"]), "IG", storage, sleep=lambda s: None)
    assert storage.deleted == storage.uploaded


def test_run_due(session, workspace):
    now = datetime(2026, 11, 20, 22, 0)
    on_time = _item(session, workspace, "imagen", ["01.jpg"], now - timedelta(minutes=2))
    late = _item(session, workspace, "imagen", ["01.jpg"], now - timedelta(hours=5))
    future = _item(session, workspace, "imagen", ["01.jpg"], now + timedelta(hours=1))
    broken = _item(session, workspace, "reel", ["01.mp4"], now - timedelta(minutes=1))
    session.commit()

    def publish(item):
        if item.kind == "reel":
            raise PublishError("video roto")
        return "M1", "https://instagram.com/p/M1"

    run_due(session, workspace.id, now, 30, publish)
    assert (on_time.status, on_time.media_id) == ("publicada", "M1")
    assert late.status == "vencida"  # la compu estaba apagada: no publica sola
    assert future.status == "pendiente"
    assert broken.status == "error" and "video roto" in broken.error

    run_due(session, workspace.id, now + timedelta(minutes=5), 30, publish)
    assert on_time.attempts == 1  # no se publica dos veces


@pytest.fixture
def sched_web(tmp_path):
    import shutil

    from fastapi.testclient import TestClient

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
        instagram_publish_dir=tmp_path / "programadas",
    )
    app = create_app(settings)
    with TestClient(app, base_url="http://testserver") as client:
        client.tmp = tmp_path
        client.app = app
        yield client


def test_schedule_flow(sched_web):
    from cmr_leads.instagram.stats import local_now

    day = local_now().date() + timedelta(days=3)
    page = sched_web.get("/instagram/programar?formato=imagen")
    assert page.status_code == 200 and "Programar en Instagram" in page.text

    r = sched_web.post(
        "/instagram/programar",
        data={"formato": "imagen", "dia": day.isoformat(), "hora": "19:30", "texto": "Hola"},
        files=[("files", ("foto.png", _png(), "image/png"))],
        follow_redirects=False,
    )
    assert r.status_code == 303 and "Programado" in r.headers["location"]

    from cmr_leads.db.session import make_engine, make_session_factory

    engine = make_engine(f"sqlite:///{sched_web.tmp / 'web.db'}")
    with make_session_factory(engine)() as s:
        item = s.scalars(select(InstagramScheduled)).one()
        plan = s.get(InstagramPlanItem, item.plan_item_id)
        assert item.publish_at == datetime(day.year, day.month, day.day, 22, 30)  # 19:30 AR
        assert (plan.kind, plan.hour, plan.day.date()) == ("imagen", 19, day)
        assert (sched_web.tmp / "programadas" / str(item.id) / "01.jpg").exists()
        item_id = item.id

    cal = sched_web.get(f"/instagram/calendario?mes={day:%Y-%m}")
    assert "⏰ Imagen · 19:30" in cal.text

    past = sched_web.post(
        "/instagram/programar",
        data={"formato": "imagen", "dia": "2020-01-01", "hora": "10:00"},
        files=[("files", ("foto.png", _png(), "image/png"))],
        follow_redirects=False,
    )
    assert "pas%C3%B3" in past.headers["location"] or "pasó" in past.headers["location"]

    r = sched_web.post(f"/instagram/programadas/{item_id}/cancelar", follow_redirects=False)
    assert r.status_code == 303
    with make_session_factory(engine)() as s:
        item = s.get(InstagramScheduled, item_id)
        assert item.status == "cancelada" and item.plan_item_id is None
        assert s.scalars(select(InstagramPlanItem)).first() is None
    assert not (sched_web.tmp / "programadas" / str(item_id)).exists()

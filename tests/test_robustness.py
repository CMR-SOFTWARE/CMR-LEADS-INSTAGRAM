"""Barrido de robustez: todas las páginas de estadísticas, en meses pasados y futuros, con
datos normales, vacíos y raros. Que ninguna se caiga y que los números cierren entre sí."""

import random
import re
import shutil
from datetime import date, datetime, timedelta

import pytest

import cmr_leads.ads.service as ads_service
import cmr_leads.ads.web as ads_web
import cmr_leads.instagram.stats as ig_stats
import cmr_leads.instagram.web as ig_web
from cmr_leads.db.models import (
    InstagramAccountSnapshot,
    InstagramDaily,
    InstagramPlanItem,
    InstagramPost,
    InstagramScheduled,
)
from cmr_leads.db.session import get_or_create_workspace
from cmr_leads.instagram.calendar import build_calendar
from cmr_leads.instagram.dashboard import build_dashboard
from cmr_leads.settings import Settings

from .conftest import ROOT

TODAYS = [date(2026, 11, 16), date(2027, 1, 1), date(2028, 2, 29)]
FEED = ("reel", "carrusel", "imagen")
SUSPECT = re.compile(r">\s*(None|nan|NaN|inf|Infinity|undefined)\s*<|\bNone%|nan%")
KINDS = {
    "reel": ("REELS", "VIDEO"),
    "carrusel": ("FEED", "CAROUSEL_ALBUM"),
    "imagen": ("FEED", "IMAGE"),
    "historia": ("STORY", "IMAGE"),
}


def seed(session, ws_id: int) -> None:
    """Un año de datos parecidos a los reales, más filas raras o incompletas."""
    rnd = random.Random(7)
    start = date(2026, 4, 1)
    day = start
    n = 0
    while day <= date(2026, 11, 15):
        session.add(
            InstagramDaily(
                workspace_id=ws_id,
                day=datetime(day.year, day.month, day.day),
                views=rnd.randint(0, 900),
                reach=rnd.randint(0, 400),
                profile_views=rnd.randint(0, 30),
                follows=rnd.choice([0, 0, 1, 2, 5]) if day.day % 9 else None,
            )
        )
        if day.weekday() in (1, 4):
            kind = rnd.choice(FEED)
            product, media = KINDS[kind]
            n += 1
            session.add(
                InstagramPost(
                    workspace_id=ws_id,
                    media_id=f"p{n}",
                    product_type=product,
                    media_type=media,
                    published_at=datetime(day.year, day.month, day.day, rnd.randint(10, 23)),
                    views=rnd.randint(50, 3000),
                    reach=rnd.randint(30, 1500),
                    follows=rnd.randint(0, 4),
                    caption=f"Publicación {n}",
                )
            )
        for _ in range(rnd.randint(0, 3)):
            n += 1
            session.add(
                InstagramPost(
                    workspace_id=ws_id,
                    media_id=f"s{n}",
                    product_type="STORY",
                    media_type="IMAGE",
                    published_at=datetime(day.year, day.month, day.day, rnd.randint(8, 23)),
                    views=rnd.randint(10, 200),
                    reach=rnd.randint(5, 150),
                )
            )
        day += timedelta(days=1)
    for d, total in ((date(2026, 11, 2), 300), (date(2026, 11, 14), 315)):
        session.add(
            InstagramAccountSnapshot(
                workspace_id=ws_id, day=datetime(d.year, d.month, d.day), followers=total
            )
        )
    # Filas raras: sin datos, en ceros, con fechas viejísimas o futuras, tipos desconocidos.
    session.add_all(
        [
            InstagramPost(workspace_id=ws_id, media_id="w1"),
            InstagramPost(
                workspace_id=ws_id,
                media_id="w2",
                product_type="REELS",
                media_type="VIDEO",
                published_at=datetime(2026, 11, 16, 3),
                views=0,
                reach=0,
                follows=0,
            ),
            InstagramPost(
                workspace_id=ws_id,
                media_id="w3",
                product_type="STORY",
                published_at=datetime(2031, 1, 1, 12),
                views=5,
            ),
            InstagramPost(
                workspace_id=ws_id,
                media_id="w4",
                product_type="RARO",
                media_type="RARO",
                published_at=datetime(2026, 10, 10, 12),
                caption="x" * 5000,
            ),
            InstagramDaily(workspace_id=ws_id, day=datetime(2016, 6, 6), follows=-3, views=-1),
            InstagramDaily(workspace_id=ws_id, day=datetime(2030, 1, 1), follows=5, views=3),
            InstagramAccountSnapshot(
                workspace_id=ws_id, day=datetime(2026, 11, 15), followers=None
            ),
            InstagramPlanItem(workspace_id=ws_id, day=datetime(2030, 2, 1), kind="reel"),
            InstagramPlanItem(workspace_id=ws_id, day=datetime(2026, 12, 2), kind="raro"),
            InstagramPlanItem(
                workspace_id=ws_id, day=datetime(2026, 12, 3), kind="imagen", removed=True
            ),
        ]
    )
    session.flush()
    session.add_all(
        [
            InstagramScheduled(
                workspace_id=ws_id,
                kind="reel",
                publish_at=datetime(2026, 1, 1),
                status="error",
                files=[],
                error="x",
            ),
            InstagramScheduled(
                workspace_id=ws_id, kind="raro", publish_at=datetime(2026, 12, 5), status="raro"
            ),
        ]
    )
    session.commit()


def freeze(monkeypatch, today: date) -> None:
    class FakeDate(date):
        @classmethod
        def today(cls):
            return today

    now = datetime(today.year, today.month, today.day, 12)
    monkeypatch.setattr(ig_web, "local_now", lambda: now)
    monkeypatch.setattr(ig_stats, "local_now", lambda: now)
    monkeypatch.setattr(ads_web, "date", FakeDate)
    monkeypatch.setattr(ads_service, "date", FakeDate)


def months(first: date, last: date):
    m = first.replace(day=1)
    while m <= last:
        yield m
        m = (m + timedelta(days=32)).replace(day=1)


@pytest.fixture(params=["datos", "vacia"])
def app_settings(request, tmp_path):
    from cmr_leads.db.session import make_engine, make_session_factory, run_migrations

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    shutil.copy(ROOT / "config" / "scoring.yaml", config_dir / "scoring.yaml")
    url = f"sqlite:///{tmp_path / 'web.db'}"
    run_migrations(url)
    if request.param == "datos":
        with make_session_factory(make_engine(url))() as session:
            seed(session, get_or_create_workspace(session, "cmr").id)
    return Settings(
        _env_file=None,
        database_url=url,
        scoring_config=config_dir / "scoring.yaml",
        website_fetch_enabled=False,
        backup_dir=tmp_path / "backups",
        meta_ads_sync_hours=0,
        instagram_publish_dir=tmp_path / "programadas",
    )


@pytest.mark.parametrize("today", TODAYS, ids=str)
def test_pages_never_break(app_settings, monkeypatch, today):
    from fastapi.testclient import TestClient

    from cmr_leads.web.app import create_app

    freeze(monkeypatch, today)
    urls = ["/instagram/programar", "/publicidad/reportes", "/publicidad/alertas"]
    for p in ("7d", "30d", "mes", "mes_anterior", "anio", "raro"):
        urls += [f"/instagram?periodo={p}", f"/publicidad?periodo={p}"]
    for f in ("reel", "carrusel", "imagen", "historia", "raro"):
        urls.append(f"/instagram?periodo=anio&formato={f}")
    for a, b in (
        ("2020-01-01", "2020-01-31"),
        ("2025-01-01", "2030-12-31"),
        (str(today), str(today)),
        ("2026-08-15", "2026-11-16"),
        ("mal", "fecha"),
        ("2026-11-30", "2026-11-01"),
    ):
        urls += [
            f"/instagram?periodo=rango&desde={a}&hasta={b}",
            f"/publicidad?periodo=rango&desde={a}&hasta={b}",
        ]
    for m in months(date(today.year - 1, 1, 1), date(today.year + 1, 12, 1)):
        urls += [f"/instagram/calendario?mes={m:%Y-%m}", f"/publicidad/reportes/{m:%Y-%m}"]
    urls += ["/instagram/calendario.ics", "/instagram/calendario?mes=2026-13"]
    with TestClient(create_app(app_settings), base_url="http://testserver") as client:
        for url in urls:
            r = client.get(url, follow_redirects=False)
            assert r.status_code < 400, url
            if "text/html" in r.headers.get("content-type", ""):
                assert not SUSPECT.search(r.text), url


@pytest.mark.parametrize("today", TODAYS, ids=str)
def test_numbers_add_up_every_month(session, workspace, today):
    seed(session, workspace.id)
    year = build_dashboard(session, workspace.id, date(today.year, 1, 1), today)
    rows = year["followers"]["months"]
    # Cada cierre = cierre del mes anterior + ganados; ningún total negativo.
    for prev, cur in zip(rows, rows[1:], strict=False):
        if None not in (prev["total"], cur["total"], cur["gained"]):
            assert prev["total"] + cur["gained"] == cur["total"], cur["name"]
    assert all(r["total"] is None or r["total"] >= 0 for r in rows)
    by_month = {r["month"]: r for r in rows}
    first = min(by_month) if by_month else None
    for m in months(date(today.year - 1, 1, 1), today + timedelta(days=60)):
        until = min((m + timedelta(days=32)).replace(day=1) - timedelta(days=1), max(today, m))
        d = build_dashboard(session, workspace.id, m, until)
        # El total de vistas es la suma de la serie y la serie no se sale del período.
        assert (d["totals"]["views"] or 0) == sum(x["views"] for x in d["series"])
        assert all(m <= x["day"] <= until for x in d["series"])
        # "Ganados" del mes es igual en la tarjeta y en la tabla.
        r = by_month.get(m)
        fl = d["followers"]
        if r and m != first and None not in (r["gained"], fl["gained"]) and until <= today:
            assert fl["gained"] == r["gained"], m
    # Si se pide un rango a mitad de mes, las barras suman lo mismo que la tarjeta.
    d = build_dashboard(session, workspace.id, date(2026, 8, 15), date(2026, 11, 16))
    if d["followers"]["gained"] is not None and all(
        x["followers_total"] is not None for x in d["series"]
    ):
        assert sum(x["follows"] for x in d["series"]) == d["followers"]["gained"]


@pytest.mark.parametrize("with_data", [True, False], ids=["datos", "vacia"])
@pytest.mark.parametrize("today", TODAYS, ids=str)
def test_calendar_rules_every_month(session, workspace, today, with_data):
    if with_data:
        seed(session, workspace.id)
    for m in months(date(today.year - 1, 1, 1), date(today.year + 1, 12, 1)):
        c = build_calendar(session, workspace.id, m, today, [])
        feed = []
        for row in c["weeks"]:
            for cell in row:
                for i in cell["items"]:
                    assert cell["in_month"], m
                    if not i.get("fixed"):
                        assert cell["date"] >= today, (m, cell["date"])
                    if i["kind"] in FEED:
                        feed.append((cell["date"], i["kind"], bool(i.get("fixed"))))
        feed.sort()
        for (d1, k1, f1), (d2, k2, f2) in zip(feed, feed[1:], strict=False):
            if f1 and f2:  # lo fijado a mano se respeta aunque no cumpla las reglas
                continue
            assert (d2 - d1).days > 1, (m, d1, d2)
            assert not (k1 == k2 == "reel"), (m, d1, d2)


def test_old_saved_report_still_opens():
    from cmr_leads.ads.report import normalize_report_data

    d = normalize_report_data({"month": "2026-10", "totals": {"spend": 10}, "ads": [{"name": "A"}]})
    assert d["totals"]["cost_per_thousand_reach"] is None
    assert d["changes"]["spend"] is None and d["best_ads"] == []
    assert d["ads"][0]["spend"] == 0 and d["ads"][0]["reach"] is None

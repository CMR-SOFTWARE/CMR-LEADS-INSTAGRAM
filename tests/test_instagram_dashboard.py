import json
from datetime import date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from cmr_leads.advice import get_advice, request_advice
from cmr_leads.db.models import Advice, InstagramDaily, InstagramPost
from cmr_leads.instagram.client import GraphClient
from cmr_leads.instagram.dashboard import build_dashboard, slot_of
from cmr_leads.instagram.stats import (
    BACKFILL_PER_SYNC,
    REFRESH_DAYS,
    days_to_fetch,
    link_breakdown,
    sync_stats,
)
from cmr_leads.settings import Settings

IG = "1784"


def _graph():
    calls: list[str] = []

    def handler(request):
        path = request.url.path.split("/v23.0/")[1]
        params = request.url.params
        calls.append(f"{path}?{params.get('metric', '')}")
        if path == f"{IG}/stories":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "s1", "media_type": "IMAGE", "timestamp": "2026-01-05T14:00:00+0000"}
                    ]
                },
            )
        if path == f"{IG}/media":
            if params.get("after") == "c2":
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "id": "old",
                                "media_type": "IMAGE",
                                "media_product_type": "FEED",
                                "timestamp": "2025-12-20T14:00:00+0000",
                            }
                        ],
                        "paging": {"cursors": {"after": "c3"}, "next": "https://x"},
                    },
                )
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "r1",
                            "caption": "Reel de canchas",
                            "media_type": "VIDEO",
                            "media_product_type": "REELS",
                            "timestamp": "2026-01-03T22:00:00+0000",
                            "like_count": 10,
                            "comments_count": 1,
                        },
                        {
                            "id": "i1",
                            "caption": "Imagen de turnos",
                            "media_type": "IMAGE",
                            "media_product_type": "FEED",
                            "timestamp": "2026-01-02T13:00:00+0000",
                        },
                    ],
                    "paging": {"cursors": {"after": "c2"}, "next": "https://x"},
                },
            )
        if path.endswith("/insights") and not path.startswith(IG):
            media = path.split("/")[0]
            metrics = params["metric"].split(",")
            if media == "r1" and len(metrics) > 1:  # Meta rechaza el pedido conjunto
                return httpx.Response(400, json={"error": {"code": 100, "message": "bad metric"}})
            value = {"r1": 900, "i1": 150, "s1": 40}[media]
            return httpx.Response(
                200, json={"data": [{"name": m, "values": [{"value": value}]} for m in metrics]}
            )
        if path == f"{IG}/insights":
            if params["metric"] == "profile_links_taps":
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "name": "profile_links_taps",
                                "total_value": {
                                    "value": 3,
                                    "breakdowns": [
                                        {
                                            "dimension_keys": ["contact_button_type"],
                                            "results": [
                                                {"dimension_values": ["CALL"], "value": 2},
                                                {"dimension_values": ["DIRECTION"], "value": 1},
                                            ],
                                        }
                                    ],
                                },
                            }
                        ]
                    },
                )
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"name": m, "total_value": {"value": 10}}
                        for m in params["metric"].split(",")
                    ]
                },
            )
        return httpx.Response(404, json={"error": {"code": 100, "message": path}})

    client = GraphClient(
        "token", "v23.0", http=httpx.Client(transport=httpx.MockTransport(handler))
    )
    return client, calls


def test_sync_stats_saves_days_posts_and_stories(session, workspace):
    client, calls = _graph()
    result = sync_stats(session, workspace.id, client, IG, today=date(2026, 1, 5))

    assert result.days == 5 and result.stories == 1 and result.posts == 2
    days = list(session.scalars(select(InstagramDaily).order_by(InstagramDaily.day)))
    assert days[0].day == datetime(2026, 1, 1)
    assert days[0].views == 10 and days[0].link_taps == 3
    assert days[0].link_breakdown == {"CALL": 2, "DIRECTION": 1}

    reel = session.scalar(select(InstagramPost).where(InstagramPost.media_id == "r1"))
    assert reel.views == 900 and reel.kind == "Reel / video"  # se pidió métrica por métrica
    story = session.scalar(select(InstagramPost).where(InstagramPost.media_id == "s1"))
    assert story.is_story and story.kind == "Historia" and story.views == 40
    # La publicación de diciembre corta la paginación y no se guarda.
    assert session.scalar(select(func.count()).where(InstagramPost.media_id == "old")) == 0


def test_days_to_fetch_backfills_in_batches(session, workspace):
    days, pending = days_to_fetch(session, workspace.id, date(2026, 11, 13))
    total = (date(2026, 11, 13) - date(2026, 1, 1)).days + 1
    assert len(days) == REFRESH_DAYS + BACKFILL_PER_SYNC
    assert days[0] == date(2026, 11, 13)  # primero lo más nuevo
    assert pending == total - REFRESH_DAYS - BACKFILL_PER_SYNC


def test_link_breakdown_without_data():
    assert link_breakdown({"data": []}) == (None, {})


def _post(media_id, kind, when, views, product="FEED", media_type="IMAGE"):
    return InstagramPost(
        workspace_id=1,
        media_id=media_id,
        media_type=media_type,
        product_type=product,
        caption=f"{kind} {media_id}",
        published_at=when,
        views=views,
        reach=views // 2,
        total_interactions=5,
    )


def test_dashboard_groups_formats_days_hours_and_stories(session, workspace):
    session.add_all(
        [
            # Horas en UTC: 22 UTC = 19 h de Argentina.
            _post("r1", "reel", datetime(2026, 11, 4, 22), 1000, "REELS", "VIDEO"),  # miércoles
            _post("r2", "reel", datetime(2026, 11, 11, 22), 800, "REELS", "VIDEO"),  # miércoles
            _post("r3", "reel", datetime(2026, 11, 7, 13), 200, "REELS", "VIDEO"),  # sábado
            _post("i1", "imagen", datetime(2026, 11, 5, 13), 300),
            _post("s1", "historia", datetime(2026, 11, 10, 14), 50, "STORY"),
            _post("s2", "historia", datetime(2026, 11, 10, 20), 70, "STORY"),
            _post("s3", "historia", datetime(2026, 11, 12, 14), 40, "STORY"),
            InstagramDaily(
                workspace_id=workspace.id,
                day=datetime(2026, 11, 10),
                views=500,
                reach=200,
                profile_views=20,
                website_clicks=4,
                link_taps=2,
                link_breakdown={"CALL": 2},
            ),
        ]
    )
    session.flush()
    d = build_dashboard(session, workspace.id, date(2026, 11, 1), date(2026, 11, 13))

    assert [f["name"] for f in d["formats"]] == ["Reel / video", "Imagen", "Historia"]
    reels = d["formats"][0]
    assert reels["count"] == 3 and reels["avg_views"] == 667
    assert reels["times"]["best_day"]["name"] == "Miércoles"
    assert reels["times"]["best_slot"]["name"] == slot_of(19) == "18 a 21 h"
    assert d["post_count"] == 4  # las historias van aparte

    st = d["stories"]
    assert st["total"] == 3 and st["views"] == 160
    assert st["days_with_stories"] == 2
    # Solo cuenta desde el primer día con historias guardadas (10/11): 3 historias en 4 días.
    assert st["since"] == date(2026, 11, 10) and st["per_day_avg"] == 0.8
    martes = st["weekdays"][1]
    assert martes["count"] == 2 and martes["avg_views"] == 60

    assert d["links"] == [{"name": "Link de la bio", "value": 4}, {"name": "Llamar", "value": 2}]
    assert d["totals"]["views"] == 500 and d["totals"]["reach_avg"] == 200

    only_stories = build_dashboard(
        session, workspace.id, date(2026, 11, 1), date(2026, 11, 13), "historia"
    )
    assert {p.media_id for p in only_stories["posts"]} == {"s1", "s2", "s3"}


def _gemini(answer: dict):
    sent = {}

    def handler(request):
        sent["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(answer)}]}}]}
        )

    return httpx.Client(transport=httpx.MockTransport(handler)), sent


def test_request_advice_cleans_and_saves(session, workspace):
    http, sent = _gemini(
        {
            "resumen": "Los reels rinden más — por lejos.",
            "decision": "Subí más reels",
            "acciones": [{"accion": "Subí 2 reels por semana", "porque": "Promedian 667 vistas"}],
            "cuidado": "",
        }
    )
    settings = Settings(_env_file=None, gemini_api_key="k")
    advice = request_advice(
        session, workspace.id, settings, "instagram", "30d|x", {"vistas": 500}, http
    )
    assert "—" not in advice.data["resumen"]
    assert advice.data["acciones"][0]["accion"] == "Subí 2 reels por semana"
    assert '"vistas": 500' in sent["body"]["contents"][0]["parts"][0]["text"]

    request_advice(session, workspace.id, settings, "instagram", "30d|x", {"vistas": 1}, http)
    assert session.scalar(select(func.count(Advice.id))) == 1
    assert get_advice(session, workspace.id, "instagram", "30d|x") is not None


@pytest.fixture
def web(tmp_path):
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
    )
    app = create_app(settings)
    with TestClient(app, base_url="http://testserver") as client:
        client.app_state = app.state
        yield client


def test_instagram_page_with_filters(web):
    from cmr_leads.db.session import get_or_create_workspace
    from cmr_leads.instagram.stats import local_now

    today = local_now().date()
    with web.app_state.session_factory() as session:
        ws = get_or_create_workspace(session, "cmr")
        when = datetime(today.year, today.month, today.day, 15)
        session.add_all(
            [
                _post("r1", "reel", when, 900, "REELS", "VIDEO"),
                _post("s1", "historia", when, 50, "STORY"),
                InstagramDaily(workspace_id=ws.id, day=datetime(*today.timetuple()[:3]), views=900),
            ]
        )
        for p in session.new:
            p.workspace_id = ws.id
        session.commit()

    page = web.get("/instagram?periodo=7d")
    assert page.status_code == 200
    assert 'data-chart="ig-time"' in page.text
    assert "Reel / video: qué día y a qué hora se ve más" in page.text
    assert "Historias subidas" in page.text
    assert "—" not in page.text.split("<main")[1]

    stories = web.get("/instagram?periodo=anio&formato=historia&desde=&hasta=")
    assert stories.status_code == 200

    no_key = web.post("/instagram/analizar", data={"periodo": "7d"}, follow_redirects=True)
    assert "GEMINI_API_KEY" in no_key.text or "No se pudo analizar" in no_key.text


@pytest.mark.parametrize("raw", ["v26.", "v26", "26", "v26.0"])
def test_graph_version_typos_are_fixed(raw):
    settings = Settings(_env_file=None, meta_graph_api_version=raw, meta_ads_graph_api_version=raw)
    assert settings.meta_graph_api_version == settings.meta_ads_graph_api_version == "v26.0"


def test_instagram_calendar_page_and_ics(web):
    from cmr_leads.db.session import get_or_create_workspace
    from cmr_leads.instagram.stats import local_now

    today = local_now().date()
    with web.app_state.session_factory() as session:
        ws = get_or_create_workspace(session, "cmr")
        base = datetime(today.year, today.month, 1) - timedelta(days=60)
        posts = []
        for i in range(12):
            when = base + timedelta(days=i * 4, hours=21)  # 18 h en Argentina
            kind = ("reel", "REELS", "VIDEO") if i % 2 else ("imagen", "FEED", "IMAGE")
            posts.append(_post(f"p{i}", kind[0], when, 400 + i * 50, kind[1], kind[2]))
        session.add_all(posts)
        for p in session.new:
            p.workspace_id = ws.id
        session.commit()

    page = web.get(f"/instagram/calendario?mes={today:%Y-%m}")
    assert page.status_code == 200
    assert "El plan del mes" in page.text
    assert "historias" in page.text
    assert "—" not in page.text.split("<main")[1]
    assert web.get("/instagram/calendario?mes=cualquiera").status_code == 200

    ics = web.get(f"/instagram/calendario.ics?mes={today:%Y-%m}")
    assert ics.status_code == 200
    assert ics.headers["content-type"].startswith("text/calendar")
    assert "BEGIN:VEVENT" in ics.text and "Instagram: subir" in ics.text


def test_instagram_calendar_changes_endpoint(web):
    from cmr_leads.instagram.stats import local_now

    today = local_now().date()
    nxt = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    mes = f"{nxt:%Y-%m}"
    day = nxt + timedelta(days=9)

    r = web.post(
        "/instagram/calendario/cambio",
        data={
            "accion": "agregar",
            "dia": day.isoformat(),
            "hora": "11",
            "formato": "carrusel",
            "mes": mes,
        },
    )
    assert r.status_code == 200 and r.json() == {"ok": True}
    page = web.get(f"/instagram/calendario?mes={mes}")
    assert "📌 Carrusel · 11:00" in page.text

    blocked = (day + timedelta(days=1)).isoformat()
    assert (
        web.post(
            "/instagram/calendario/cambio", data={"accion": "bloquear", "dia": blocked}
        ).status_code
        == 200
    )
    assert "No disponible" in web.get(f"/instagram/calendario?mes={mes}").text

    past = web.post(
        "/instagram/calendario/cambio",
        data={"accion": "agregar", "dia": "2020-01-01", "hora": "11", "formato": "reel"},
    )
    assert past.status_code == 400

    assert (
        web.post(
            "/instagram/calendario/cambio", data={"accion": "reiniciar", "mes": mes}
        ).status_code
        == 200
    )
    page = web.get(f"/instagram/calendario?mes={mes}")
    assert "📌" not in page.text.split("cal-grid")[1] and "No disponible" not in page.text

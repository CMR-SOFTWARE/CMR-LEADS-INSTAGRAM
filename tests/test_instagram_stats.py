from datetime import datetime

import httpx

from cmr_leads.db.models import InstagramAccountSnapshot, InstagramPost, Lead
from cmr_leads.domain.enums import LeadStatus, SourceType
from cmr_leads.domain.schemas import RawProfile, SourceRecord
from cmr_leads.instagram.analysis import build_report
from cmr_leads.instagram.client import GraphClient
from cmr_leads.instagram.sync import daily_budget_guard, sync_instagram
from cmr_leads.pipeline.budget import DailyBudget
from cmr_leads.pipeline.ingest import ingest
from cmr_leads.replies.classifier import ReplyClassifier

IG = "1784"
PAGE = "999"


def _graph(dead_metrics=()):
    """Simula la Graph API según la ruta pedida."""
    calls = []

    def handler(request):
        path = request.url.path.split("/v23.0/")[1]
        params = request.url.params
        calls.append(path)
        if path == IG:
            return httpx.Response(
                200, json={"username": "cmr.software", "followers_count": 820, "media_count": 40}
            )
        if path == f"{IG}/insights":
            metric = params["metric"]
            if metric in dead_metrics:
                return httpx.Response(
                    400, json={"error": {"code": 100, "message": f"{metric} deprecated"}}
                )
            return httpx.Response(
                200, json={"data": [{"name": metric, "total_value": {"value": 100}}]}
            )
        if path == f"{IG}/media":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "m1",
                            "caption": "Reel: antes y después de una web",
                            "media_type": "VIDEO",
                            "media_product_type": "REELS",
                            "timestamp": "2026-10-20T22:00:00+0000",
                            "permalink": "https://instagram.com/p/m1",
                            "like_count": 30,
                            "comments_count": 4,
                        },
                        {
                            "id": "m2",
                            "caption": "Foto del equipo",
                            "media_type": "IMAGE",
                            "media_product_type": "FEED",
                            "timestamp": "2026-10-21T13:00:00+0000",
                            "permalink": "https://instagram.com/p/m2",
                            "like_count": 5,
                            "comments_count": 0,
                        },
                    ]
                },
            )
        if path.endswith("/insights") and path.startswith("m"):
            metric = params["metric"]
            value = {"m1": 900, "m2": 150}[path.split("/")[0]]
            if metric == "saved":
                value = 12 if path.startswith("m1") else 0
            if metric == "views" and path.startswith("m2"):
                return httpx.Response(
                    400, json={"error": {"code": 100, "message": "not supported"}}
                )
            return httpx.Response(
                200, json={"data": [{"name": metric, "values": [{"value": value}]}]}
            )
        if path == PAGE:
            return httpx.Response(200, json={"access_token": "page-token", "id": PAGE})
        if path == f"{PAGE}/conversations":
            assert params["access_token"] == "page-token"
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "c1",
                            "participants": {
                                "data": [{"username": "cmr.software"}, {"username": "pelu.lu"}]
                            },
                        },
                        {
                            "id": "c2",
                            "participants": {
                                "data": [
                                    {"username": "cmr.software"},
                                    {"username": "nuevo.negocio"},
                                ]
                            },
                        },
                    ]
                },
            )
        if path == "c1":
            return httpx.Response(
                200,
                json={
                    "messages": {
                        "data": [
                            {
                                "created_time": "2026-10-22T15:00:00+0000",
                                "from": {"username": "pelu.lu"},
                                "message": "Cuánto sale?",
                            },
                            {
                                "created_time": "2026-10-22T10:00:00+0000",
                                "from": {"username": "cmr.software"},
                                "message": "Hola! Soy Tomás de CMR",
                            },
                        ]
                    }
                },
            )
        if path == "c2":
            return httpx.Response(
                200,
                json={
                    "messages": {
                        "data": [
                            {
                                "created_time": "2026-10-23T12:00:00+0000",
                                "from": {"username": "nuevo.negocio"},
                                "message": "Hola, me interesa una web para mi local",
                            },
                        ]
                    }
                },
            )
        raise AssertionError(f"ruta inesperada {path}")

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def test_sync_stats_posts_and_dms(session, workspace):
    lead = ingest(
        session,
        workspace,
        SourceRecord(
            profile=RawProfile(instagram_username="pelu.lu"), source=SourceType.CMR_FOLLOWERS
        ),
        "AR",
    ).lead
    http, calls = _graph(dead_metrics=("views",))
    result = sync_instagram(
        session, workspace, GraphClient("tok", http=http), IG, PAGE, ReplyClassifier(), "AR"
    )
    assert result.stopped_reason is None, result.stopped_reason
    assert result.account and result.posts == 2
    assert any("views" in w for w in result.warnings)  # métrica dada de baja: no corta el resto

    snapshot = session.query(InstagramAccountSnapshot).one()
    assert snapshot.followers == 820 and snapshot.reach == 100 and snapshot.views is None

    reel = session.query(InstagramPost).filter_by(media_id="m1").one()
    assert reel.kind == "Reel / video" and reel.reach == 900 and reel.saves == 12

    # La respuesta de un lead existente se registra y clasifica.
    assert lead.status == LeadStatus.REPLIED
    replies = [i for i in lead.interactions if i.direction == "inbound"]
    assert replies[0].outcome == "pide_info"
    assert any(i.direction == "outbound" for i in lead.interactions)
    # Quien escribió primero a CMR se crea como lead nuevo.
    new = session.query(Lead).filter_by(instagram_username="nuevo.negocio").one()
    assert {s.source for s in new.sources} == {"instagram_dm"}
    assert result.new_leads == 1 and result.replies == 2

    # Volver a sincronizar no duplica nada.
    http, _ = _graph()
    again = sync_instagram(
        session, workspace, GraphClient("tok", http=http), IG, PAGE, ReplyClassifier(), "AR"
    )
    assert again.replies == 0 and again.new_leads == 0
    assert session.query(InstagramPost).count() == 2


def test_sync_without_page_id_skips_messages(session, workspace):
    http, calls = _graph()
    result = sync_instagram(
        session, workspace, GraphClient("tok", http=http), IG, None, ReplyClassifier(), "AR"
    )
    assert result.posts == 2 and not any(c.startswith(PAGE) for c in calls)
    assert any("FACEBOOK_PAGE_ID" in w for w in result.warnings)


def test_daily_budget_stops_sync(session, workspace):
    http, _ = _graph()
    budget = DailyBudget(session, workspace.id, "meta_stats", 3)
    client = GraphClient("tok", http=http, on_request=daily_budget_guard(budget))
    result = sync_instagram(session, workspace, client, IG, None, ReplyClassifier(), "AR")
    assert "tope diario" in result.stopped_reason
    assert budget.used() == 3


def test_expired_token_is_reported(session, workspace):
    def handler(request):
        return httpx.Response(400, json={"error": {"code": 190, "message": "Session has expired"}})

    client = GraphClient("tok", http=httpx.Client(transport=httpx.MockTransport(handler)))
    result = sync_instagram(session, workspace, client, IG, None, ReplyClassifier(), "AR")
    assert "token" in result.stopped_reason


def _post(session, workspace, i, kind, reach, weekday_day, hour_utc, interactions):
    media_type, product = {"reel": ("VIDEO", "REELS"), "img": ("IMAGE", "FEED")}[kind]
    session.add(
        InstagramPost(
            workspace_id=workspace.id,
            media_id=f"p{i}",
            media_type=media_type,
            product_type=product,
            caption=f"Post {i}",
            reach=reach,
            total_interactions=interactions,
            published_at=datetime(2026, 10, weekday_day, hour_utc),
        )
    )


def test_report_recommendations(session, workspace):
    # 19/10/2026 es lunes. Reels (lunes 23 UTC = 20 hs AR) rinden más que imágenes (jueves).
    for i in range(4):
        _post(session, workspace, i, "reel", 1000 + i, 19, 23, 80)
        _post(session, workspace, 10 + i, "img", 200, 22, 14, 10)
    session.flush()
    report = build_report(session, workspace.id)
    assert report.by_kind[0].name == "Reel / video"
    assert report.by_weekday[0].name == "Lunes"
    assert report.by_slot[0].name.startswith("Tarde-noche")
    text = " ".join(report.recommendations)
    assert "reel" in text.lower() and "lunes" in text


def test_report_needs_enough_posts(session, workspace):
    _post(session, workspace, 1, "reel", 100, 19, 12, 5)
    session.flush()
    assert "pocas publicaciones" in build_report(session, workspace.id).recommendations[0]

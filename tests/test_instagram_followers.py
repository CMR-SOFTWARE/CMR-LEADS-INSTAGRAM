from datetime import date, datetime

from sqlalchemy import select

from cmr_leads.db.models import InstagramAccountSnapshot, InstagramDaily, InstagramPost
from cmr_leads.instagram.dashboard import build_dashboard
from cmr_leads.instagram.stats import StatsResult, sync_followers
from cmr_leads.instagram.web import followers_vs_ads


class FakeClient:
    def __init__(self, payload):
        self.payload, self.calls = payload, []

    def get(self, path, **params):
        self.calls.append((path, params))
        return self.payload


def test_sync_followers_saves_each_day(session, workspace):
    session.add(InstagramDaily(workspace_id=workspace.id, day=datetime(2026, 11, 12), views=10))
    session.flush()
    client = FakeClient(
        {
            "data": [
                {
                    "name": "follower_count",
                    "values": [
                        {"value": 2, "end_time": "2026-11-13T08:00:00+0000"},
                        {"value": 8, "end_time": "2026-11-14T08:00:00+0000"},
                    ],
                }
            ]
        }
    )
    sync_followers(session, workspace.id, client, "IG", date(2026, 11, 14), StatsResult())
    rows = {r.day.date(): r for r in session.scalars(select(InstagramDaily))}
    assert rows[date(2026, 11, 12)].follows == 2 and rows[date(2026, 11, 12)].views == 10
    assert rows[date(2026, 11, 13)].follows == 8
    assert client.calls[0][1]["metric"] == "follower_count"


def _post(workspace, n, day, kind, views, follows):
    product, media = {"reel": ("REELS", "VIDEO"), "imagen": ("FEED", "IMAGE")}[kind]
    return InstagramPost(
        workspace_id=workspace.id,
        media_id=f"m{n}",
        media_type=media,
        product_type=product,
        caption=f"Post {n}",
        published_at=datetime(day.year, day.month, day.day, 15),
        views=views,
        follows=follows,
    )


def test_followers_summary(session, workspace):
    for d, follows, visits in ((1, 2, 20), (2, 6, 30), (3, 0, 10)):
        session.add(
            InstagramDaily(
                workspace_id=workspace.id,
                day=datetime(2026, 11, d),
                follows=follows,
                profile_views=visits,
            )
        )
    session.add(InstagramDaily(workspace_id=workspace.id, day=datetime(2026, 10, 30), follows=4))
    session.add(
        InstagramAccountSnapshot(
            workspace_id=workspace.id, day=datetime(2026, 11, 3), followers=338
        )
    )
    session.add_all(
        [
            _post(workspace, 1, date(2026, 11, 1), "reel", 2000, 5),
            _post(workspace, 2, date(2026, 11, 2), "imagen", 500, 2),
            _post(workspace, 3, date(2026, 11, 3), "imagen", 500, 0),
        ]
    )
    session.flush()
    d = build_dashboard(session, workspace.id, date(2026, 11, 1), date(2026, 11, 3))
    fl = d["followers"]
    assert (fl["current"], fl["gained"], fl["previous"]) == (338, 8, 4)
    assert fl["per_100_visits"] == 13.3  # 8 seguidores en 60 visitas
    assert fl["best_day"] == date(2026, 11, 2)
    assert [p.media_id for p in fl["top"]] == ["m1", "m2"]
    per = {f["key"]: f["per_1000"] for f in fl["formats"]}
    assert per == {"reel": 2.5, "imagen": 2.0}
    assert d["series"][1]["follows"] == 6

    ads = {date(2026, 11, 1): 500, date(2026, 11, 2): 300, date(2026, 11, 3): 0}
    vs = followers_vs_ads(d, ads)
    assert (vs["with_ads"], vs["without"], vs["days_with"]) == (4.0, 0.0, 2)


def test_instagram_page_shows_followers(session, workspace):
    from cmr_leads.instagram.web import build_charts

    session.add(InstagramDaily(workspace_id=workspace.id, day=datetime(2026, 11, 1), follows=3))
    session.flush()
    d = build_dashboard(session, workspace.id, date(2026, 11, 1), date(2026, 11, 1))
    charts = build_charts(d, {})
    assert "ig-follows" in charts


def test_followers_per_month(session, workspace):
    from cmr_leads.instagram.dashboard import follower_history, follower_months

    # 10 en septiembre, 20 en octubre, 10 en noviembre: 40 ganados, pero hoy hay 36 (4 se fueron).
    for day, follows in (
        (date(2026, 9, 10), 10),
        (date(2026, 10, 10), 20),
        (date(2026, 11, 5), 10),
    ):
        session.add(
            InstagramDaily(
                workspace_id=workspace.id,
                day=datetime(day.year, day.month, day.day),
                follows=follows,
            )
        )
    session.add(
        InstagramAccountSnapshot(
            workspace_id=workspace.id, day=datetime(2026, 11, 10), followers=36
        )
    )
    session.flush()
    history = follower_history(session, workspace.id)
    months = follower_months(
        history, {date(2026, 9, 1): 10, date(2026, 10, 1): 20}, date(2026, 11, 16)
    )
    by = {m["month"]: m for m in months}
    assert by[date(2026, 9, 1)]["total"] == 9  # 10 de 40, escalado a 36
    assert by[date(2026, 10, 1)]["total"] == 27
    assert by[date(2026, 11, 1)]["total"] == 36 and by[date(2026, 11, 1)]["real"]
    assert not by[date(2026, 10, 1)]["real"] and by[date(2026, 11, 1)]["current"]
    # Ganados = diferencia entre cierres, no lo que informa Meta por día (llega con atraso).
    assert by[date(2026, 10, 1)]["gained"] == 18 and by[date(2026, 11, 1)]["gained"] == 9
    assert by[date(2026, 9, 1)]["gained"] == 9  # primer mes: desde 0

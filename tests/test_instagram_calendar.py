from datetime import date, datetime, timedelta

from cmr_leads.db.models import InstagramPost
from cmr_leads.instagram.calendar import Stats, build_calendar, plan_week, to_ics, weekly_mix

TODAY = date(2026, 11, 13)


def make(ws, n, when, views, kind="reel", caption=None):
    media, product = {
        "reel": ("VIDEO", "REELS"),
        "imagen": ("IMAGE", "FEED"),
        "carrusel": ("CAROUSEL_ALBUM", "FEED"),
        "historia": ("IMAGE", "STORY"),
    }[kind]
    return InstagramPost(
        workspace_id=ws.id, media_id=str(n), media_type=media, product_type=product,
        caption=caption or f"post {n}", published_at=when, views=views, reach=views // 2,
    )  # fmt: skip


def _utc(d: date, hour_ar: int) -> datetime:
    return datetime(d.year, d.month, d.day, hour_ar) + timedelta(hours=3)


def test_one_viral_post_does_not_define_the_best_day(session, workspace):
    start = date(2026, 6, 1)  # lunes
    posts = []
    for week in range(10):
        monday = start + timedelta(weeks=week)
        posts.append(make(workspace, week * 10 + 1, _utc(monday, 18), 900))  # lunes, siempre bien
        posts.append(make(workspace, week * 10 + 2, _utc(monday + timedelta(days=3), 18), 300))
    posts.append(make(workspace, 999, _utc(date(2026, 9, 5), 18), 9000))  # un sábado viral
    stats = Stats(posts)
    _sabado, n = stats.day_score("reel", 5)
    assert n == 1
    # Para elegir el día, 1 solo dato no le gana a 10 lunes buenos.
    assert stats.day_rank("reel", 0) > stats.day_rank("reel", 5)
    week = plan_week(stats, ["reel"])
    assert week[0]["weekday"] == 0


def test_weekly_mix_follows_reach_and_spreads_days(session, workspace):
    posts = []
    for i in range(6):
        d = date(2026, 9, 1) + timedelta(days=i * 5)
        posts.append(make(workspace, i, _utc(d, 19), 2000, "reel"))
        posts.append(make(workspace, 100 + i, _utc(d + timedelta(days=1), 12), 400, "imagen"))
    stats = Stats(posts)
    mix = weekly_mix(stats, 3)
    assert mix[0] == "reel" and mix.count("reel") >= 2
    week = plan_week(stats, mix)
    days = sorted(p["weekday"] for p in week)
    assert len(set(days)) == 3
    assert week[0]["slot"] == "18 a 21 h" and week[0]["hour"] == 19


def test_build_calendar_month_marks_what_was_posted(session, workspace):
    for i in range(10):
        d = date(2026, 9, 1) + timedelta(days=i * 6)
        session.add(make(workspace, i, _utc(d, 18), 500 + i * 30, "reel" if i % 2 else "imagen"))
    session.add(make(workspace, 50, _utc(date(2026, 11, 3), 18), 700, "reel"))
    session.add(make(workspace, 60, _utc(date(2026, 11, 12), 10), 80, "historia"))
    session.add(make(workspace, 70, _utc(date(2026, 10, 20), 18), 9000, "reel",
                     caption="Promocionada con plata grande y más"))  # fmt: skip
    session.flush()
    c = build_calendar(
        session, workspace.id, date(2026, 11, 1), TODAY,
        ["Instagram post: Promocionada con plata grande..."],
    )  # fmt: skip
    assert c["title"] == "Noviembre 2026"
    assert c["promoted_excluded"] == 1
    assert 2 <= c["per_week"] <= 5
    cells = {cell["date"]: cell for row in c["weeks"] for cell in row}
    assert cells[date(2026, 11, 3)]["done"][0]["kind"] == "reel"
    assert cells[date(2026, 11, 12)]["stories_done"] == 1
    planned = sum(len(cell["items"]) for cell in cells.values() if cell["in_month"])
    assert planned >= c["per_week"] * 4
    assert c["test"] is not None and c["test"]["test"]
    ics = to_ics(c, "CMR")
    assert ics.startswith("BEGIN:VCALENDAR") and ics.count("BEGIN:VEVENT") >= planned + 30

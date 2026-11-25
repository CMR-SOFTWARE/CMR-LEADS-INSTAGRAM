from datetime import date, datetime, timedelta

from cmr_leads.db.models import InstagramPlanItem, InstagramPost
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
    assert planned >= 4  # del 13 al 30 de noviembre
    assert not any(cell["items"] for d, cell in cells.items() if d < TODAY)  # nada en el pasado
    assert c["test"] is not None and c["test"]["test"]
    ics = to_ics(c, "CMR")
    assert ics.startswith("BEGIN:VCALENDAR") and ics.count("BEGIN:VEVENT") >= planned + 30


def _seed(session, workspace):
    for i in range(10):
        d = date(2026, 9, 1) + timedelta(days=i * 6)
        session.add(make(workspace, i, _utc(d, 18), 500 + i * 30, "reel" if i % 2 else "imagen"))
    session.flush()


def _cells(c):
    return {cell["date"]: cell for row in c["weeks"] for cell in row if cell["in_month"]}


def _dt(d: date) -> datetime:
    return datetime(d.year, d.month, d.day)


def test_moving_a_recommendation_reprograms_the_week(session, workspace):
    _seed(session, workspace)
    month = date(2026, 12, 1)
    before = build_calendar(session, workspace.id, month, TODAY, [])
    week = before["weeks"][2]  # semana completa del medio del mes
    auto = [(cell["date"], i) for cell in week for i in cell["items"] if not i.get("test")]
    origin, item = auto[0]
    others_before = len(auto)
    target = next(cell["date"] for cell in week if not cell["items"] and cell["date"] != origin)

    session.add(
        InstagramPlanItem(
            workspace_id=workspace.id, day=_dt(target), hour=10, kind=item["kind"],
            origin_day=_dt(origin),
        )
    )  # fmt: skip
    session.flush()
    after = build_calendar(session, workspace.id, month, TODAY, [])
    cells = _cells(after)
    fixed = [i for i in cells[target]["items"] if i.get("fixed")]
    assert fixed and fixed[0]["time"] == "10:00" and fixed[0]["kind"] == item["kind"]
    week_items = [i for cell in after["weeks"][2] for i in cell["items"] if not i.get("test")]
    assert len(week_items) == others_before  # se movió, no se duplicó


def test_blocked_day_moves_the_recommendation_elsewhere(session, workspace):
    _seed(session, workspace)
    month = date(2026, 12, 1)
    before = build_calendar(session, workspace.id, month, TODAY, [])
    week = before["weeks"][2]
    day = next(cell["date"] for cell in week if any(not i.get("test") for i in cell["items"]))
    count = sum(len([i for i in cell["items"] if not i.get("test")]) for cell in week)

    session.add(InstagramPlanItem(workspace_id=workspace.id, day=_dt(day), kind="bloqueado"))
    session.flush()
    after = build_calendar(session, workspace.id, month, TODAY, [])
    cells = _cells(after)
    assert cells[day]["blocked"] and not cells[day]["items"]
    week_after = after["weeks"][2]
    assert sum(len([i for i in c["items"] if not i.get("test")]) for c in week_after) == count


def test_removed_recommendation_is_not_replaced(session, workspace):
    _seed(session, workspace)
    month = date(2026, 12, 1)
    week = build_calendar(session, workspace.id, month, TODAY, [])["weeks"][2]
    origin, item = next(
        (cell["date"], i) for cell in week for i in cell["items"] if not i.get("test")
    )
    count = sum(len([i for i in c["items"] if not i.get("test")]) for c in week)
    session.add(
        InstagramPlanItem(
            workspace_id=workspace.id, day=_dt(origin), kind=item["kind"],
            origin_day=_dt(origin), removed=True,
        )
    )  # fmt: skip
    session.flush()
    week = build_calendar(session, workspace.id, month, TODAY, [])["weeks"][2]
    assert sum(len([i for i in c["items"] if not i.get("test")]) for c in week) == count - 1


def test_changing_format_rebalances_the_rest_of_the_month(session, workspace):
    _seed(session, workspace)
    month = date(2026, 12, 1)
    before = build_calendar(session, workspace.id, month, TODAY, [])
    kinds_before = sorted(
        i["kind"] for row in before["weeks"] for c in row for i in c["items"] if not i.get("test")
    )
    origin, item = next(
        (c["date"], i) for c in before["weeks"][1] for i in c["items"] if not i.get("test")
    )
    other = "imagen" if item["kind"] == "reel" else "reel"
    session.add(
        InstagramPlanItem(
            workspace_id=workspace.id, day=_dt(origin), hour=12, kind=other,
            origin_day=_dt(origin),
        )
    )  # fmt: skip
    session.flush()
    after = build_calendar(session, workspace.id, month, TODAY, [])
    kinds_after = sorted(
        i["kind"] for row in after["weeks"] for c in row for i in c["items"] if not i.get("test")
    )
    # Mismo total y misma proporción de formatos en el mes: el resto se reacomodó.
    assert kinds_after == kinds_before


def test_full_blocked_week_moves_posts_to_other_weeks(session, workspace):
    _seed(session, workspace)
    month = date(2026, 12, 1)
    before = build_calendar(session, workspace.id, month, TODAY, [])
    total = sum(len(c["items"]) for row in before["weeks"] for c in row if not c["blocked"])
    for c in before["weeks"][2]:
        session.add(
            InstagramPlanItem(workspace_id=workspace.id, day=_dt(c["date"]), kind="bloqueado")
        )
    session.flush()
    after = build_calendar(session, workspace.id, month, TODAY, [])
    assert not any(c["items"] for c in after["weeks"][2])
    total_after = sum(len(c["items"]) for row in after["weeks"] for c in row)
    assert total_after == total


def test_posts_are_not_glued_to_a_fixed_post_in_the_next_week(session, workspace):
    _seed(session, workspace)
    month = date(2026, 12, 1)
    week = build_calendar(session, workspace.id, month, TODAY, [])["weeks"][2]
    monday = week[0]["date"]
    session.add(InstagramPlanItem(workspace_id=workspace.id, day=_dt(monday), hour=18, kind="reel"))
    session.flush()
    after = build_calendar(session, workspace.id, month, TODAY, [])
    sunday_before = after["weeks"][1][6]
    tuesday = after["weeks"][2][1]
    glued = [i for c in (sunday_before, tuesday) for i in c["items"] if not i.get("test")]
    free_days = [c for row in after["weeks"] for c in row if c["in_month"] and not c["items"]]
    assert not glued or len(free_days) < 3


def _feed_items(c):
    return sorted(
        (cell["date"], i["kind"])
        for row in c["weeks"]
        for cell in row
        for i in cell["items"]
        if cell["in_month"]
    )


def test_no_posts_on_consecutive_days_and_no_reel_after_reel(session, workspace):
    _seed(session, workspace)
    for month in (date(2026, 12, 1), date(2027, 1, 1)):
        items = _feed_items(build_calendar(session, workspace.id, month, TODAY, []))
        assert items
        for (d1, k1), (d2, k2) in zip(items, items[1:], strict=False):
            assert (d2 - d1).days > 1, (d1, d2)
            assert not (k1 == "reel" and k2 == "reel"), (d1, d2)


def test_rules_respect_fixed_reel_and_suggest_exceptions(session, workspace):
    _seed(session, workspace)
    month = date(2026, 12, 1)
    base = build_calendar(session, workspace.id, month, TODAY, [])
    day = base["weeks"][2][2]["date"]
    session.add(InstagramPlanItem(workspace_id=workspace.id, day=_dt(day), hour=18, kind="reel"))
    session.flush()
    c = build_calendar(session, workspace.id, month, TODAY, [])
    items = _feed_items(c)
    assert (day, "reel") in items  # lo fijado se respeta
    for (d1, k1), (d2, k2) in zip(items, items[1:], strict=False):
        assert (d2 - d1).days > 1
        assert not (k1 == "reel" and k2 == "reel")
    assert isinstance(c["suggestions"], list)


def test_warns_when_fixed_items_break_the_rules(session, workspace):
    _seed(session, workspace)
    month = date(2026, 12, 1)
    for day in (date(2026, 12, 26), date(2026, 12, 31)):
        session.add(
            InstagramPlanItem(workspace_id=workspace.id, day=_dt(day), hour=18, kind="reel")
        )
    session.flush()
    c = build_calendar(session, workspace.id, month, TODAY, [])
    items = _feed_items(c)
    between = [k for d, k in items if date(2026, 12, 26) < d < date(2026, 12, 31)]
    if not between:
        assert any("26/12" in s["text"] and "reels seguidos" in s["text"] for s in c["suggestions"])


def test_story_slots_need_enough_stories(session, workspace):
    from cmr_leads.instagram.calendar import stories_plan

    stories = []
    for i in range(30):  # muchas historias parejas a la tarde (18 h AR = 21 UTC)
        stories.append(
            InstagramPost(
                workspace_id=workspace.id, media_id=f"s{i}", product_type="STORY",
                published_at=datetime(2026, 10, 1) + timedelta(days=i, hours=21), views=40,
            )
        )  # fmt: skip
    for i in range(2):  # dos historias a las 7 h AR que explotaron
        stories.append(
            InstagramPost(
                workspace_id=workspace.id, media_id=f"m{i}", product_type="STORY",
                published_at=datetime(2026, 10, 1) + timedelta(days=i, hours=10), views=900,
            )
        )  # fmt: skip
    plan = stories_plan(stories, Stats([]), 30)
    assert plan["source"] == "historias"
    assert "6 a 9 h" not in plan["slots"]


def test_story_plan_gives_exact_hours(session, workspace):
    from cmr_leads.instagram.calendar import stories_plan

    stories = []
    for i in range(20):  # a las 10 AR (13 UTC) rinden más que a las 11 AR
        for hour, views in ((13, 60), (14, 30), (21, 40)):
            stories.append(
                InstagramPost(
                    workspace_id=workspace.id, media_id=f"s{i}-{hour}", product_type="STORY",
                    published_at=datetime(2026, 10, 1) + timedelta(days=i, hours=hour),
                    views=views,
                )
            )  # fmt: skip
    plan = stories_plan(stories, Stats([]), 20)
    times = {t["slot"]: t["time"] for t in plan["times"]}
    assert times["9 a 12 h"] == "10:00"
    assert times["18 a 21 h"] == "18:00"
    assert plan["time_labels"] == [t["time"] for t in plan["times"]]

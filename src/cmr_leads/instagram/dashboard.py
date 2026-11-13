"""Números de Instagram para un período y un formato: totales, series, links, mejor día y horario."""

from collections import defaultdict
from datetime import date, datetime, timedelta
from statistics import mean

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cmr_leads.db.models import InstagramDaily, InstagramPost
from cmr_leads.instagram.stats import to_local

WEEKDAYS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
SLOTS = (
    (0, 6, "0 a 6 h"),
    (6, 9, "6 a 9 h"),
    (9, 12, "9 a 12 h"),
    (12, 15, "12 a 15 h"),
    (15, 18, "15 a 18 h"),
    (18, 21, "18 a 21 h"),
    (21, 24, "21 a 24 h"),
)
FORMATS = {
    "reel": "Reel / video",
    "imagen": "Imagen",
    "carrusel": "Carrusel",
    "historia": "Historia",
}
BUTTONS = {
    "BIO_LINK": "Link de la bio",
    "CALL": "Llamar",
    "TEXT": "Mensaje de texto",
    "EMAIL": "Mail",
    "DIRECTION": "Cómo llegar",
    "BOOK_NOW": "Reservar",
    "INSTANT_EXPERIENCE": "Experiencia instantánea",
    "OTRO": "Otros botones",
}


def slot_of(hour: int) -> str:
    return next(label for start, end, label in SLOTS if start <= hour < end)


def _day(value: date) -> datetime:
    return datetime(value.year, value.month, value.day)


def _sum(rows, attr) -> int | None:
    values = [getattr(r, attr) for r in rows if getattr(r, attr) is not None]
    return sum(values) if values else None


def account_totals(rows: list[InstagramDaily]) -> dict:
    reach = [r.reach for r in rows if r.reach is not None]
    return {
        "views": _sum(rows, "views"),
        "reach_avg": round(mean(reach)) if reach else None,
        "profile_views": _sum(rows, "profile_views"),
        "website_clicks": _sum(rows, "website_clicks"),
        "link_taps": _sum(rows, "link_taps"),
        "interactions": _sum(rows, "total_interactions"),
        "days": len(rows),
    }


def daily_rows(session: Session, workspace_id: int, since: date, until: date) -> list:
    return list(
        session.scalars(
            select(InstagramDaily)
            .where(
                InstagramDaily.workspace_id == workspace_id,
                InstagramDaily.day >= _day(since),
                InstagramDaily.day <= _day(until),
            )
            .order_by(InstagramDaily.day)
        )
    )


def series(rows: list[InstagramDaily], monthly: bool) -> list[dict]:
    buckets: dict[date, dict] = {}
    for r in rows:
        key = r.day.date().replace(day=1) if monthly else r.day.date()
        b = buckets.setdefault(
            key, {"day": key, "views": 0, "reach": 0, "profile_views": 0, "clicks": 0}
        )
        b["views"] += r.views or 0
        b["reach"] += r.reach or 0
        b["profile_views"] += r.profile_views or 0
        b["clicks"] += (r.website_clicks or 0) + (r.link_taps or 0)
    return list(buckets.values())


def links(rows: list[InstagramDaily]) -> list[dict]:
    """A qué links o botones del perfil entra la gente (Meta da cantidades, no quién)."""
    totals: dict[str, int] = defaultdict(int)
    for r in rows:
        totals["BIO_LINK"] += r.website_clicks or 0
        for key, value in (r.link_breakdown or {}).items():
            totals[key if key in BUTTONS else "OTRO"] += int(value or 0)
    return sorted(
        ({"name": BUTTONS[k], "value": v} for k, v in totals.items() if v),
        key=lambda x: -x["value"],
    )


def media_in(session: Session, workspace_id: int, since: date, until: date) -> list[InstagramPost]:
    """Publicaciones e historias publicadas en el período (fechas en hora de Argentina)."""
    start = _day(since) + timedelta(hours=3)
    end = _day(until) + timedelta(days=1, hours=3)
    return list(
        session.scalars(
            select(InstagramPost)
            .where(
                InstagramPost.workspace_id == workspace_id,
                InstagramPost.published_at >= start,
                InstagramPost.published_at < end,
            )
            .order_by(InstagramPost.published_at.desc())
        )
    )


def format_key(post: InstagramPost) -> str:
    return {v: k for k, v in FORMATS.items()}[post.kind]


def _views(post: InstagramPost) -> int:
    return post.views if post.views is not None else (post.reach or 0)


def _avg(values: list[float]) -> float | None:
    return round(mean(values)) if values else None


def best_times(posts: list[InstagramPost]) -> dict:
    """Promedio de vistas por día de la semana y por franja horaria, con la cantidad de posts."""
    by_day: dict[int, list[int]] = defaultdict(list)
    by_slot: dict[str, list[int]] = defaultdict(list)
    grid: dict[tuple[int, str], list[int]] = defaultdict(list)
    for p in posts:
        if not p.published_at:
            continue
        local = to_local(p.published_at)
        slot = slot_of(local.hour)
        by_day[local.weekday()].append(_views(p))
        by_slot[slot].append(_views(p))
        grid[(local.weekday(), slot)].append(_views(p))
    days = [
        {"name": WEEKDAYS[i], "avg": _avg(by_day[i]), "count": len(by_day[i])} for i in range(7)
    ]
    slots = [
        {"name": label, "avg": _avg(by_slot[label]), "count": len(by_slot[label])}
        for _s, _e, label in SLOTS
    ]
    heat = [
        [{"avg": _avg(grid[(d, label)]), "count": len(grid[(d, label)])} for _s, _e, label in SLOTS]
        for d in range(7)
    ]
    cells = [c["avg"] for row in heat for c in row if c["avg"]]
    best_day = max((d for d in days if d["avg"]), key=lambda d: d["avg"], default=None)
    best_slot = max((s for s in slots if s["avg"]), key=lambda s: s["avg"], default=None)
    return {
        "days": days,
        "slots": slots,
        "heat": heat,
        "heat_max": max(cells, default=0),
        "best_day": best_day,
        "best_slot": best_slot,
    }


def by_format(posts: list[InstagramPost]) -> list[dict]:
    groups: dict[str, list[InstagramPost]] = defaultdict(list)
    for p in posts:
        groups[format_key(p)].append(p)
    rows = []
    for key, label in FORMATS.items():
        items = groups.get(key, [])
        if not items:
            continue
        rows.append(
            {
                "key": key,
                "name": label,
                "count": len(items),
                "views": sum(_views(p) for p in items),
                "avg_views": _avg([_views(p) for p in items]),
                "avg_reach": _avg([p.reach for p in items if p.reach is not None]),
                "avg_interactions": _avg([p.interactions for p in items]),
                "profile_visits": sum(p.profile_visits or 0 for p in items),
                "times": best_times(items),
            }
        )
    return sorted(rows, key=lambda r: -(r["avg_views"] or 0))


def stories_summary(stories: list[InstagramPost], since: date, until: date) -> dict:
    per_day: dict[date, dict] = {}
    current = since
    while current <= until:
        per_day[current] = {"day": current, "count": 0, "views": 0}
        current += timedelta(days=1)
    for s in stories:
        if not s.published_at:
            continue
        key = to_local(s.published_at).date()
        if key in per_day:
            per_day[key]["count"] += 1
            per_day[key]["views"] += _views(s)
    days = list(per_day.values())
    # Por día de la semana: cuántas historias se suben en promedio y cuántas vistas hace cada una.
    weekday_days: dict[int, list[dict]] = defaultdict(list)
    for d in days:
        weekday_days[d["day"].weekday()].append(d)
    weekdays = []
    for i in range(7):
        items = weekday_days.get(i, [])
        stories_count = sum(d["count"] for d in items)
        views = sum(d["views"] for d in items)
        weekdays.append(
            {
                "name": WEEKDAYS[i],
                "avg_count": round(stories_count / len(items), 1) if items else None,
                "avg_views": round(views / stories_count) if stories_count else None,
                "count": stories_count,
            }
        )
    total = len(stories)
    with_data = [d for d in days if d["count"]]
    return {
        "total": total,
        "views": sum(_views(s) for s in stories),
        "avg_views": _avg([_views(s) for s in stories]),
        "per_day_avg": round(total / len(days), 1) if days else 0,
        "days_with_stories": len(with_data),
        "days": days,
        "weekdays": weekdays,
        "times": best_times(stories),
        "replies": sum(s.replies or 0 for s in stories),
        "profile_visits": sum(s.profile_visits or 0 for s in stories),
        "since": since,
    }


def stories_tracked_since(session: Session, workspace_id: int) -> date | None:
    """Primer día con historias guardadas: Meta no deja leer historias de días anteriores."""
    first = session.scalar(
        select(func.min(InstagramPost.published_at)).where(
            InstagramPost.workspace_id == workspace_id, InstagramPost.product_type == "STORY"
        )
    )
    return to_local(first).date() if first else None


def build_dashboard(
    session: Session, workspace_id: int, since: date, until: date, formato: str = ""
) -> dict:
    length = (until - since).days + 1
    prev_until = since - timedelta(days=1)
    prev_since = prev_until - timedelta(days=length - 1)
    rows = daily_rows(session, workspace_id, since, until)
    previous = account_totals(daily_rows(session, workspace_id, prev_since, prev_until))
    media = media_in(session, workspace_id, since, until)
    posts = [p for p in media if not p.is_story]
    stories = [p for p in media if p.is_story]
    selected = [p for p in media if format_key(p) == formato] if formato in FORMATS else posts
    monthly = length > 62
    totals = account_totals(rows)
    tracked = stories_tracked_since(session, workspace_id) or until
    return {
        "since": since,
        "until": until,
        "monthly": monthly,
        "totals": totals,
        "previous": previous,
        "series": series(rows, monthly),
        "links": links(rows),
        "formats": by_format(media),
        "times": best_times(selected),
        "stories": stories_summary(stories, max(since, tracked), until),
        "stories_tracked_since": tracked,
        "posts": sorted(selected, key=_views, reverse=True),
        "post_count": len(posts),
        "formato": formato if formato in FORMATS else "",
    }

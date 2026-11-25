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
        "follows": _sum(rows, "follows"),
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
            key,
            {
                "day": key,
                "views": 0,
                "reach": 0,
                "profile_views": 0,
                "clicks": 0,
                "interactions": 0,
                "follows": 0,
            },
        )
        b["follows"] += r.follows or 0
        b["interactions"] += r.total_interactions or 0
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
    previous_rows = daily_rows(session, workspace_id, prev_since, prev_until)
    previous = account_totals(previous_rows)
    media = media_in(session, workspace_id, since, until)
    posts = [p for p in media if not p.is_story]
    stories = [p for p in media if p.is_story]
    selected = [p for p in media if format_key(p) == formato] if formato in FORMATS else posts
    monthly = length > 62
    totals = account_totals(rows)
    tracked = stories_tracked_since(session, workspace_id) or until
    result = {
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
        "followers": followers_summary(session, workspace_id, rows, previous_rows, media),
        "follower_history": follower_history(session, workspace_id),
        "posts": sorted(selected, key=_views, reverse=True),
        "post_count": len(posts),
        "formato": formato if formato in FORMATS else "",
    }
    history = result["follower_history"]
    for b in result["series"]:
        if monthly:
            last = min((b["day"] + timedelta(days=32)).replace(day=1) - timedelta(days=1), until)
        else:
            last = b["day"]
        b["followers_total"] = value_on(history, last)
        if monthly:
            # Por mes, "ganados" es la diferencia entre totales: Meta informa los seguidores
            # diarios con días de atraso (mientras tanto da 0) y no resta los que dejan de seguir.
            before = value_on(history, max(b["day"], since) - timedelta(days=1))
            if before is not None and b["followers_total"] is not None:
                b["follows"] = b["followers_total"] - before
            elif b["followers_total"] is not None:  # primer mes: no puede ganar más que el total
                b["follows"] = min(b["follows"], b["followers_total"])
    fl = result["followers"]
    start_total = value_on(history, since - timedelta(days=1))
    end_total = value_on(history, until)
    prev_start = value_on(history, prev_since - timedelta(days=1))
    if start_total is not None and end_total is not None:
        fl["gained"] = end_total - start_total
        if prev_start is not None:
            fl["previous"] = start_total - prev_start
    elif (
        end_total is not None and fl["gained"] is not None
    ):  # el período arranca antes de los datos
        fl["gained"] = min(fl["gained"], end_total)
    gains_by_month: dict[date, int] = defaultdict(int)
    for r in session.scalars(
        select(InstagramDaily).where(
            InstagramDaily.workspace_id == workspace_id, InstagramDaily.follows.is_not(None)
        )
    ):
        gains_by_month[r.day.date().replace(day=1)] += r.follows
    result["followers"]["months"] = follower_months(history, gains_by_month, until)
    result["followers"]["real_since"] = min(history["real"]) if history["real"] else None
    return result


def followers_summary(
    session: Session, workspace_id: int, rows: list, previous_rows: list, media: list
) -> dict:
    """De dónde salen los seguidores: cuántos se ganan, qué publicaciones los traen y
    qué formato convierte mejor (seguidores cada 1.000 vistas)."""
    from cmr_leads.db.models import InstagramAccountSnapshot

    snapshot = session.scalar(
        select(InstagramAccountSnapshot)
        .where(
            InstagramAccountSnapshot.workspace_id == workspace_id,
            InstagramAccountSnapshot.followers.is_not(None),
        )
        .order_by(InstagramAccountSnapshot.day.desc())
    )
    gained = _sum(rows, "follows")
    profile = _sum(rows, "profile_views")
    measured = [r for r in rows if r.follows is not None]
    best_day = max(measured, key=lambda r: r.follows, default=None)
    top = sorted((p for p in media if (p.follows or 0) > 0), key=lambda p: -(p.follows or 0))
    formats = []
    for key, name in FORMATS.items():
        items = [p for p in media if format_key(p) == key and p.follows is not None]
        if not items:
            continue
        follows = sum(p.follows for p in items)
        views = sum(_views(p) for p in items)
        formats.append(
            {
                "key": key,
                "name": name,
                "count": len(items),
                "follows": follows,
                "per_1000": round(follows * 1000 / views, 1) if views else None,
            }
        )
    formats.sort(key=lambda f: -(f["per_1000"] or 0))
    return {
        "current": snapshot.followers if snapshot else None,
        "current_day": snapshot.day.date() if snapshot else None,
        "gained": gained,
        "previous": _sum(previous_rows, "follows"),
        "days_measured": len(measured),
        "per_100_visits": round(gained * 100 / profile, 1)
        if gained is not None and profile
        else None,
        "best_day": best_day.day.date() if best_day and best_day.follows else None,
        "best_day_follows": best_day.follows if best_day else None,
        "top": top[:5],
        "from_posts": sum(p.follows or 0 for p in media),
        "formats": formats,
    }


MONTH_NAMES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)  # fmt: skip


def follower_history(session: Session, workspace_id: int) -> dict:
    """Seguidores totales por día: real donde la app lo guardó y aproximado hacia atrás.

    Meta no da el total de seguidores de días pasados, solo cuántos empezaron a seguir cada
    día (sin restar los que dejaron). Se reconstruye así:
    - Se toma el último total real guardado que tenga datos diarios hasta ese día (ancla).
    - Si lo ganado hasta el ancla supera el total (hubo gente que dejó de seguir), se asume que
      la cuenta arrancó en 0 y se reparte esa diferencia en proporción a lo ganado.
    - Si no (faltan datos viejos), se resta hacia atrás lo ganado desde el total del ancla.
    Devuelve {"days": {fecha: total}, "real": {fecha: total}}.
    """
    from cmr_leads.db.models import InstagramAccountSnapshot

    snapshots = {
        sn.day.date(): sn.followers
        for sn in session.scalars(
            select(InstagramAccountSnapshot)
            .where(
                InstagramAccountSnapshot.workspace_id == workspace_id,
                InstagramAccountSnapshot.followers.is_not(None),
            )
            .order_by(InstagramAccountSnapshot.day)
        )
    }
    gains = {
        r.day.date(): r.follows
        for r in session.scalars(
            select(InstagramDaily).where(
                InstagramDaily.workspace_id == workspace_id, InstagramDaily.follows.is_not(None)
            )
        )
    }
    if not snapshots or not gains:
        return {"days": dict(snapshots), "real": dict(snapshots)}
    first, last_gain = min(gains), max(gains)
    anchors = [d for d in snapshots if d <= last_gain] or [min(snapshots)]
    anchor = max(anchors)
    end = max(max(snapshots), last_gain)
    cum, running, day = {}, 0, first
    while day <= end:
        running += gains.get(day, 0)
        cum[day] = running
        day += timedelta(days=1)
    total_at_anchor = cum.get(anchor, running)
    followers = snapshots[anchor]
    days = {}
    for d, c in cum.items():
        if d <= anchor:
            if total_at_anchor >= followers and total_at_anchor:
                days[d] = round(c * followers / total_at_anchor)
            else:
                days[d] = followers - (total_at_anchor - c)
        else:
            days[d] = followers + (c - total_at_anchor)
    days.update(snapshots)  # donde hay dato real, manda el real
    return {"days": days, "real": dict(snapshots)}


def value_on(history: dict, day: date) -> int | None:
    """El total del último día con dato hasta `day` (incluido)."""
    known = [d for d in history["days"] if d <= day]
    return history["days"][max(known)] if known else None


def follower_months(history: dict, gains_by_month: dict, today: date) -> list[dict]:
    """Una fila por mes: seguidores al cierre (o hoy, en el mes actual) y ganados."""
    if not history["days"]:
        return []
    start = min(history["days"]).replace(day=1)
    rows, month = [], start
    while month <= today.replace(day=1):
        nxt = (month + timedelta(days=32)).replace(day=1)
        last = min(nxt - timedelta(days=1), today)
        total = value_on(history, last)
        real_days = [d for d in history["real"] if month <= d <= last]
        is_real = bool(real_days) and max(real_days) == max(d for d in history["days"] if d <= last)
        before = value_on(history, month - timedelta(days=1))
        # Ganados = diferencia con el cierre del mes anterior (lo que de verdad subió la cuenta).
        # El primer mes no tiene cierre anterior: se usa lo que informa Meta, sin pasar del total.
        gained = total - before if total is not None and before is not None else None
        if gained is None and month in gains_by_month:
            gained = gains_by_month[month] if total is None else min(gains_by_month[month], total)
        rows.append(
            {
                "month": month,
                "name": f"{MONTH_NAMES[month.month - 1].capitalize()} {month.year}",
                "total": total,
                "gained": gained,
                "current": month == today.replace(day=1),
                "real": is_real,
            }
        )
        month = nxt
    return rows

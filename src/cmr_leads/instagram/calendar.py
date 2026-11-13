"""Calendario del mes: qué subir cada día (reel, carrusel, imagen, historias) y a qué hora.

Se arma de nuevo cada vez que se abre, con las estadísticas del momento: cuanto más se publica,
más datos hay y mejor queda.

Cómo decide:
- Cuántas publicaciones por semana: las que se vienen subiendo (últimas 8 semanas), entre 2 y 5.
- Qué formato: en proporción a cuánta gente alcanza cada formato sin publicidad.
- Qué día y a qué hora: el promedio de alcance de cada día y franja, "suavizado" hacia el
  promedio general cuando hay pocos datos (con 1 sola publicación un día no puede ser "el mejor").
- Una publicación por mes va a un día que casi no se probó, para aprender si funciona.
- Las publicaciones con publicidad no cuentan: su alcance incluye lo pagado.
- Historias: cuántas por día y en qué horario, con los datos que se pudieron guardar.
"""

import calendar as cal
from collections import defaultdict
from datetime import date, datetime, timedelta
from math import expm1, log1p, sqrt
from statistics import mean, median, pstdev

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from cmr_leads.db.models import InstagramPlanItem, InstagramPost
from cmr_leads.instagram.dashboard import SLOTS, WEEKDAYS, slot_of, stories_tracked_since
from cmr_leads.instagram.promote import match_post
from cmr_leads.instagram.stats import to_local

LOOKBACK_DAYS = 365
RECENT_WEEKS = 8
SMOOTH = 3.0  # cuánto pesa el promedio general frente a pocos datos
MIN_PER_WEEK, MAX_PER_WEEK = 2, 5
FEED_KINDS = {"Reel / video": "reel", "Carrusel": "carrusel", "Imagen": "imagen"}
PLURAL = {"reel": "reels", "carrusel": "carruseles", "imagen": "imágenes"}
SHORT = {"reel": "Reel", "carrusel": "Carrusel", "imagen": "Imagen", "historia": "Historias"}
MONTHS = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]  # fmt: skip


def _views(p: InstagramPost) -> int:
    return p.views if p.views is not None else (p.reach or 0)


def _confidence(n: int) -> str:
    return "alta" if n >= 8 else ("media" if n >= 3 else "baja")


def _log(p: InstagramPost) -> float:
    # El alcance es muy desparejo (una publicación puede llegar a 10 veces más que otra):
    # se promedia en escala logarítmica para que un solo caso no defina el día o la hora.
    return log1p(_views(p))


def _smoothed(values: list[float], prior: float) -> float:
    return (sum(values) + SMOOTH * prior) / (len(values) + SMOOTH)


class Stats:
    """Promedios suavizados (en escala logarítmica) por formato, día y franja horaria.

    Para ELEGIR días y horarios se usa una estimación prudente (el promedio menos su margen de
    error): un día con 1 sola publicación buena no le gana a uno con 10 publicaciones buenas.
    Los días poco probados se prueban aparte, con la prueba del mes.
    """

    def __init__(self, posts: list[InstagramPost]):
        self.posts = posts
        raw = {p.id or id(p): log1p(_views(p)) for p in posts}
        self.base = mean(raw.values()) if raw else 0.0
        self.sd = pstdev(raw.values()) if len(raw) > 1 else 1.0
        # Los picos extremos se recortan para que una publicación viral no mande en todo.
        top = self.base + 1.5 * self.sd
        self.logs = {k: min(v, top) for k, v in raw.items()}
        self.overall = expm1(self.base)
        self.by_kind: dict[str, list[InstagramPost]] = defaultdict(list)
        for p in posts:
            self.by_kind[FEED_KINDS[p.kind]].append(p)
        day_all: dict[int, list[float]] = defaultdict(list)
        self.slot_all: dict[str, list[float]] = defaultdict(list)
        for p in posts:
            local = to_local(p.published_at)
            day_all[local.weekday()].append(self.log(p))
            self.slot_all[slot_of(local.hour)].append(self.log(p))
        # Efecto general del día y de la franja (todos los formatos juntos), a media fuerza.
        self.day_effect = {d: (_smoothed(day_all[d], self.base) - self.base) / 2 for d in range(7)}
        self.slot_effect = {
            s: (_smoothed(self.slot_all[s], self.base) - self.base) / 2 for *_x, s in SLOTS
        }

    def log(self, p: InstagramPost) -> float:
        return self.logs[p.id or id(p)]

    def _kind_log(self, kind: str) -> float:
        items = self.by_kind.get(kind, [])
        return _smoothed([self.log(p) for p in items], self.base) if items else self.base

    def kind_mean(self, kind: str) -> float:
        return expm1(self._kind_log(kind))

    def _cell(self, kind: str, match, effect: float) -> tuple[float, int]:
        items = [self.log(p) for p in self.by_kind.get(kind, []) if match(to_local(p.published_at))]
        return _smoothed(items, self._kind_log(kind) + effect), len(items)

    def _rank(self, value: float, n: int) -> float:
        return value - self.sd / sqrt(n + SMOOTH)

    def day_score(self, kind: str, weekday: int) -> tuple[float, int]:
        value, n = self._cell(kind, lambda t: t.weekday() == weekday, self.day_effect[weekday])
        return expm1(value), n

    def day_rank(self, kind: str, weekday: int) -> float:
        value, n = self._cell(kind, lambda t: t.weekday() == weekday, self.day_effect[weekday])
        return self._rank(value, n)

    def slot_score(self, kind: str, slot: str) -> tuple[float, int]:
        value, n = self._cell(kind, lambda t: slot_of(t.hour) == slot, self.slot_effect[slot])
        return expm1(value), n

    def slot_rank(self, kind: str, slot: str) -> float:
        value, n = self._cell(kind, lambda t: slot_of(t.hour) == slot, self.slot_effect[slot])
        return self._rank(value, n)

    def tried_slots(self, minimum: int = 1) -> list[str]:
        """Franjas desde las 6 h en las que ya se publicó (no se recomiendan horarios a ciegas)."""
        slots = [s for start, _e, s in SLOTS if start >= 6 and len(self.slot_all[s]) >= minimum]
        return slots or [s for start, _e, s in SLOTS if start >= 6]

    def best_slot(self, kind: str) -> tuple[str, int]:
        slot = max(self.tried_slots(), key=lambda s: self.slot_rank(kind, s))
        return slot, self.slot_score(kind, slot)[1]

    def hour_for(self, kind: str, slot: str) -> int:
        """La hora típica en la que se subió ese formato en esa franja (o el medio de la franja)."""
        hours = [
            to_local(p.published_at).hour
            for p in self.by_kind.get(kind, []) or self.posts
            if slot_of(to_local(p.published_at).hour) == slot
        ]
        if hours:
            return int(median(hours))
        start, end = next((a, b) for a, b, s in SLOTS if s == slot)
        return (start + end) // 2


def _feed_posts(session: Session, workspace_id: int, today: date, ad_names: list[str]):
    since = datetime.combine(today - timedelta(days=LOOKBACK_DAYS), datetime.min.time())
    posts = list(
        session.scalars(
            select(InstagramPost).where(
                InstagramPost.workspace_id == workspace_id,
                InstagramPost.published_at >= since,
            )
        )
    )
    feed = [p for p in posts if not p.is_story and p.published_at]
    promoted = {m.id for name in ad_names if (m := match_post(name, feed))}
    stories = [p for p in posts if p.is_story and p.published_at]
    return [p for p in feed if p.id not in promoted], promoted, stories


def weekly_mix(stats: Stats, per_week: int) -> list[str]:
    """Qué formatos subir en la semana, en proporción a lo que alcanza cada uno."""
    kinds = [k for k in FEED_KINDS.values() if len(stats.by_kind.get(k, [])) >= 2]
    if not kinds:
        return ["reel"] * per_week
    weights = {k: stats.kind_mean(k) for k in kinds}
    total = sum(weights.values())
    raw = {k: per_week * w / total for k, w in weights.items()}
    counts = {k: int(v) for k, v in raw.items()}
    for k in sorted(raw, key=lambda k: raw[k] - counts[k], reverse=True):
        if sum(counts.values()) >= per_week:
            break
        counts[k] += 1
    best = max(weights, key=weights.get)
    if not counts[best]:
        counts[best] = 1
        worst = min((k for k in counts if counts[k]), key=weights.get)
        counts[worst] -= 1
    order = sorted(counts, key=weights.get, reverse=True)
    return [k for k in order for _ in range(counts[k])]


def month_sequence(stats: Stats, per_week: int, weeks: int) -> list[list[str]]:
    """Reparte los formatos en todo el mes y no solo en una semana.

    Con 2 publicaciones por semana, una semana sola deja afuera al tercer formato; en el mes
    entra en proporción a lo que alcanza (intercalado, para que no se amontone).
    """
    counts: dict[str, int] = defaultdict(int)
    for kind in weekly_mix(stats, per_week * weeks):
        counts[kind] += 1
    total = sum(counts.values())
    current = dict.fromkeys(counts, 0)
    sequence = []
    for _ in range(total):  # reparto ponderado intercalado
        for k in current:
            current[k] += counts[k]
        pick = max(current, key=lambda k: (current[k], counts[k]))
        current[pick] -= total
        sequence.append(pick)
    return [sequence[i * per_week : (i + 1) * per_week] for i in range(weeks)]


def plan_week(
    stats: Stats, mix: list[str], blocked: set[int] = frozenset(), fixed: set[int] = frozenset()
) -> list[dict]:
    """Elige el día de cada publicación de la semana, separándolas para no amontonar.

    `blocked` son días que no se pueden usar y `fixed` días que ya tienen algo fijado a mano:
    lo recomendado se reubica alrededor de eso.
    """
    used: set[int] = set(fixed)
    plan = []
    for kind in mix:
        options = []
        for d in range(7):
            if d in used or d in blocked:
                continue
            score, n = stats.day_score(kind, d)
            crowded = any(abs(d - u) == 1 for u in used)  # días pegados rinden menos
            options.append((stats.day_rank(kind, d) - (0.15 if crowded else 0.0), score, n, d))
        if not options:
            break
        _adj, score, n, day = max(options)
        used.add(day)
        slot, slot_n = stats.best_slot(kind)
        plan.append(
            {
                "kind": kind,
                "weekday": day,
                "slot": slot,
                "hour": stats.hour_for(kind, slot),
                "expected": round(score),
                "samples": n,
                "slot_samples": slot_n,
            }
        )
    return plan


def test_slot(stats: Stats, plan: list[dict], unavailable: set[int] = frozenset()) -> dict | None:
    """Una prueba por mes: el mejor formato en el día que menos se probó."""
    if not plan:
        return None
    kind = plan[0]["kind"]
    used = {p["weekday"] for p in plan} | set(unavailable)
    free = [d for d in range(7) if d not in used and d < 5] or [
        d for d in range(7) if d not in used
    ]
    if not free:
        return None
    day = min(free, key=lambda d: (stats.day_score(kind, d)[1], -stats.day_score(kind, d)[0]))
    slot, _n = stats.best_slot(kind)
    return {
        "kind": kind,
        "weekday": day,
        "slot": slot,
        "hour": stats.hour_for(kind, slot),
        "expected": round(stats.day_score(kind, day)[0]),
        "samples": stats.day_score(kind, day)[1],
        "test": True,
    }


def stories_plan(stories: list[InstagramPost], stats: Stats, tracked_days: int) -> dict:
    """Historias por día y horarios. Con pocos datos se apoya en lo que funciona en el feed."""
    per_day = len(stories) / tracked_days if tracked_days else 0
    count = min(max(round(per_day), 2), 6)
    by_slot: dict[str, list[int]] = defaultdict(list)
    for s in stories:
        by_slot[slot_of(to_local(s.published_at).hour)].append(_log(s))
    candidates = [s for start, _e, s in SLOTS if 6 <= start < 24]
    if len(stories) >= 10:
        avg = mean(_log(s) for s in stories)
        ranked = sorted(candidates, key=lambda s: _smoothed(by_slot[s], avg), reverse=True)
        source = "historias"
    else:
        candidates = stats.tried_slots(minimum=2)
        ranked = sorted(candidates, key=lambda s: stats.slot_effect[s], reverse=True)
        source = "feed"
    slots = ranked[: min(3, count)]
    slots.sort(key=lambda s: next(a for a, _b, x in SLOTS if x == s))
    return {
        "count": count,
        "slots": slots,
        "current_per_day": round(per_day, 1),
        "samples": len(stories),
        "tracked_days": tracked_days,
        "source": source,
        "confidence": _confidence(len(stories) // 3),
    }


def _why(item: dict, stats: Stats) -> str:
    name = PLURAL[item["kind"]]
    if item.get("test"):
        return (
            f"Prueba: el {WEEKDAYS[item['weekday']].lower()} casi no se probó con {name} "
            f"({item['samples']} publicaciones). Sirve para aprender si ese día funciona."
        )
    vs = item["expected"] / stats.overall if stats.overall else 1
    text = (
        f"{name.capitalize()} del {WEEKDAYS[item['weekday']].lower()}: unas {item['expected']} vistas "
        f"cada una ({vs:.1f} veces el promedio de la cuenta)".replace(".", ",", 1)
    )
    text += f"; la franja de {item['slot']} es la que mejor le va a este formato."
    if item["samples"] < 3:
        text += " Hay pocos datos de este día: se va a ajustar a medida que publiques."
    return text


def _interleave(counts: dict[str, int]) -> list[str]:
    """Intercala los formatos (reparto ponderado) para que no se amontone uno solo."""
    counts = {k: v for k, v in counts.items() if v > 0}
    total = sum(counts.values())
    current = dict.fromkeys(counts, 0)
    sequence = []
    for _ in range(total):
        for k in current:
            current[k] += counts[k]
        pick = max(current, key=lambda k: (current[k], counts[k]))
        current[pick] -= total
        sequence.append(pick)
    return sequence


def plan_days(
    stats: Stats, kinds: list[str], open_days: list[date], taken: set[date]
) -> list[dict]:
    """Pone cada formato en el mejor día libre, lejos de lo que ya hay (también de otras semanas)."""
    plan = []
    for kind in kinds:
        options = []
        for d in open_days:
            if d in taken:
                continue
            score, n = stats.day_score(kind, d.weekday())
            near = (d - timedelta(days=1)) in taken or (d + timedelta(days=1)) in taken
            rank = stats.day_rank(kind, d.weekday()) - (0.15 if near else 0.0)
            options.append((rank, score, n, d))
        if not options:
            break
        _rank, score, n, day = max(options)
        taken.add(day)
        slot, slot_n = stats.best_slot(kind)
        plan.append(
            {
                "kind": kind,
                "date": day,
                "weekday": day.weekday(),
                "slot": slot,
                "hour": stats.hour_for(kind, slot),
                "expected": round(score),
                "samples": n,
                "slot_samples": slot_n,
            }
        )
    return plan


def plan_month(stats: Stats, month: date, month_weeks, per_week: int, changes: list, today: date):
    """Arma el mes a partir de lo fijado a mano y de lo que queda por delante.

    - Solo recomienda en días que todavía no pasaron.
    - Cada semana apunta a `per_week` (proporcional si la semana está cortada por el mes o por hoy).
    - Lo que se movió, editó o quitó deja de contar en la semana de donde salió.
    - El reparto de formatos es del MES completo contando lo que fijaste: si cambiaste un reel
      por un carrusel, otra recomendación vuelve a ser reel para mantener la proporción.
    - Si una semana no tiene lugar (días bloqueados o fijados), lo que sobra pasa a otra semana.
    - Todo queda separado de lo fijado, aunque esté en la semana de al lado.
    """
    in_month = [d for days in month_weeks for d in days if d.month == month.month]
    fixed = [c for c in changes if c.kind in SHORT and not c.removed and c.day.date() in in_month]
    blocked = {c.day.date() for c in changes if c.kind == BLOCKED}
    taken = {c.day.date() for c in fixed}

    open_by_week, base = [], []
    for days in month_weeks:
        ahead = [d for d in days if d.month == month.month and d >= today]
        open_by_week.append([d for d in ahead if d not in blocked and d not in taken])
        base.append(round(per_week * len(ahead) / 7))
    week_of = {d: w for w, days in enumerate(month_weeks) for d in days}
    for c in changes:
        if c.kind != BLOCKED and c.origin_day is not None:
            w = week_of.get(c.origin_day.date())
            if w is not None and c.origin_day.date() >= today:
                base[w] = max(0, base[w] - 1)

    auto_total = sum(base)
    counts: dict[str, int] = defaultdict(int)
    for kind in weekly_mix(stats, auto_total + len(fixed)) if auto_total else []:
        counts[kind] += 1
    for c in fixed:  # lo fijado ya ocupa su parte del reparto
        if counts.get(c.kind, 0) > 0:
            counts[c.kind] -= 1
        elif any(counts.values()):
            counts[max(counts, key=counts.get)] -= 1
    sequence = _interleave(counts)

    plans: list[list[dict]] = [[] for _ in month_weeks]
    carry: list[str] = []
    pos = 0
    for w, open_days in enumerate(open_by_week):
        kinds = carry + sequence[pos : pos + base[w]]
        pos += base[w]
        plans[w] = plan_days(stats, kinds, open_days, taken)
        carry = kinds[len(plans[w]) :]
    for w, open_days in enumerate(open_by_week):  # lo que no entró va donde haya lugar
        if not carry:
            break
        extra = plan_days(stats, carry, open_days, taken)
        plans[w] += extra
        carry = carry[len(extra) :]

    fixed_by_week = [[c for c in fixed if c.day.date() in set(days)] for days in month_weeks]

    # Una prueba por mes: el mejor formato en el día menos probado que quede libre.
    test = None
    best = weekly_mix(stats, 1)[0] if stats.posts else "reel"
    free = [
        d
        for days in open_by_week
        for d in days
        if d not in taken
        and (d - timedelta(days=1)) not in taken
        and (d + timedelta(days=1)) not in taken
    ]
    weekdays = [d for d in free if d.weekday() < 5] or free
    if weekdays:
        day = min(
            weekdays,
            key=lambda d: (
                stats.day_score(best, d.weekday())[1],
                -stats.day_score(best, d.weekday())[0],
                d,
            ),
        )
        slot, _n = stats.best_slot(best)
        score, n = stats.day_score(best, day.weekday())
        test = {
            "kind": best,
            "date": day,
            "weekday": day.weekday(),
            "slot": slot,
            "hour": stats.hour_for(best, slot),
            "expected": round(score),
            "samples": n,
            "test": True,
        }
    return plans, fixed_by_week, blocked, test


def build_calendar(
    session: Session, workspace_id: int, month: date, today: date, ad_names: list[str]
) -> dict:
    month = month.replace(day=1)
    feed, promoted, stories = _feed_posts(session, workspace_id, today, ad_names)
    stats = Stats(feed)

    recent_since = today - timedelta(weeks=RECENT_WEEKS)
    recent = [p for p in feed if to_local(p.published_at).date() >= recent_since]
    recent += [
        p
        for p in session.scalars(select(InstagramPost).where(InstagramPost.id.in_(promoted)))
        if to_local(p.published_at).date() >= recent_since
    ]
    current_per_week = len(recent) / RECENT_WEEKS
    per_week = min(max(round(current_per_week), MIN_PER_WEEK), MAX_PER_WEEK)
    month_weeks = cal.Calendar(firstweekday=0).monthdatescalendar(month.year, month.month)
    changes = load_changes(session, workspace_id, month_weeks[0][0], month_weeks[-1][-1])
    plans, fixed_by_week, blocked_days, test = plan_month(
        stats, month, month_weeks, per_week, changes, today
    )
    for item in [i for plan in plans for i in plan] + ([test] if test else []):
        item["why"] = _why(item, stats)
        item["label"] = SHORT[item["kind"]]
        item["time"] = f"{item['hour']:02d}:00"
        item["confidence"] = _confidence(item["samples"])

    tracked = stories_tracked_since(session, workspace_id)
    tracked_days = (today - tracked).days + 1 if tracked else 0
    st = stories_plan(stories, stats, tracked_days)

    # Ideas de contenido: las publicaciones sin publicidad que más alcanzaron, por formato.
    inspiration = {
        k: [
            ((p.caption or "").split("\n")[0][:60], p.permalink)
            for p in sorted(items, key=_views, reverse=True)[:3]
        ]
        for k, items in stats.by_kind.items()
    }

    # Lo que se subió de verdad en el mes (para comparar con el plan).
    actual: dict[date, list[dict]] = defaultdict(list)
    for p in (
        feed
        + stories
        + [p for p in session.scalars(select(InstagramPost).where(InstagramPost.id.in_(promoted)))]
    ):
        local = to_local(p.published_at)
        if (local.year, local.month) == (month.year, month.month):
            kind = "historia" if p.is_story else FEED_KINDS[p.kind]
            actual[local.date()].append(
                {"kind": kind, "time": f"{local:%H:%M}", "views": _views(p)}
            )

    weeks = []
    for w_index, days in enumerate(month_weeks):
        row = []
        for d in days:
            in_month = d.month == month.month
            items = []
            if in_month:
                items = [i for i in plans[w_index] if i["date"] == d]
                if test and test["date"] == d:
                    items = items + [test]
                items += [_fixed_item(c) for c in fixed_by_week[w_index] if c.day.date() == d]
                items.sort(key=lambda i: i["hour"])
            done = actual.get(d, [])
            feed_done = [a for a in done if a["kind"] != "historia"]
            row.append(
                {
                    "date": d,
                    "in_month": in_month,
                    "past": d < today,
                    "blocked": d in blocked_days,
                    "today": d == today,
                    "items": items,
                    "stories": st if in_month else None,
                    "done": done,
                    "stories_done": sum(1 for a in done if a["kind"] == "historia"),
                    "followed": bool(items)
                    and any(a["kind"] == i["kind"] for i in items for a in feed_done),
                }
            )
        weeks.append(row)

    totals = defaultdict(int)
    for row in weeks:
        for cell in row:
            for i in cell["items"]:
                totals[i["kind"]] += 1
    prev_month = (month - timedelta(days=1)).replace(day=1)
    next_month = (month + timedelta(days=32)).replace(day=1)
    return {
        "month": month,
        "title": f"{MONTHS[month.month - 1].capitalize()} {month.year}",
        "prev": prev_month,
        "next": next_month,
        "weeks": weeks,
        "week_plan": _pattern(weeks),
        "test": test,
        "stories": st,
        "per_week": per_week,
        "current_per_week": round(current_per_week, 1),
        "totals": {SHORT[k]: v for k, v in sorted(totals.items(), key=lambda x: -x[1])},
        "samples": len(feed),
        "promoted_excluded": len(promoted),
        "overall": round(stats.overall),
        "inspiration": inspiration,
        "weekdays": WEEKDAYS,
        "today": today,
        "changes": len(changes),
        "kinds": {k: v for k, v in SHORT.items() if k != "historia"},
    }


BLOCKED = "bloqueado"


def load_changes(session: Session, workspace_id: int, first: date, last: date) -> list:
    """Cambios hechos a mano que tocan las semanas que se muestran."""
    start = datetime.combine(first, datetime.min.time())
    end = datetime.combine(last + timedelta(days=1), datetime.min.time())
    item = InstagramPlanItem
    return list(
        session.scalars(
            select(item).where(
                item.workspace_id == workspace_id,
                or_(
                    and_(item.day >= start, item.day < end),
                    and_(item.origin_day >= start, item.origin_day < end),
                ),
            )
        )
    )


def _fixed_item(c) -> dict:
    hour = c.hour if c.hour is not None else 19
    return {
        "id": c.id,
        "kind": c.kind,
        "label": SHORT[c.kind],
        "hour": hour,
        "time": f"{hour:02d}:00",
        "date": c.day.date(),
        "fixed": True,
        "origin": c.origin_day.date() if c.origin_day else None,
        "why": "Lo fijaste vos. El resto del mes se acomoda alrededor.",
        "confidence": None,
    }


def _pattern(weeks: list[list[dict]]) -> list[dict]:
    """Lo que se repite en el mes (formato, día y hora) con cuántas veces toca."""
    seen: dict[tuple, dict] = {}
    for row in weeks:
        for cell in row:
            for i in cell["items"]:
                if i.get("test") or i.get("fixed"):
                    continue
                key = (i["kind"], i["weekday"], i["time"])
                seen.setdefault(key, {**i, "times": 0})["times"] += 1
    return sorted(seen.values(), key=lambda i: (i["weekday"], i["time"]))


def to_ics(c: dict, company: str) -> str:
    """El plan del mes en formato .ics para importarlo en Google Calendar."""

    def esc(text: str) -> str:
        return (
            text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
        )

    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:-//{company}//Calendario Instagram//ES"]
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    for row in c["weeks"]:
        for cell in row:
            if not cell["in_month"]:
                continue
            d = cell["date"]
            for n, item in enumerate(cell["items"]):
                start = datetime(d.year, d.month, d.day, item["hour"])
                end = start + timedelta(minutes=30)
                title = f"Instagram: subir {item['label'].lower()}" + (
                    " (prueba)" if item.get("test") else ""
                )
                lines += [
                    "BEGIN:VEVENT",
                    f"UID:cmr-ig-{d:%Y%m%d}-{n}@cmr-leads",
                    f"DTSTAMP:{stamp}",
                    f"DTSTART;TZID=America/Argentina/Buenos_Aires:{start:%Y%m%dT%H%M%S}",
                    f"DTEND;TZID=America/Argentina/Buenos_Aires:{end:%Y%m%dT%H%M%S}",
                    f"SUMMARY:{esc(title)}",
                    f"DESCRIPTION:{esc(item['why'])}",
                    "END:VEVENT",
                ]
            st = cell["stories"]
            if st:
                lines += [
                    "BEGIN:VEVENT",
                    f"UID:cmr-ig-{d:%Y%m%d}-historias@cmr-leads",
                    f"DTSTAMP:{stamp}",
                    f"DTSTART;VALUE=DATE:{d:%Y%m%d}",
                    "SUMMARY:" + esc(f"Instagram: {st['count']} historias"),
                    "DESCRIPTION:" + esc("Horarios: " + ", ".join(st["slots"])),
                    "END:VEVENT",
                ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"

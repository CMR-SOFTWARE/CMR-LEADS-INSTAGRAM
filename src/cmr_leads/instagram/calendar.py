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

from cmr_leads.db.models import InstagramPlanItem, InstagramPost, InstagramScheduled
from cmr_leads.instagram.dashboard import SLOTS, WEEKDAYS, slot_of, stories_tracked_since
from cmr_leads.instagram.promote import match_post
from cmr_leads.instagram.stats import to_local

LOOKBACK_DAYS = 365
RECENT_WEEKS = 8
SMOOTH = 3.0  # cuánto pesa el promedio general frente a pocos datos
MIN_PER_WEEK, MAX_PER_WEEK = 2, 5
MIN_STORY_SLOT = 5  # historias mínimas en una franja para recomendarla
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


def _story_time(stories: list[InstagramPost], slot: str) -> dict:
    """La mejor hora dentro de una franja: la de mejores vistas con al menos 3 historias.

    Si ninguna hora tiene datos suficientes, se sugiere una hora después del inicio de la franja.
    """
    start, end = next((a, b) for a, b, x in SLOTS if x == slot)
    by_hour: dict[int, list[float]] = defaultdict(list)
    for st in stories:
        hour = to_local(st.published_at).hour
        if start <= hour < end:
            by_hour[hour].append(_log(st))
    values = [v for vals in by_hour.values() for v in vals]
    hour = min(start + 1, end - 1)
    expected = None
    if values:
        avg, sd = mean(values), pstdev(values)
        cap = avg + 1.5 * sd
        clipped = {h: [min(v, cap) for v in vals] for h, vals in by_hour.items()}
        expected = round(expm1(mean(min(v, cap) for v in values)))
        good = [h for h, vals in clipped.items() if len(vals) >= 3]
        if good:
            hour = max(
                good,
                key=lambda h: _smoothed(clipped[h], avg) - sd / sqrt(len(clipped[h]) + SMOOTH),
            )
    return {
        "slot": slot,
        "hour": hour,
        "time": f"{hour:02d}:00",
        "expected": expected,
        "samples": len(values),
    }


def story_recommendation(session: Session, workspace_id: int, today: date) -> dict:
    """Cuántas historias subir por día y a qué hora (lo mismo que muestra el calendario)."""
    feed, _promoted, stories = _feed_posts(session, workspace_id, today, [])
    tracked = stories_tracked_since(session, workspace_id)
    tracked_days = (today - tracked).days + 1 if tracked else 0
    return stories_plan(stories, Stats(feed), tracked_days)


def stories_plan(stories: list[InstagramPost], stats: Stats, tracked_days: int) -> dict:
    """Historias por día y horarios. Con pocos datos se apoya en lo que funciona en el feed."""
    per_day = len(stories) / tracked_days if tracked_days else 0
    count = min(max(round(per_day), 2), 6)
    by_slot: dict[str, list[int]] = defaultdict(list)
    for s in stories:
        by_slot[slot_of(to_local(s.published_at).hour)].append(_log(s))
    candidates = [s for start, _e, s in SLOTS if 6 <= start < 24]
    if len(stories) >= 10:
        # Igual que en el feed: se recortan las que explotaron (por ejemplo, una mención que
        # compartieron otras cuentas) y se elige con una estimación prudente, así una franja
        # con 2 historias buenas no le gana a una con 30 historias parejas.
        logs = [_log(s) for s in stories]
        avg, sd = mean(logs), pstdev(logs)
        cap = avg + 1.5 * sd
        clipped = {k: [min(v, cap) for v in vals] for k, vals in by_slot.items()}
        tried = [s for s in candidates if len(clipped.get(s, [])) >= MIN_STORY_SLOT]
        candidates = tried or candidates

        def prudent(slot: str) -> float:
            vals = clipped.get(slot, [])
            return _smoothed(vals, avg) - sd / sqrt(len(vals) + SMOOTH)

        ranked = sorted(candidates, key=prudent, reverse=True)
        source = "historias"
    else:
        candidates = stats.tried_slots(minimum=2)
        ranked = sorted(candidates, key=lambda s: stats.slot_effect[s], reverse=True)
        source = "feed"
    slots = ranked[: min(3, count)]
    slots.sort(key=lambda s: next(a for a, _b, x in SLOTS if x == s))
    times = [_story_time(stories, slot) for slot in slots]
    return {
        "count": count,
        "slots": slots,
        "times": times,
        "time_labels": [t["time"] for t in times],
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


SUGGEST_MARGIN = 0.25  # en escala logarítmica: ~28% más de vistas para sugerir una excepción
OVER_PENALTY = 0.6  # costo de usar un formato más veces de lo que le toca en el mes


def _assign_kinds(
    stats: Stats, timeline: list, counts: dict[str, int], last_kind, no_reel_run=True
):
    """Elige el formato de cada lugar libre, en orden de fecha.

    `timeline` es la lista ordenada de (fecha, formato o None): None es un lugar a completar.
    Busca la combinación que más rinde respetando la regla de no subir dos reels seguidos
    (también contra lo fijado y lo último que se subió), con la cantidad de cada formato del mes.
    """
    slots = [i for i, (_d, k) in enumerate(timeline) if k is None]

    def solve(kinds: list[str]):
        gain = {(i, k): stats.day_rank(k, timeline[i][0].weekday()) for i in slots for k in kinds}
        start = tuple(counts.get(k, 0) for k in kinds)
        memo: dict = {}

        def best(pos: int, rem: tuple, prev):
            if pos == len(timeline):
                return 0.0, ()
            key = (pos, rem, prev)
            if key in memo:
                return memo[key]
            day, fixed_kind = timeline[pos]
            if fixed_kind is not None:
                result = best(pos + 1, rem, fixed_kind)
                memo[key] = result
                return result
            out = (float("-inf"), ())
            for j, k in enumerate(kinds):
                if no_reel_run and k == "reel" and prev == "reel":
                    continue
                nxt = timeline[pos + 1][1] if pos + 1 < len(timeline) else None
                if no_reel_run and k == "reel" and nxt == "reel":
                    continue
                over = rem[j] <= 0
                new_rem = rem[:j] + (max(rem[j] - 1, 0),) + rem[j + 1 :]
                value, choice = best(pos + 1, new_rem, k)
                value += gain[(pos, k)] - (OVER_PENALTY if over else 0.0)
                if value > out[0]:
                    out = (value, (k,) + choice)
            if out[0] == float("-inf"):  # no hay forma sin dos reels seguidos: se usa lo menos malo
                for k in kinds:
                    if k == "reel":
                        continue
                    value, choice = best(pos + 1, rem, k)
                    value += gain[(pos, k)] - OVER_PENALTY
                    if value > out[0]:
                        out = (value, (k,) + choice)
            memo[key] = out
            return out

        return best(0, start, last_kind)

    # Primero con los formatos del reparto del mes. Si así no hay forma de evitar dos reels
    # seguidos (pocos datos: el reparto es solo de reels), se suman los otros formatos.
    score, choice = solve(sorted(counts) or ["reel"])
    if score == float("-inf") or len(choice) < len(slots):
        score, choice = solve(sorted(set(counts) | {"reel", "carrusel", "imagen"}))
    return score, dict(zip(slots, choice, strict=False))


def plan_month(
    stats: Stats,
    month: date,
    month_weeks,
    per_week: int,
    changes: list,
    today: date,
    posted: dict | None = None,
):
    """Arma el mes a partir de lo fijado a mano, lo ya subido y lo que queda por delante.

    Reglas fijas:
    - Nunca dos publicaciones (reel, carrusel o imagen) en días seguidos.
    - Nunca un reel justo después de otro reel.
    Lo fijado a mano se respeta aunque no cumpla las reglas; el resto se acomoda alrededor.
    Si romper una regla rindiera bastante más, no se aplica: queda como sugerencia.

    Además:
    - Solo recomienda en días que todavía no pasaron.
    - Cada semana apunta a `per_week` (proporcional si la semana está cortada por el mes o por hoy).
    - Lo que se movió, editó o quitó deja de contar en la semana de donde salió.
    - El reparto de formatos es del mes completo contando lo fijado.
    - Si una semana no tiene lugar, lo que sobra pasa a otra semana.
    """
    posted = posted or {}
    in_month = [d for days in month_weeks for d in days if d.month == month.month]
    fixed = [c for c in changes if c.kind in SHORT and not c.removed and c.day.date() in in_month]
    blocked = {c.day.date() for c in changes if c.kind == BLOCKED}
    occupied = {c.day.date(): c.kind for c in fixed}
    for d, k in posted.items():  # lo que ya se subió también cuenta para no pegar publicaciones
        occupied.setdefault(d, k)

    def free(d: date, taken) -> bool:
        return (
            d not in taken
            and (d - timedelta(days=1)) not in taken
            and (d + timedelta(days=1)) not in taken
        )

    open_by_week, base = [], []
    for days in month_weeks:
        ahead = [d for d in days if d.month == month.month and d >= today]
        open_by_week.append([d for d in ahead if d not in blocked and d not in occupied])
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
    counts = {k: v for k, v in counts.items() if v > 0}
    weights = {k: v / sum(counts.values()) for k, v in counts.items()} if counts else {}

    def day_value(d: date) -> float:
        if not weights:
            return stats.day_rank("reel", d.weekday())
        return sum(w * stats.day_rank(k, d.weekday()) for k, w in weights.items())

    # 1) Fechas: las mejores de cada semana, sin días seguidos.
    taken = set(occupied)
    dates: list[date] = []
    carry = 0
    for w, open_days in enumerate(open_by_week):
        want = base[w] + carry
        chosen = []
        for d in sorted(open_days, key=day_value, reverse=True):
            if len(chosen) >= want:
                break
            if free(d, taken):
                taken.add(d)
                chosen.append(d)
        dates += chosen
        carry = want - len(chosen)
    if carry:  # lo que no entró va donde haya lugar
        for d in sorted((d for days in open_by_week for d in days), key=day_value, reverse=True):
            if carry and free(d, taken):
                taken.add(d)
                dates.append(d)
                carry -= 1
    missing = carry

    # 2) Formatos: la mejor combinación sin dos reels seguidos.
    first = min(dates, default=today)
    before = [d for d in occupied if d < first]
    last_kind = occupied[max(before)] if before else None
    timeline = sorted(
        [(d, None) for d in dates] + [(d, k) for d, k in occupied.items() if d >= first]
    )
    score, chosen_kinds = _assign_kinds(stats, timeline, counts, last_kind)

    plans: list[list[dict]] = [[] for _ in month_weeks]
    for i, (d, k) in enumerate(timeline):
        if k is not None:
            continue
        kind = chosen_kinds.get(i, "reel")
        slot, slot_n = stats.best_slot(kind)
        value, n = stats.day_score(kind, d.weekday())
        plans[week_of[d]].append(
            {
                "kind": kind,
                "date": d,
                "weekday": d.weekday(),
                "slot": slot,
                "hour": stats.hour_for(kind, slot),
                "expected": round(value),
                "samples": n,
                "slot_samples": slot_n,
            }
        )
    for plan in plans:
        plan.sort(key=lambda i: i["date"])

    # 3) Sugerencias: excepciones a las reglas que según los números convendrían.
    suggestions = []
    for d in sorted(dates):
        item = next(i for i in plans[week_of[d]] if i["date"] == d)
        kind = item["kind"]
        here = stats.day_rank(kind, d.weekday())
        for other in (d - timedelta(days=1), d + timedelta(days=1)):
            if (
                other < today
                or other.month != month.month
                or other in blocked
                or other in occupied
                or other in dates
            ):
                continue
            gain = stats.day_rank(kind, other.weekday()) - here
            if gain >= SUGGEST_MARGIN:
                suggestions.append(
                    {
                        "date": other,
                        "text": (
                            f"El {kind_name(kind)} del {d:%d/%m} rendiría más el "
                            f"{WEEKDAYS[other.weekday()].lower()} {other:%d/%m}, pero quedaría "
                            "pegado a otra publicación. Si querés probarlo, movelo."
                        ),
                        "gain": gain,
                    }
                )
    if timeline and any(k is None for _d, k in timeline):
        free_score, free_kinds = _assign_kinds(
            stats, timeline, counts, last_kind, no_reel_run=False
        )
        if free_score - score >= SUGGEST_MARGIN:
            changed = [timeline[i][0] for i, k in free_kinds.items() if chosen_kinds.get(i) != k]
            if changed:
                suggestions.append(
                    {
                        "date": changed[0],
                        "text": (
                            "Según tus números, subir dos reels seguidos rendiría más ("
                            + ", ".join(f"{d:%d/%m}" for d in changed[:3])
                            + " como reel). Si querés probarlo, cambialos a reel."
                        ),
                        "gain": free_score - score,
                    }
                )
    if missing:
        suggestions.append(
            {
                "date": None,
                "text": (
                    f"No entraron {missing} publicación(es) sin pegarlas a otra. "
                    "Si querés subir más, agregalas a mano en un día libre."
                ),
                "gain": 0,
            }
        )
    # Avisos: lo fijado a mano que no cumple las reglas (se respeta, pero conviene saberlo).
    fixed_days = {c.day.date() for c in fixed}
    final = sorted(
        [(d, k) for d, k in occupied.items() if d >= today and d.month == month.month]
        + [(i["date"], i["kind"]) for plan in plans for i in plan]
    )
    for (d1, k1), (d2, k2) in zip(final, final[1:], strict=False):
        if d1 not in fixed_days and d2 not in fixed_days:
            continue
        if (d2 - d1).days == 1:
            problem = "quedan dos publicaciones en días seguidos"
        elif k1 == "reel" and k2 == "reel":
            problem = "quedan dos reels seguidos"
        else:
            continue
        suggestions.append(
            {
                "date": d1,
                "text": (
                    f"El {d1:%d/%m} y el {d2:%d/%m} {problem} por algo que fijaste vos. "
                    "Se respeta, pero si no fue a propósito, cambiá uno."
                ),
                "gain": -1,
            }
        )
    suggestions.sort(key=lambda x: -x["gain"])

    fixed_by_week = [[c for c in fixed if c.day.date() in set(days)] for days in month_weeks]

    # Una prueba por mes: el mejor formato en el día menos probado que quede libre.
    test = None
    best_kind = weekly_mix(stats, 1)[0] if stats.posts else "reel"
    candidates = [d for days in open_by_week for d in days if free(d, taken)]
    weekdays = [d for d in candidates if d.weekday() < 5] or candidates
    if weekdays:
        day = min(
            weekdays,
            key=lambda d: (
                stats.day_score(best_kind, d.weekday())[1],
                -stats.day_score(best_kind, d.weekday())[0],
                d,
            ),
        )
        neighbors = {}
        for d, k in timeline:
            neighbors[d] = k if k is not None else chosen_kinds.get(timeline.index((d, k)))
        prev = [k for d, k in sorted(neighbors.items()) if d < day]
        nxt = [k for d, k in sorted(neighbors.items()) if d > day]
        kind = best_kind
        if kind == "reel" and ((prev and prev[-1] == "reel") or (nxt and nxt[0] == "reel")):
            kind = next((k for k in weekly_mix(stats, 3) if k != "reel"), "carrusel")
        slot, _n = stats.best_slot(kind)
        value, n = stats.day_score(kind, day.weekday())
        test = {
            "kind": kind,
            "date": day,
            "weekday": day.weekday(),
            "slot": slot,
            "hour": stats.hour_for(kind, slot),
            "expected": round(value),
            "samples": n,
            "test": True,
        }
    return plans, fixed_by_week, blocked, test, suggestions[:4]


def kind_name(kind: str) -> str:
    return {"reel": "reel", "carrusel": "carrusel", "imagen": "imagen"}.get(kind, kind)


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
    posted = {}
    for p in sorted(
        feed + list(session.scalars(select(InstagramPost).where(InstagramPost.id.in_(promoted)))),
        key=lambda p: p.published_at,
    ):
        posted[to_local(p.published_at).date()] = FEED_KINDS[p.kind]
    plans, fixed_by_week, blocked_days, test, suggestions = plan_month(
        stats, month, month_weeks, per_week, changes, today, posted
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

    # Lo programado desde la app: se marca en el calendario y no se puede pisar.
    scheduled = {
        sc.plan_item_id: sc
        for sc in session.scalars(
            select(InstagramScheduled).where(
                InstagramScheduled.workspace_id == workspace_id,
                InstagramScheduled.plan_item_id.is_not(None),
                InstagramScheduled.status != "cancelada",
            )
        )
    }
    for row in weeks:
        for cell in row:
            for i in cell["items"]:
                sc = scheduled.get(i.get("id")) if i.get("fixed") else None
                if sc is not None:
                    local = to_local(sc.publish_at)
                    i["scheduled"] = sc.status
                    i["time"] = f"{local:%H:%M}"
                    i["why"] = SCHEDULED_WHY.get(sc.status, "Programada desde la app.")

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
        "suggestions": suggestions,
        "changes": len(changes),
        "kinds": {k: v for k, v in SHORT.items() if k != "historia"},
    }


BLOCKED = "bloqueado"
NEXT_NAME = {
    "reel": "el próximo reel",
    "carrusel": "el próximo carrusel",
    "imagen": "la próxima imagen",
}
SCHEDULED_WHY = {
    "pendiente": "Programada: la app la publica sola a esta hora.",
    "publicando": "Publicándose ahora.",
    "publicada": "Ya se publicó desde la app.",
    "vencida": "No salió: la app estaba cerrada. Entrá a Programar para publicarla.",
    "error": "No se pudo publicar. Entrá a Programar para ver el motivo.",
}


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
                for n, t in enumerate(st["times"]):
                    start = datetime(d.year, d.month, d.day, t["hour"])
                    end = start + timedelta(minutes=15)
                    lines += [
                        "BEGIN:VEVENT",
                        f"UID:cmr-ig-{d:%Y%m%d}-historia-{n}@cmr-leads",
                        f"DTSTAMP:{stamp}",
                        f"DTSTART;TZID=America/Argentina/Buenos_Aires:{start:%Y%m%dT%H%M%S}",
                        f"DTEND;TZID=America/Argentina/Buenos_Aires:{end:%Y%m%dT%H%M%S}",
                        "SUMMARY:" + esc("Instagram: historia"),
                        "DESCRIPTION:" + esc(f"Franja de {t['slot']}"),
                        "END:VEVENT",
                    ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def next_slot(
    session: Session, workspace_id: int, kind: str, now_local: datetime, ad_names: list[str]
) -> tuple[date, int, str] | None:
    """El próximo día y hora que recomienda el calendario para ese formato."""
    today = now_local.date()
    if kind == "historia":
        plan = story_recommendation(session, workspace_id, today)
        for offset in (0, 1):
            d = today + timedelta(days=offset)
            for t in plan["times"]:
                if datetime(d.year, d.month, d.day, t["hour"]) > now_local:
                    return (
                        d,
                        t["hour"],
                        (
                            f"Sugerido: {t['time']}, una de las mejores horas para historias "
                            "según tus números."
                        ),
                    )
        return None
    month = today.replace(day=1)
    for m in (month, (month + timedelta(days=32)).replace(day=1)):
        c = build_calendar(session, workspace_id, m, today, ad_names)
        for row in c["weeks"]:
            for cell in row:
                if not cell["in_month"]:
                    continue
                for i in cell["items"]:
                    when = datetime(i["date"].year, i["date"].month, i["date"].day, i["hour"])
                    if i["kind"] == kind and not i.get("fixed") and when > now_local:
                        return (
                            i["date"],
                            i["hour"],
                            (
                                f"Sugerido: {NEXT_NAME[kind]} que recomienda el "
                                f"calendario ({WEEKDAYS[i['date'].weekday()].lower()} "
                                f"{i['date']:%d/%m} a las {i['time']})."
                            ),
                        )
    return None

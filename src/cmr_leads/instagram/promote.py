"""Qué publicación conviene promocionar, según cómo le fue sin publicidad y con publicidad.

- Sin publicidad: se compara cada publicación con la publicación típica de la cuenta
  (mediana) en dos cosas: a cuánta gente llegó sola y qué proporción interactuó.
  Guardados y compartidos pesan doble, porque muestran que el contenido le sirvió a alguien.
- Con publicidad: el alcance que informa Instagram ya incluye lo pagado, así que esas
  publicaciones no se comparan con las orgánicas; se miran los números del anuncio
  (cuánto costó llegar a cada 1.000 personas y qué se consiguió).
"""

import re
from datetime import date, timedelta
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from cmr_leads.db.models import InstagramPost
from cmr_leads.instagram.stats import to_local

MIN_REACH = 50  # menos que esto es ruido
LOOKBACK_DAYS = 365
CAP = 5.0  # que una sola publicación rarísima no tape todo
MIN_SCORE = 1.1  # al menos 10% mejor que la publicación típica
_KIND_TEXT = {"Reel / video": "un reel", "Carrusel": "un carrusel", "Imagen": "una imagen"}
_PREFIXES = ("instagram post:", "facebook post:")


def _norm(text: str | None) -> str:
    text = (text or "").lower()
    for prefix in _PREFIXES:
        text = text.removeprefix(prefix)
    text = re.sub(r"\s+", " ", text).strip()
    return text.removesuffix("...").removesuffix("…").strip()


def match_post(ad_name: str, posts: list[InstagramPost]) -> InstagramPost | None:
    """Meta nombra los anuncios de publicaciones existentes "Instagram post: <inicio del texto>..."."""
    if not ad_name.lower().startswith(_PREFIXES):
        return None
    start = _norm(ad_name)
    if len(start) < 10:
        return None
    found = [p for p in posts if _norm(p.caption).startswith(start)]
    return max(found, key=lambda p: p.published_at) if found else None


def engagement(post: InstagramPost) -> float | None:
    if not post.reach:
        return None
    weighted = (
        (post.likes or 0) + (post.comments or 0) + 2 * (post.saves or 0) + 2 * (post.shares or 0)
    )
    return weighted / post.reach


def _title(post: InstagramPost, size: int = 70) -> str:
    text = ((post.caption or "Sin texto").split("\n")[0]).strip()
    return text if len(text) <= size else text[: size - 1] + "…"


def _n(value: float) -> str:
    return f"{value:,.0f}".replace(",", ".")


def _d(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def _reasons(post, reach_idx, eng_idx, base_reach, base_eng, best_kind, today) -> list[str]:
    reasons = []
    if reach_idx >= 1.1:
        reasons.append(
            f"Sin publicidad llegó a {_n(post.reach)} personas, {_d(reach_idx)} veces lo que "
            f"llega una publicación típica ({_n(base_reach)})."
        )
    if eng_idx >= 1.1:
        reasons.append(
            f"Interactuó más gente de lo normal: {_d(engagement(post) * 100)} de cada 100 que "
            f"la vieron (lo típico es {_d(base_eng * 100)})."
        )
    saved, shared = post.saves or 0, post.shares or 0
    if saved + shared >= 3:
        parts = [
            f"la compartieron {shared} veces" if shared else "",
            f"la guardaron {saved}" if saved else "",
        ]
        text = " y ".join(x for x in parts if x)
        reasons.append(f"{text[0].upper()}{text[1:]}: le sirvió a la gente.")
    if best_kind and post.kind == best_kind:
        reasons.append(
            f"Es {_KIND_TEXT.get(best_kind, best_kind)}: en tu cuenta es el formato que más "
            "gente alcanza sin publicidad."
        )
    if post.published_at:
        days = (today - post.published_at.date()).days
        if days > 120:
            reasons.append(
                f"Tiene {days} días: revisá que el contenido siga vigente antes de pagar."
            )
    return reasons


def promotion_ideas(
    session: Session, workspace_id: int, ad_rows: list, today: date, limit: int = 3
) -> dict:
    """`ad_rows` son las filas por anuncio (level_rows nivel "anuncios") de todo el historial."""
    since = today - timedelta(days=LOOKBACK_DAYS)
    posts = [
        p
        for p in session.scalars(
            select(InstagramPost).where(InstagramPost.workspace_id == workspace_id)
        )
        if not p.is_story and p.published_at and p.published_at.date() >= since
    ]

    # Publicaciones que ya tuvieron publicidad (varios anuncios pueden usar la misma).
    promoted: dict[int, dict] = {}
    for row in ad_rows:
        post = match_post(row.name, posts)
        if post is None or not row.totals.spend:
            continue
        item = promoted.setdefault(
            post.id,
            {
                "post": post,
                "title": _title(post),
                "spend": 0.0,
                "reach": 0,
                "result": 0,
                "goal": row.goal.result,
                "ads": 0,
            },
        )
        item["spend"] += row.totals.spend
        item["reach"] += row.totals.reach or 0
        item["result"] += row.result or 0
        item["ads"] += 1
    costs = []
    for item in promoted.values():
        item["cost"] = item["spend"] / item["reach"] * 1000 if item["reach"] else None
        if item["cost"]:
            costs.append(item["cost"])
    best_cost = min(costs) if costs else None
    for item in promoted.values():
        if item["cost"] is None:
            item["verdict"], item["why"] = "Sin datos", "El anuncio no informó alcance."
        elif item["cost"] <= best_cost * 1.25:
            item["verdict"] = "Volver a promocionar"
            item["why"] = "Fue de las que menos costó para llegar a la gente."
        else:
            item["verdict"] = "No repetir así"
            item["why"] = (
                f"Llegar a 1.000 personas costó {_d(item['cost'] / best_cost)} veces más que "
                "con la mejor."
            )
    promoted_list = sorted(promoted.values(), key=lambda x: x["cost"] or float("inf"))

    # Publicaciones sin publicidad: comparación contra la publicación típica.
    organic = [p for p in posts if p.id not in promoted and (p.reach or 0) >= MIN_REACH]
    candidates = []
    base_reach = base_eng = None
    if len(organic) >= 3:
        base_reach = median(p.reach for p in organic)
        base_eng = median(engagement(p) for p in organic)
        by_kind: dict[str, list[int]] = {}
        for p in organic:
            by_kind.setdefault(p.kind, []).append(p.reach)
        ranked = [(median(v), k) for k, v in by_kind.items() if len(v) >= 3]
        best_kind = max(ranked)[1] if ranked else None
        for p in organic:
            reach_idx = p.reach / base_reach
            eng_idx = engagement(p) / base_eng if base_eng else 1.0
            score = (min(reach_idx, CAP) + min(eng_idx, CAP)) / 2
            if score < MIN_SCORE:
                continue
            candidates.append(
                {
                    "post": p,
                    "title": _title(p),
                    "published": to_local(p.published_at).strftime("%d/%m/%Y"),
                    "score": round(score, 2),
                    "reasons": _reasons(
                        p, reach_idx, eng_idx, base_reach, base_eng, best_kind, today
                    ),
                }
            )
        candidates.sort(key=lambda x: x["score"], reverse=True)
    return {
        "candidates": candidates[:limit],
        "promoted": promoted_list,
        "base_reach": base_reach,
        "base_engagement": base_eng,
        "organic_count": len(organic),
    }


def ideas_facts(ideas: dict) -> dict:
    """Versión resumida para el análisis (sin objetos de la base)."""
    return {
        "como_se_calcula": (
            "Candidatas: publicaciones SIN publicidad que llegaron a más gente o tuvieron más "
            "interacción que la publicación típica de la cuenta. Ya promocionadas: se miden por "
            "el costo de llegar a 1.000 personas con el anuncio."
        ),
        "candidatas_a_promocionar": [
            {
                "texto": c["title"],
                "formato": c["post"].kind,
                "alcance_sin_publicidad": c["post"].reach,
                "puntaje_vs_tipica": c["score"],
                "motivos": c["reasons"],
            }
            for c in ideas["candidates"]
        ],
        "ya_promocionadas": [
            {
                "texto": x["title"],
                "inversion": round(x["spend"]),
                "personas_alcanzadas": x["reach"],
                "costo_cada_1000": round(x["cost"]) if x["cost"] else None,
                "resultado": f"{x['result']} {x['goal']}",
                "veredicto": x["verdict"],
            }
            for x in ideas["promoted"]
        ],
    }


def ideas_for(session: Session, workspace_id: int, today: date) -> dict:
    """Ideas con los anuncios del año (el período que Meta guarda con alcance exacto)."""
    from cmr_leads.ads.metrics import level_rows
    from cmr_leads.ads.sync import standard_periods

    since, until = standard_periods(today)["anio"]
    rows = level_rows(session, workspace_id, "anuncios", since, until)
    return promotion_ideas(session, workspace_id, rows, today)

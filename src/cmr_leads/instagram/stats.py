"""Estadísticas propias de Instagram para filtrar por período: día por día, publicaciones e historias.

- Cuenta: una fila por día (`InstagramDaily`). La primera vez completa desde el 1 de enero, de a
  tandas para no gastar el tope diario de consultas; después refresca los últimos días.
- Publicaciones: todas las del año (con paginación). Las nuevas se refrescan en cada
  actualización; las viejas, una vez que sus números ya se estabilizaron.
- Historias: Meta solo las muestra mientras están activas (24 h), por eso se piden cada hora
  mientras la app está abierta. La última lectura antes de que venzan queda guardada.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from cmr_leads.db.models import InstagramDaily, InstagramPost, utcnow
from cmr_leads.enrichment.instagram_discovery import AuthError, InstagramApiError, RateLimitedError
from cmr_leads.instagram.client import GraphClient, metric_values

ARGENTINA = timedelta(hours=-3)
DAY_METRICS = (
    "reach",
    "views",
    "profile_views",
    "website_clicks",
    "accounts_engaged",
    "total_interactions",
)
REEL_METRICS = ("views", "reach", "saved", "shares", "total_interactions")
FEED_METRICS = (*REEL_METRICS, "profile_visits", "follows")
STORY_METRICS = (
    "views",
    "reach",
    "replies",
    "shares",
    "total_interactions",
    "profile_visits",
    "follows",
)
MEDIA_FIELDS = (
    "id,caption,media_type,media_product_type,timestamp,permalink,like_count,comments_count"
)
STORY_FIELDS = "id,caption,media_type,media_product_type,timestamp,permalink"
REFRESH_DAYS = 3  # Meta corrige los últimos días: se vuelven a pedir siempre
BACKFILL_PER_SYNC = 120  # días de historia que se completan por actualización
BACKFILL_RESERVE = 150  # consultas que se dejan libres para publicaciones y días recientes
FRESH_POST_DAYS = 30  # publicaciones que todavía suman vistas: se refrescan siempre
STABLE_AFTER_DAYS = 7  # una publicación leída 7 días después de salir ya no cambia mucho
FATAL = (RateLimitedError, AuthError)


@dataclass
class StatsResult:
    days: int = 0
    posts: int = 0
    stories: int = 0
    pending_days: int = 0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{self.days} días", f"{self.posts} publicaciones", f"{self.stories} historias"]
        text = "Instagram: " + ", ".join(parts)
        if self.pending_days:
            text += f". Faltan {self.pending_days} días de historia: se completan en las próximas actualizaciones"
        return text


def local_now() -> datetime:
    return utcnow() + ARGENTINA


def to_local(value: datetime) -> datetime:
    return value + ARGENTINA


def history_start(today: date) -> date:
    return today.replace(month=1, day=1)


def _day_bounds(day: date) -> tuple[int, int]:
    """Medianoche de Argentina en unix (UTC-3) para pedir un solo día."""
    start = datetime(day.year, day.month, day.day, 3, 0)  # 00:00 ART = 03:00 UTC
    epoch = datetime(1970, 1, 1)
    since = int((start - epoch).total_seconds())
    return since, since + 86400


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")


def _insights(client: GraphClient, path: str, metrics: tuple[str, ...], warnings, **params) -> dict:
    """Pide todas las métricas juntas; si Meta rechaza alguna, las pide de a una."""
    try:
        return metric_values(client.get(path, metric=",".join(metrics), **params))
    except FATAL:
        raise
    except InstagramApiError:
        pass
    values: dict[str, int] = {}
    for metric in metrics:
        try:
            values.update(metric_values(client.get(path, metric=metric, **params)))
        except FATAL:
            raise
        except InstagramApiError as exc:
            warnings.append(f"{metric}: {exc}")
    return values


def link_breakdown(payload: dict) -> tuple[int | None, dict[str, int]]:
    """Total de toques a botones del perfil y el detalle por tipo de botón."""
    for item in payload.get("data", []):
        total = item.get("total_value") or {}
        detail: dict[str, int] = {}
        for breakdown in total.get("breakdowns") or []:
            for row in breakdown.get("results") or []:
                key = (row.get("dimension_values") or ["OTRO"])[0] or "OTRO"
                detail[key] = detail.get(key, 0) + int(row.get("value") or 0)
        return int(total.get("value") or 0), detail
    return None, {}


# ---------------- Cuenta, día por día ----------------


def days_to_fetch(session: Session, workspace_id: int, today: date) -> tuple[list[date], int]:
    """Días que faltan (los más nuevos primero) y cuántos quedan para la próxima vez."""
    have = {
        d.date()
        for d in session.scalars(
            select(InstagramDaily.day).where(InstagramDaily.workspace_id == workspace_id)
        )
    }
    start = history_start(today)
    days = []
    current = today
    while current >= start:
        if current not in have or (today - current).days < REFRESH_DAYS:
            days.append(current)
        current -= timedelta(days=1)
    recent = [d for d in days if (today - d).days < REFRESH_DAYS]
    missing = [d for d in days if (today - d).days >= REFRESH_DAYS]
    batch = recent + missing[:BACKFILL_PER_SYNC]
    return batch, max(0, len(missing) - BACKFILL_PER_SYNC)


def sync_days(
    session: Session,
    workspace_id: int,
    client: GraphClient,
    ig_id: str,
    today: date,
    result,
    budget_left=None,
) -> None:
    """`budget_left` (opcional) devuelve las consultas que quedan: el relleno de días viejos
    se corta antes de gastarlas todas, para no dejar sin cupo al resto."""
    days, result.pending_days = days_to_fetch(session, workspace_id, today)
    existing = {
        r.day.date(): r
        for r in session.scalars(
            select(InstagramDaily).where(InstagramDaily.workspace_id == workspace_id)
        )
    }
    for day in days:
        old = (today - day).days >= REFRESH_DAYS
        if old and budget_left is not None and budget_left() < BACKFILL_RESERVE:
            result.pending_days += sum(1 for d in days if d <= day)
            break
        since, until = _day_bounds(day)
        params = {"period": "day", "metric_type": "total_value", "since": since, "until": until}
        values = _insights(client, f"{ig_id}/insights", DAY_METRICS, result.warnings, **params)
        try:
            taps, detail = link_breakdown(
                client.get(
                    f"{ig_id}/insights",
                    metric="profile_links_taps",
                    breakdown="contact_button_type",
                    **params,
                )
            )
        except FATAL:
            raise
        except InstagramApiError as exc:
            result.warnings.append(f"profile_links_taps: {exc}")
            taps, detail = None, {}
        row = existing.get(day)
        if row is None:
            row = InstagramDaily(
                workspace_id=workspace_id, day=datetime(day.year, day.month, day.day)
            )
            session.add(row)
            existing[day] = row
        for key in DAY_METRICS:
            setattr(row, key, values.get(key))
        row.link_taps = taps
        row.link_breakdown = detail or None
        row.fetched_at = utcnow()
        result.days += 1
        session.commit()  # día por día: la base no queda tomada mientras se consulta a Meta


# ---------------- Publicaciones e historias ----------------


def _metrics_for(item: dict) -> tuple[str, ...]:
    product = item.get("media_product_type")
    if product == "STORY":
        return STORY_METRICS
    if product == "REELS" or item.get("media_type") == "VIDEO":
        return REEL_METRICS
    return FEED_METRICS


def needs_refresh(post: InstagramPost, now: datetime) -> bool:
    if post.published_at is None or post.reach is None:
        return True
    if now - post.published_at <= timedelta(days=FRESH_POST_DAYS):
        return True
    return post.fetched_at < post.published_at + timedelta(days=STABLE_AFTER_DAYS)


def save_media(
    session: Session, workspace_id: int, client: GraphClient, item: dict, cache: dict, warnings
) -> bool:
    """Guarda una publicación o historia y, si hace falta, sus métricas. Devuelve si pidió métricas."""
    post = cache.get(item["id"])
    if post is None:
        post = InstagramPost(workspace_id=workspace_id, media_id=item["id"])
        session.add(post)
        cache[item["id"]] = post
    post.media_type = item.get("media_type")
    post.product_type = item.get("media_product_type")
    post.caption = (item.get("caption") or "")[:2000] or None
    post.permalink = item.get("permalink")
    post.published_at = _parse_time(item.get("timestamp"))
    if "like_count" in item:
        post.likes = item.get("like_count")
    if "comments_count" in item:
        post.comments = item.get("comments_count")
    if not needs_refresh(post, utcnow()) and post.product_type != "STORY":
        return False
    values = _insights(client, f"{item['id']}/insights", _metrics_for(item), warnings)
    post.views = values.get("views")
    post.reach = values.get("reach")
    post.saves = values.get("saved")
    post.shares = values.get("shares")
    post.total_interactions = values.get("total_interactions")
    post.profile_visits = values.get("profile_visits")
    post.follows = values.get("follows")
    post.replies = values.get("replies")
    post.fetched_at = utcnow()
    return True


def _post_cache(session: Session, workspace_id: int) -> dict[str, InstagramPost]:
    return {
        p.media_id: p
        for p in session.scalars(
            select(InstagramPost).where(InstagramPost.workspace_id == workspace_id)
        )
    }


def sync_media(
    session: Session, workspace_id: int, client: GraphClient, ig_id: str, today: date, result
) -> None:
    """Todas las publicaciones del año (Meta las devuelve de la más nueva a la más vieja)."""
    start = datetime(*history_start(today).timetuple()[:3])
    cache = _post_cache(session, workspace_id)
    after = None
    while True:
        params = {"fields": MEDIA_FIELDS, "limit": 50}
        if after:
            params["after"] = after
        page = client.get(f"{ig_id}/media", **params)
        reached_start = False
        for item in page.get("data", []):
            published = _parse_time(item.get("timestamp"))
            if published and published < start:
                reached_start = True
                break
            save_media(session, workspace_id, client, item, cache, result.warnings)
            result.posts += 1
            session.commit()
        after = ((page.get("paging") or {}).get("cursors") or {}).get("after")
        if reached_start or not after or not (page.get("paging") or {}).get("next"):
            return


def sync_stories(
    session: Session, workspace_id: int, client: GraphClient, ig_id: str, result
) -> None:
    cache = _post_cache(session, workspace_id)
    for item in client.get(f"{ig_id}/stories", fields=STORY_FIELDS).get("data", []):
        item.setdefault("media_product_type", "STORY")
        save_media(session, workspace_id, client, item, cache, result.warnings)
        result.stories += 1
    session.flush()


def sync_stats(
    session: Session,
    workspace_id: int,
    client: GraphClient,
    ig_id: str,
    today: date | None = None,
    include_stories: bool = True,
    budget_left=None,
) -> StatsResult:
    """Historias, publicaciones y días. Guarda por etapas para no bloquear la base."""
    today = today or local_now().date()
    result = StatsResult()
    if include_stories:
        sync_stories(session, workspace_id, client, ig_id, result)
        session.commit()
    sync_media(session, workspace_id, client, ig_id, today, result)
    session.commit()
    sync_days(session, workspace_id, client, ig_id, today, result, budget_left)
    session.commit()
    sync_followers(session, workspace_id, client, ig_id, today, result)
    session.commit()
    return result


def sync_followers(
    session: Session, workspace_id: int, client: GraphClient, ig_id: str, today: date, result
) -> None:
    """Seguidores ganados por día. Meta solo los da de los últimos 30 días y con más de 100
    seguidores: lo anterior se completa con el CSV que exporta Meta."""
    since, _ = _day_bounds(today - timedelta(days=29))
    _, until = _day_bounds(today)
    try:
        payload = client.get(
            f"{ig_id}/insights", metric="follower_count", period="day", since=since, until=until
        )
    except FATAL:
        raise
    except InstagramApiError as exc:
        result.warnings.append(f"follower_count: {exc}")
        return
    existing = {
        r.day.date(): r
        for r in session.scalars(
            select(InstagramDaily).where(InstagramDaily.workspace_id == workspace_id)
        )
    }
    for item in payload.get("data", []):
        for value in item.get("values") or []:
            end = value.get("end_time")
            if not end or not isinstance(value.get("value"), int | float):
                continue
            # end_time es el cierre del día (08:00 UTC del día siguiente).
            day = datetime.fromisoformat(end.replace("+0000", "+00:00")).date() - timedelta(days=1)
            row = existing.get(day)
            if row is None:
                row = InstagramDaily(
                    workspace_id=workspace_id, day=datetime(day.year, day.month, day.day)
                )
                session.add(row)
                existing[day] = row
            row.follows = int(value["value"])

"""Sincroniza estadísticas de la cuenta, publicaciones y mensajes directos de Instagram.

Pide cada métrica por separado: Meta cambia seguido qué métricas existen, y así una métrica
dada de baja no impide traer las demás (queda anotada en `warnings`).
"""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from cmr_leads.db.models import (
    InstagramAccountSnapshot,
    InstagramPost,
    Interaction,
    Lead,
    LeadIdentifier,
    Workspace,
    utcnow,
)
from cmr_leads.domain.enums import Channel, IdentifierType, LeadStatus, SourceType
from cmr_leads.domain.schemas import RawProfile, SourceRecord
from cmr_leads.enrichment.instagram_discovery import AuthError, InstagramApiError, RateLimitedError
from cmr_leads.instagram.client import GraphClient, metric_values
from cmr_leads.pipeline.ingest import ingest
from cmr_leads.replies.classifier import ReplyClassifier
from cmr_leads.replies.service import _already_registered, register_reply
from cmr_leads.services import set_status

PERIOD_DAYS = 28
ACCOUNT_METRICS = (
    "reach",
    "views",
    "profile_views",
    "website_clicks",
    "accounts_engaged",
    "total_interactions",
)
POST_METRICS = ("reach", "views", "saved", "shares", "total_interactions")


class BudgetExceeded(Exception):
    pass


@dataclass
class SyncResult:
    account: bool = False
    posts: int = 0
    conversations: int = 0
    replies: int = 0
    new_leads: int = 0
    requests: int = 0
    warnings: list[str] = field(default_factory=list)
    stopped_reason: str | None = None

    def summary(self) -> str:
        parts = []
        if self.account:
            parts.append("cuenta actualizada")
        parts.append(f"{self.posts} publicaciones")
        if self.conversations:
            parts.append(
                f"{self.conversations} chats revisados, {self.replies} respuestas, {self.new_leads} leads nuevos"
            )
        if self.stopped_reason:
            parts.append(f"se detuvo: {self.stopped_reason}")
        elif self.warnings:
            parts.append(f"{len(self.warnings)} avisos")
        return " · ".join(parts)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")


def sync_account(
    session: Session, workspace: Workspace, client: GraphClient, ig_user_id: str, result: SyncResult
) -> None:
    info = client.get(ig_user_id, fields="username,followers_count,media_count")
    today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    until = int(utcnow().timestamp())
    since = until - PERIOD_DAYS * 86400
    values: dict[str, int] = {}
    for metric in ACCOUNT_METRICS:
        try:
            payload = client.get(
                f"{ig_user_id}/insights",
                metric=metric,
                period="day",
                metric_type="total_value",
                since=since,
                until=until,
            )
            values.update(metric_values(payload))
        except (RateLimitedError, AuthError, BudgetExceeded):
            raise
        except InstagramApiError as exc:
            result.warnings.append(f"métrica {metric}: {exc}")

    snapshot = session.scalar(
        select(InstagramAccountSnapshot).where(
            InstagramAccountSnapshot.workspace_id == workspace.id,
            InstagramAccountSnapshot.day == today,
        )
    )
    if snapshot is None:
        snapshot = InstagramAccountSnapshot(workspace_id=workspace.id, day=today)
        session.add(snapshot)
    snapshot.followers = info.get("followers_count")
    snapshot.media_count = info.get("media_count")
    snapshot.period_days = PERIOD_DAYS
    snapshot.reach = values.get("reach")
    snapshot.views = values.get("views")
    snapshot.profile_views = values.get("profile_views")
    snapshot.website_clicks = values.get("website_clicks")
    snapshot.accounts_engaged = values.get("accounts_engaged")
    snapshot.total_interactions = values.get("total_interactions")
    snapshot.raw = {"username": info.get("username"), **values}
    result.account = True
    session.flush()


def sync_posts(
    session: Session,
    workspace: Workspace,
    client: GraphClient,
    ig_user_id: str,
    limit: int,
    result: SyncResult,
) -> None:
    payload = client.get(
        f"{ig_user_id}/media",
        fields="id,caption,media_type,media_product_type,timestamp,permalink,like_count,comments_count",
        limit=min(limit, 50),
    )
    for item in payload.get("data", [])[:limit]:
        post = session.scalar(
            select(InstagramPost).where(
                InstagramPost.workspace_id == workspace.id, InstagramPost.media_id == item["id"]
            )
        )
        if post is None:
            post = InstagramPost(workspace_id=workspace.id, media_id=item["id"])
            session.add(post)
        post.media_type = item.get("media_type")
        post.product_type = item.get("media_product_type")
        post.caption = (item.get("caption") or "")[:2000] or None
        post.permalink = item.get("permalink")
        post.published_at = _parse_time(item.get("timestamp"))
        post.likes = item.get("like_count")
        post.comments = item.get("comments_count")
        values: dict[str, int] = {}
        for metric in POST_METRICS:
            try:
                values.update(metric_values(client.get(f"{item['id']}/insights", metric=metric)))
            except (RateLimitedError, AuthError, BudgetExceeded):
                raise
            except InstagramApiError:
                continue  # métrica no disponible para este tipo de publicación
        post.reach = values.get("reach")
        post.views = values.get("views")
        post.saves = values.get("saved")
        post.shares = values.get("shares")
        post.total_interactions = values.get("total_interactions")
        post.fetched_at = utcnow()
        result.posts += 1
    session.flush()


def _lead_by_username(session: Session, workspace_id: int, username: str) -> Lead | None:
    row = session.scalar(
        select(LeadIdentifier).where(
            LeadIdentifier.workspace_id == workspace_id,
            LeadIdentifier.type == IdentifierType.IG_USERNAME,
            LeadIdentifier.value == username.lower(),
        )
    )
    return row.lead if row else None


def sync_messages(
    session: Session,
    workspace: Workspace,
    client: GraphClient,
    page_id: str,
    own_username: str | None,
    classifier: ReplyClassifier,
    country: str,
    result: SyncResult,
    max_conversations: int = 25,
    updated_after: str | None = None,
) -> None:
    """Lee los DM de Instagram: registra respuestas de leads y crea leads con quien escribió a CMR.

    Con `updated_after` (ISO de Meta) solo abre los chats con mensajes nuevos: ahorra consultas."""
    page_token = None
    try:
        page_token = client.get(page_id, fields="access_token").get("access_token")
    except InstagramApiError:
        page_token = None  # el token configurado ya puede ser de la página
    conversations = client.get(
        f"{page_id}/conversations",
        token=page_token,
        platform="instagram",
        fields="id,updated_time,participants",
        limit=max_conversations,
    )
    own = (own_username or "").lower()
    for conversation in conversations.get("data", [])[:max_conversations]:
        participants = (conversation.get("participants") or {}).get("data", [])
        other = next((p for p in participants if (p.get("username") or "").lower() != own), None)
        if not other or not other.get("username"):
            continue
        username = other["username"].lower()
        if updated_after and (conversation.get("updated_time") or "") <= updated_after:
            continue  # sin mensajes nuevos desde la última revisión
        detail = client.get(
            conversation["id"],
            token=page_token,
            fields="messages.limit(20){id,created_time,from,message}",
        )
        messages = sorted(
            (detail.get("messages") or {}).get("data", []), key=lambda m: m.get("created_time", "")
        )
        if not messages:
            continue
        result.conversations += 1

        lead = _lead_by_username(session, workspace.id, username)
        if lead is None:
            # Nos escribió alguien que no estaba en la base: es un lead caliente.
            if (messages[0].get("from") or {}).get("username", "").lower() == own:
                continue  # chat iniciado por CMR con alguien que no es lead: no se guarda
            lead = ingest(
                session,
                workspace,
                SourceRecord(
                    profile=RawProfile(instagram_username=username),
                    source=SourceType.INSTAGRAM_DM,
                    source_ref="mensaje directo",
                ),
                country,
            ).lead
            result.new_leads += 1

        # Agrupa mensajes seguidos del mismo lado, igual que con los chats de WhatsApp.
        blocks: list[tuple[str, datetime | None, list[str]]] = []
        for message in messages:
            text = (message.get("message") or "").strip()
            if not text:
                continue
            side = (
                "outbound"
                if (message.get("from") or {}).get("username", "").lower() == own
                else "inbound"
            )
            if blocks and blocks[-1][0] == side:
                blocks[-1][2].append(text)
            else:
                blocks.append((side, _parse_time(message.get("created_time")), [text]))

        has_outbound = any(i.direction == "outbound" for i in lead.interactions)
        for side, at, texts in blocks:
            text = "\n".join(texts)
            if side == "outbound":
                if not has_outbound and not _already_registered(lead, "outbound", text):
                    lead.interactions.append(
                        Interaction(
                            channel=Channel.INSTAGRAM_DM,
                            direction="outbound",
                            message=text,
                            created_at=at or utcnow(),
                        )
                    )
                    lead.contacted_at = lead.contacted_at or at
                    if lead.status in (LeadStatus.NEW, LeadStatus.APPROVED):
                        set_status(session, lead, LeadStatus.CONTACTED, "Detectado en Instagram")
                    has_outbound = True
                continue
            if (
                register_reply(session, lead, text, classifier, Channel.INSTAGRAM_DM, at)
                is not None
            ):
                result.replies += 1
    session.flush()


def sync_instagram(
    session: Session,
    workspace: Workspace,
    client: GraphClient,
    ig_user_id: str,
    page_id: str | None,
    classifier: ReplyClassifier,
    country: str,
    posts_limit: int = 25,
    include_posts: bool = True,
) -> SyncResult:
    result = SyncResult()
    try:
        sync_account(session, workspace, client, ig_user_id, result)
        if include_posts:
            sync_posts(session, workspace, client, ig_user_id, posts_limit, result)
        if page_id:
            own = None
            snapshot = session.scalar(
                select(InstagramAccountSnapshot)
                .where(InstagramAccountSnapshot.workspace_id == workspace.id)
                .order_by(InstagramAccountSnapshot.day.desc())
            )
            if snapshot and snapshot.raw:
                own = snapshot.raw.get("username")
            try:
                sync_messages(session, workspace, client, page_id, own, classifier, country, result)
            except InstagramApiError as exc:
                if isinstance(exc, RateLimitedError | AuthError):
                    raise
                result.warnings.append(
                    f"mensajes: {exc} (¿falta el permiso instagram_manage_messages?)"
                )
        else:
            result.warnings.append("mensajes: falta FACEBOOK_PAGE_ID")
    except BudgetExceeded as exc:
        result.stopped_reason = str(exc)
    except RateLimitedError as exc:
        result.stopped_reason = f"rate limit de Meta: {exc}"
    except AuthError as exc:
        result.stopped_reason = str(exc)
    except InstagramApiError as exc:
        result.stopped_reason = str(exc)
    result.requests = client.requests_made
    session.flush()
    return result


def daily_budget_guard(budget):
    def guard() -> None:
        if budget.remaining() <= 0:
            raise BudgetExceeded(f"tope diario de consultas a Meta ({budget.limit})")
        budget.consume()

    return guard

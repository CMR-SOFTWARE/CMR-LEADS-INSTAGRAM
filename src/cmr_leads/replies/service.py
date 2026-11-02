"""Registrar respuestas de leads (pegadas o desde un chat exportado) y medir qué funciona."""

import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from cmr_leads.db.models import Interaction, Lead, utcnow
from cmr_leads.domain.enums import Channel, LeadStatus
from cmr_leads.replies.classifier import POSITIVE, ReplyAnalysis, ReplyClassifier
from cmr_leads.replies.whatsapp_export import contact_from_filename, parse_chat, split_sides
from cmr_leads.services import mark_do_not_contact, set_status

FOLLOW_UP_LATER_DAYS = 60
FOLLOW_UP_AUTO_REPLY_DAYS = 3


def _fingerprint(text: str) -> str:
    return hashlib.sha1(" ".join(text.split()).lower().encode()).hexdigest()[:16]


def _context(lead: Lead) -> dict:
    return {
        "negocio": lead.label,
        "rubro": lead.business_type,
        "ciudad": lead.city,
        "servicio ofrecido": lead.recommended_service,
    }


def _already_registered(lead: Lead, direction: str, text: str) -> bool:
    fp = _fingerprint(text)
    return any(
        i.direction == direction and i.message and _fingerprint(i.message) == fp
        for i in lead.interactions
    )


def apply_analysis(session: Session, lead: Lead, analysis: ReplyAnalysis) -> None:
    """Actualiza estado y seguimiento según lo que respondió."""
    category = analysis.category
    if category == "no_contactar":
        mark_do_not_contact(session, lead, "Pidió no recibir más mensajes")
        return
    if category == "automatica":
        lead.next_follow_up_at = utcnow() + timedelta(days=FOLLOW_UP_AUTO_REPLY_DAYS)
        return
    if lead.status in (LeadStatus.NEW, LeadStatus.APPROVED, LeadStatus.CONTACTED):
        set_status(session, lead, LeadStatus.REPLIED, analysis.summary or None)
    if category == "no_interesa":
        set_status(session, lead, LeadStatus.LOST, "No le interesa")
        lead.next_follow_up_at = None
    elif category == "ahora_no":
        lead.next_follow_up_at = utcnow() + timedelta(days=FOLLOW_UP_LATER_DAYS)
    elif category in POSITIVE:
        lead.next_follow_up_at = utcnow()  # responder hoy


def register_reply(
    session: Session,
    lead: Lead,
    text: str,
    classifier: ReplyClassifier,
    channel: Channel = Channel.WHATSAPP,
    at: datetime | None = None,
) -> Interaction | None:
    text = text.strip()
    if not text or _already_registered(lead, "inbound", text):
        return None
    analysis = classifier.classify(text, _context(lead))
    interaction = Interaction(
        channel=channel,
        direction="inbound",
        message=text,
        outcome=analysis.category,
        analysis=analysis.to_json(),
        created_at=at or utcnow(),
    )
    lead.interactions.append(interaction)
    apply_analysis(session, lead, analysis)
    session.flush()
    return interaction


@dataclass
class ChatImportResult:
    replies: int = 0
    outbound: int = 0
    my_name: str | None = None
    error: str | None = None
    last_category: str | None = None


def import_whatsapp_chat(
    session: Session,
    lead: Lead,
    content: str,
    classifier: ReplyClassifier,
    filename: str | None = None,
    my_name: str | None = None,
) -> ChatImportResult:
    result = ChatImportResult()
    messages = parse_chat(content)
    if not messages:
        result.error = "No se encontraron mensajes. ¿Es el .txt de 'Exportar chat'?"
        return result
    me = split_sides(messages, my_name, contact_from_filename(filename))
    if me is None:
        senders = ", ".join(dict.fromkeys(m.sender for m in messages))
        result.error = f"No pude saber cuál sos vos. Escribí tu nombre tal como aparece en el chat ({senders})."
        return result
    result.my_name = me

    # Desde mi primer mensaje en adelante; agrupa mensajes seguidos del mismo lado.
    first_mine = next((i for i, m in enumerate(messages) if m.sender == me), None)
    if first_mine is None:
        result.error = "En el chat no hay mensajes tuyos."
        return result
    blocks: list[tuple[str, datetime, list[str]]] = []
    for message in messages[first_mine:]:
        side = "outbound" if message.sender == me else "inbound"
        if blocks and blocks[-1][0] == side:
            blocks[-1][2].append(message.text)
        else:
            blocks.append((side, message.at, [message.text]))

    has_outbound = any(i.direction == "outbound" for i in lead.interactions)
    for side, at, texts in blocks:
        text = "\n".join(texts)
        if side == "outbound":
            if not has_outbound and not _already_registered(lead, "outbound", text):
                lead.interactions.append(
                    Interaction(
                        channel=Channel.WHATSAPP, direction="outbound", message=text, created_at=at
                    )
                )
                lead.contacted_at = lead.contacted_at or at
                if lead.status in (LeadStatus.NEW, LeadStatus.APPROVED):
                    set_status(session, lead, LeadStatus.CONTACTED, "Importado del chat")
                has_outbound = True
                result.outbound += 1
            continue
        interaction = register_reply(session, lead, text, classifier, Channel.WHATSAPP, at)
        if interaction is not None:
            result.replies += 1
            result.last_category = interaction.outcome
    session.flush()
    return result


# ---------------- Estadísticas ----------------


@dataclass
class Bucket:
    contacted: int = 0
    replied: int = 0
    positive: int = 0

    @property
    def reply_rate(self) -> float | None:
        return self.replied / self.contacted if self.contacted else None

    @property
    def positive_rate(self) -> float | None:
        return self.positive / self.contacted if self.contacted else None


@dataclass
class ReplyStats:
    contacted: int = 0
    replied: int = 0
    positive: int = 0
    categories: list[tuple[str, int]] = field(default_factory=list)
    objections: list[tuple[str, int]] = field(default_factory=list)
    median_hours: float | None = None
    by: dict[str, list[tuple[str, Bucket]]] = field(default_factory=dict)
    recent: list[tuple[Lead, Interaction]] = field(default_factory=list)

    @property
    def reply_rate(self) -> float | None:
        return self.replied / self.contacted if self.contacted else None


def reply_stats(session: Session, workspace_id: int) -> ReplyStats:
    stats = ReplyStats()
    leads = session.scalars(
        select(Lead).where(
            Lead.workspace_id == workspace_id,
            Lead.contacted_at.is_not(None) | Lead.interactions.any(),
        )
    ).all()
    dims = {
        "template": "Mensaje",
        "service": "Servicio ofrecido",
        "business_type": "Rubro",
        "source": "Fuente",
        "channel": "Canal",
    }
    groups: dict[str, dict[str, Bucket]] = {d: defaultdict(Bucket) for d in dims}
    categories: Counter[str] = Counter()
    objections: Counter[str] = Counter()
    hours: list[float] = []
    recent: list[tuple[Lead, Interaction]] = []

    for lead in leads:
        outbound = [i for i in lead.interactions if i.direction == "outbound"]
        inbound = [i for i in lead.interactions if i.direction == "inbound"]
        first_out = min(outbound, key=lambda i: i.created_at) if outbound else None
        human_replies = [i for i in inbound if i.outcome != "automatica"]
        replied = bool(human_replies)
        positive = any(i.outcome in POSITIVE for i in human_replies)
        stats.contacted += 1
        stats.replied += replied
        stats.positive += positive
        for i in inbound:
            categories[i.outcome or "otro"] += 1
            objections.update((i.analysis or {}).get("objections", []))
            recent.append((lead, i))
        if replied and first_out:
            first_reply = min(human_replies, key=lambda i: i.created_at)
            delta = (first_reply.created_at - first_out.created_at).total_seconds() / 3600
            if delta >= 0:
                hours.append(delta)
        keys = {
            "template": (first_out.template if first_out else None) or "sin registrar",
            "service": lead.recommended_service or "—",
            "business_type": lead.business_type or "sin rubro",
            "source": (lead.sources[0].source if lead.sources else "—"),
            "channel": (first_out.channel if first_out else None) or "—",
        }
        for dim, key in keys.items():
            bucket = groups[dim][key]
            bucket.contacted += 1
            bucket.replied += replied
            bucket.positive += positive

    stats.categories = categories.most_common()
    stats.objections = objections.most_common()
    stats.median_hours = median(hours) if hours else None
    stats.by = {
        label: sorted(
            groups[dim].items(), key=lambda kv: (-(kv[1].positive_rate or 0), -kv[1].contacted)
        )
        for dim, label in dims.items()
    }
    stats.recent = sorted(recent, key=lambda li: li[1].created_at, reverse=True)[:15]
    return stats


def build_classifier(settings) -> ReplyClassifier:
    if settings.reply_ai_configured:
        from cmr_leads.replies.ai_classifier import ClaudeReplyClassifier

        return ReplyClassifier(ClaudeReplyClassifier(settings.anthropic_api_key, settings.ai_model))
    return ReplyClassifier()

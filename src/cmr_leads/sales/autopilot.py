"""Piloto automático del embudo: hace solo lo repetitivo y deja todo deshacible.

- Califica solos a los leads nuevos más prometedores (por chance de la IA o por puntaje).
- Cierra como "Perdido — sin respuesta" a los que no contestaron tras el último seguimiento.
- Arma la cola de "Mi jornada": revisar → enviar → seguimientos → respuestas → cerrar.

Cada acción automática queda marcada en `raw_data["auto"]` con su fecha, para mostrarla y
poder deshacerla con un botón.
"""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session

from cmr_leads.db.models import Lead
from cmr_leads.domain.enums import LeadStatus, LostReason
from cmr_leads.pipeline.normalize import whatsapp_chat_link
from cmr_leads.sales.config import SalesConfig
from cmr_leads.sales.pipeline import change_stage, follow_up_alert
from cmr_leads.sales.repository import SalesRepository
from cmr_leads.services import set_status

AUTO_QUALIFIED = "calificado"
AUTO_CLOSED = "cerrado"


def _auto(lead: Lead) -> dict:
    return (lead.raw_data or {}).get("auto") or {}


def _mark(lead: Lead, kind: str, now: datetime) -> None:
    auto = {**_auto(lead), kind: now.isoformat(timespec="seconds")}
    lead.raw_data = {**(lead.raw_data or {}), "auto": auto}


def _reachable(lead: Lead) -> bool:
    """Tiene por dónde escribirle: WhatsApp (número o link corto), Instagram o email."""
    return bool(
        lead.whatsapp_e164
        or whatsapp_chat_link(lead.website, lead.bio)
        or lead.instagram_username
        or lead.email
    )


def auto_flag(lead: Lead, kind: str) -> str | None:
    return _auto(lead).get(kind)


def qualifies(lead: Lead, config: SalesConfig) -> bool:
    if lead.excluded or lead.do_not_contact or not _reachable(lead):
        return False
    if lead.ai_score is not None and config.auto_calificar_desde_ia:
        return lead.ai_score >= config.auto_calificar_desde_ia
    return bool(
        config.auto_calificar_desde_score
        and (lead.business_score or 0) >= config.auto_calificar_desde_score
    )


def auto_qualify(
    session: Session, repo: SalesRepository, config: SalesConfig, now: datetime
) -> list[Lead]:
    limit = config.auto_calificar_max_por_dia
    if limit <= 0:
        return []
    today = now.date().isoformat()
    done_today = sum(
        1
        for lead in repo.leads_in_stages([LeadStatus.APPROVED, LeadStatus.CONTACTED])
        if (auto_flag(lead, AUTO_QUALIFIED) or "").startswith(today)
    )
    qualified = []
    for lead in repo.leads_in_stages([LeadStatus.NEW]):
        if done_today + len(qualified) >= limit:
            break
        if auto_flag(lead, AUTO_QUALIFIED) is None and qualifies(lead, config):
            set_status(session, lead, LeadStatus.APPROVED, "calificado automático (piloto)")
            _mark(lead, AUTO_QUALIFIED, now)
            qualified.append(lead)
    return qualified


def auto_close(
    session: Session, repo: SalesRepository, config: SalesConfig, now: datetime
) -> list[Lead]:
    if not config.auto_cerrar_sin_respuesta:
        return []
    closed = []
    for lead in repo.leads_in_stages([LeadStatus.CONTACTED]):
        alert = follow_up_alert(lead, now)
        replied = any(i.direction == "inbound" for i in lead.interactions)
        if alert and alert.kind == "cerrar" and not replied:
            change_stage(
                session,
                lead,
                LeadStatus.LOST,
                lost_reason=LostReason.NO_RESPONSE,
                note="cerrado automático: sin respuesta después de los seguimientos",
            )
            _mark(lead, AUTO_CLOSED, now)
            closed.append(lead)
    return closed


def undo(session: Session, lead: Lead, now: datetime) -> str:
    """Deshace lo último que hizo el piloto con este lead."""
    auto = _auto(lead)
    if AUTO_CLOSED in auto and lead.status == LeadStatus.LOST:
        set_status(session, lead, LeadStatus.CONTACTED, "reabierto a mano")
        lead.lost_reason = None
        lead.next_follow_up_at = now
        lead.next_step = "Reabierto: decidí si le escribís de nuevo"
        auto.pop(AUTO_CLOSED)
        msg = f"{lead.label} reabierto"
    elif AUTO_QUALIFIED in auto and lead.status == LeadStatus.APPROVED:
        set_status(session, lead, LeadStatus.NEW, "calificación automática deshecha")
        auto[AUTO_QUALIFIED] = "deshecho"  # no lo vuelve a calificar solo
        msg = f"{lead.label} volvió a Nuevo"
    else:
        return "No hay nada automático para deshacer en este lead"
    lead.raw_data = {**(lead.raw_data or {}), "auto": auto}
    session.flush()
    return msg


@dataclass
class AutopilotResult:
    qualified: list[Lead] = field(default_factory=list)
    closed: list[Lead] = field(default_factory=list)


def run_autopilot(
    session: Session, repo: SalesRepository, config: SalesConfig, now: datetime
) -> AutopilotResult:
    result = AutopilotResult(
        closed=auto_close(session, repo, config, now),
        qualified=auto_qualify(session, repo, config, now),
    )
    session.flush()
    return result


# ---------------- Mi jornada ----------------

STEPS = [
    ("revisar", "Revisar", "Decidí si cada negocio vale la pena"),
    ("enviar", "Enviar", "Mandá el primer mensaje"),
    ("seguimiento", "Seguimientos", "Volvé a escribirle a quien no contestó"),
    ("responder", "Responder", "Contestale a quien te escribió"),
    ("cerrar", "Cerrar", "Reuniones y propuestas abiertas"),
]


@dataclass
class Journey:
    queues: dict[str, list] = field(default_factory=dict)

    def count(self, step: str) -> int:
        return len(self.queues.get(step, []))

    @property
    def current(self) -> str | None:
        return next((key for key, *_ in STEPS if self.queues.get(key)), None)


def build_journey(
    repo: SalesRepository, config: SalesConfig, now: datetime, skip: set[int]
) -> Journey:
    def keep(leads):
        return [lead for lead in leads if lead.id not in skip and not lead.excluded]

    new = [lead for lead in keep(repo.leads_in_stages([LeadStatus.NEW])) if _reachable(lead)]
    to_send = [
        lead
        for lead in keep(repo.leads_in_stages([LeadStatus.APPROVED]))
        if lead.contacted_at is None and _reachable(lead)
    ]
    follow = [
        alert
        for lead in keep(repo.leads_in_stages([LeadStatus.CONTACTED]))
        if (alert := follow_up_alert(lead, now)) and alert.kind == "seguimiento"
    ]
    respond = [
        lead
        for lead in keep(repo.leads_in_stages([LeadStatus.REPLIED]))
        if lead.next_follow_up_at is None or lead.next_follow_up_at <= now
    ]
    close = [
        alert
        for lead in keep(repo.leads_in_stages([LeadStatus.MEETING, LeadStatus.PROPOSAL]))
        if (alert := follow_up_alert(lead, now))
    ]
    return Journey(
        {
            "revisar": new,
            "enviar": to_send,
            "seguimiento": follow,
            "responder": respond,
            "cerrar": close,
        }
    )

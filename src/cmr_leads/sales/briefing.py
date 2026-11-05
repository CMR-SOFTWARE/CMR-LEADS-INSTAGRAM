"""Briefing diario: a quién escribirle hoy, seguimientos vencidos, negocios trabados y agenda."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from cmr_leads.db.models import Lead, Meeting
from cmr_leads.domain.enums import LeadStatus
from cmr_leads.sales.config import SalesConfig
from cmr_leads.sales.pipeline import Alert, follow_up_alert, stuck_alert
from cmr_leads.sales.repository import ACTIVE_STAGES, SalesRepository


@dataclass
class Briefing:
    to_contact: list[Lead] = field(default_factory=list)
    to_qualify: list[Lead] = field(default_factory=list)
    follow_ups: list[Alert] = field(default_factory=list)
    stuck: list[Alert] = field(default_factory=list)
    meetings: list[Meeting] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (
            self.to_contact or self.to_qualify or self.follow_ups or self.stuck or self.meetings
        )


def _reachable(lead: Lead) -> bool:
    return bool(lead.whatsapp_e164 or lead.instagram_username or lead.email or lead.phone_e164)


def build_briefing(repo: SalesRepository, config: SalesConfig, now: datetime) -> Briefing:
    briefing = Briefing()
    size = config.briefing_size
    qualified = [
        lead
        for lead in repo.leads_in_stages([LeadStatus.APPROVED])
        if lead.contacted_at is None and _reachable(lead) and not lead.excluded
    ]
    briefing.to_contact = qualified[:size]
    if len(briefing.to_contact) < size:
        new = [
            lead
            for lead in repo.leads_in_stages([LeadStatus.NEW])
            if _reachable(lead) and not lead.excluded
        ]
        briefing.to_qualify = new[: size - len(briefing.to_contact)]

    seen: set[int] = set()
    for lead in repo.leads_in_stages(list(ACTIVE_STAGES)):
        if alert := follow_up_alert(lead, now):
            briefing.follow_ups.append(alert)
            seen.add(lead.id)
    for lead in repo.leads_in_stages(list(ACTIVE_STAGES)):
        if lead.id not in seen and (alert := stuck_alert(lead, now, config)):
            briefing.stuck.append(alert)
    briefing.follow_ups.sort(key=lambda a: -a.days)
    briefing.stuck.sort(key=lambda a: -a.days)

    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    briefing.meetings = repo.meetings_between(today, today + timedelta(days=7))
    return briefing

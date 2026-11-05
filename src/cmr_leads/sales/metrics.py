"""Métricas del embudo calculadas SOLO con datos reales de la base (nunca números de ejemplo).

- Actividad del período: cuántos leads llegaron por PRIMERA vez a cada etapa dentro del período
  (según el historial de cambios de estado), reuniones, propuestas e ingresos ganados.
- Embudo: de los leads encontrados en el período, cuántos llegaron a cada etapa. Como cada lead
  cuenta en todas las etapas que alcanzó, los números bajan etapa a etapa y las conversiones cierran.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from cmr_leads.db.models import Lead
from cmr_leads.domain.enums import LeadStatus
from cmr_leads.sales.pipeline import FUNNEL, LOST_REASON_LABELS, STAGE_LABELS, stage_index
from cmr_leads.sales.repository import SalesRepository


def first_reach(lead: Lead) -> dict[int, datetime]:
    """Para cada índice de etapa del embudo, cuándo la alcanzó por primera vez."""
    reached: dict[int, datetime] = {0: lead.first_seen_at}
    for change in lead.status_changes:
        index = stage_index(change.to_status)
        for i in range(1, index + 1):
            if i not in reached or change.changed_at < reached[i]:
                reached[i] = change.changed_at
    # Leads cargados ya en una etapa avanzada (sin historial): cuenta la etapa actual.
    current = stage_index(lead.status)
    for i in range(1, current + 1):
        reached.setdefault(i, lead.stage_entered_at or lead.first_seen_at)
    return reached


def max_stage(lead: Lead) -> int:
    return max(first_reach(lead))


@dataclass
class FunnelStep:
    stage: str
    label: str
    count: int
    rate: float | None  # conversión desde la etapa anterior


@dataclass
class Breakdown:
    name: str
    leads: int = 0
    contacted: int = 0
    replied: int = 0
    won: int = 0

    @property
    def reply_rate(self) -> float | None:
        return self.replied / self.contacted if self.contacted else None

    @property
    def win_rate(self) -> float | None:
        return self.won / self.contacted if self.contacted else None


@dataclass
class SalesMetrics:
    start: datetime | None
    end: datetime
    new_leads: int = 0
    qualified: int = 0
    contacted: int = 0
    replied: int = 0
    reply_rate: float | None = None
    meetings: int = 0
    proposals: int = 0
    won: int = 0
    won_ars: int = 0
    funnel: list[FunnelStep] = field(default_factory=list)
    by_source: list[Breakdown] = field(default_factory=list)
    by_rubro: list[Breakdown] = field(default_factory=list)
    lost_reasons: list[tuple[str, int]] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.new_leads or self.contacted or self.meetings or self.proposals or self.won)


IDX = {stage: stage_index(stage) for stage in FUNNEL}
SOURCE_NAMES = {
    "google_places": "Google Maps",
    "osm": "OpenStreetMap",
    "csv_import": "CSV",
    "manual": "Carga manual",
    "cmr_following": "Seguidos de CMR",
    "cmr_followers": "Seguidores de CMR",
    "instagram_dm": "Instagram (DM)",
}


def _in(when: datetime | None, start: datetime | None, end: datetime) -> bool:
    return when is not None and (start is None or when >= start) and when < end


def compute_metrics(repo: SalesRepository, start: datetime | None, end: datetime) -> SalesMetrics:
    metrics = SalesMetrics(start=start, end=end)
    leads = repo.all_leads()
    sources = repo.sources_by_lead()
    contacted_idx, replied_idx = IDX[LeadStatus.CONTACTED], IDX[LeadStatus.REPLIED]

    cohort: list[tuple[Lead, dict[int, datetime]]] = []
    contacted_in_period: list[dict[int, datetime]] = []
    lost: Counter[str] = Counter()
    by_source: dict[str, Breakdown] = {}
    by_rubro: dict[str, Breakdown] = {}

    for lead in leads:
        reach = first_reach(lead)
        if _in(lead.first_seen_at, start, end):
            cohort.append((lead, reach))
        if _in(reach.get(IDX[LeadStatus.APPROVED]), start, end):
            metrics.qualified += 1
        if _in(reach.get(contacted_idx), start, end):
            metrics.contacted += 1
            contacted_in_period.append(reach)
        if _in(reach.get(replied_idx), start, end):
            metrics.replied += 1
        if _in(reach.get(IDX[LeadStatus.WON]), start, end):
            metrics.won += 1
            metrics.won_ars += lead.deal_amount_ars or 0
        if lead.status == LeadStatus.LOST and lead.lost_reason:
            lost_at = max(
                (c.changed_at for c in lead.status_changes if c.to_status == LeadStatus.LOST),
                default=None,
            )
            if _in(lost_at, start, end):
                lost[lead.lost_reason] += 1

    metrics.new_leads = len(cohort)
    if contacted_in_period:
        answered = sum(1 for r in contacted_in_period if replied_idx in r)
        metrics.reply_rate = answered / len(contacted_in_period)
    metrics.meetings = len(repo.meetings_between(start or datetime.min, end))
    metrics.proposals = len(repo.proposals_between(start or datetime.min, end))

    previous = None
    for stage in FUNNEL:
        count = sum(1 for _, reach in cohort if IDX[stage] in reach)
        rate = count / previous if previous else None
        metrics.funnel.append(FunnelStep(stage, STAGE_LABELS[stage], count, rate))
        previous = count

    for lead, reach in cohort:
        rubro = lead.category or lead.business_type or "Sin rubro"
        groups = [by_rubro.setdefault(rubro, Breakdown(rubro))]
        for source in dict.fromkeys(sources.get(lead.id, [])):
            name = SOURCE_NAMES.get(source, source)
            groups.append(by_source.setdefault(name, Breakdown(name)))
        for group in groups:
            group.leads += 1
            group.contacted += contacted_idx in reach
            group.replied += replied_idx in reach
            group.won += IDX[LeadStatus.WON] in reach

    def ranked(groups: dict[str, Breakdown]) -> list[Breakdown]:
        return sorted(groups.values(), key=lambda g: (-g.won, -g.replied, -g.contacted, -g.leads))

    metrics.by_source = ranked(by_source)
    metrics.by_rubro = ranked(by_rubro)[:12]
    metrics.lost_reasons = [
        (LOST_REASON_LABELS.get(reason, reason), count) for reason, count in lost.most_common()
    ]
    return metrics

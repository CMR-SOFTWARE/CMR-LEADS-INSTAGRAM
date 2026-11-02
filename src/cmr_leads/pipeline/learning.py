"""Aprendizaje a partir de las decisiones humanas.

Cada lead revisado queda etiquetado (bueno / malo) por el feedback explícito o por su estado.
Se mide, para cada regla del scoring, si los leads que la cumplen terminan siendo buenos más o
menos seguido que el promedio, y se sugiere subir o bajar su peso. No cambia nada solo: las
sugerencias se aplican desde el panel.
"""

from collections import Counter
from dataclasses import dataclass, field

from cmr_leads.db.models import Lead
from cmr_leads.domain.enums import LeadStatus
from cmr_leads.pipeline.scoring import ScoringConfig, get_weight

POSITIVE_STATUSES = {
    LeadStatus.APPROVED,
    LeadStatus.CONTACTED,
    LeadStatus.REPLIED,
    LeadStatus.MEETING,
    LeadStatus.WON,
}
NEGATIVE_STATUSES = {LeadStatus.REJECTED, LeadStatus.LOST}

MIN_LABELED = 10  # leads revisados necesarios para sugerir cambios
MIN_SAMPLES = 4  # leads con la regla necesarios para opinar sobre ella
LIFT_THRESHOLD = 0.15
STEP = 5
MAX_WEIGHT = 40

RULE_LABELS = {
    "fit.business_account": "Cuenta profesional de Instagram",
    "fit.category": "Tiene categoría comercial",
    "fit.business_keywords_points": "Palabras comerciales en la bio",
    "fit.business_type_points": "Rubro identificado",
    "fit.local_points": "Es local (zona de CMR)",
    "need.no_website": "No tiene sitio web",
    "need.link_in_bio_only": "Solo link en bio / redes",
    "need.website_unreachable": "Sitio caído",
    "need.website_no_https": "Sitio sin HTTPS",
    "need.website_not_mobile": "Sitio no apto celular",
    "need.sells_by_dm_points": "Vende o toma pedidos por mensaje",
    "need.appointments_points": "Trabaja con turnos / reservas",
    "reach.whatsapp_confirmed": "WhatsApp publicado",
    "reach.phone": "Teléfono publicado",
    "reach.email": "Email publicado",
    "reach.instagram_only": "Solo contacto por Instagram",
}

FEEDBACK_REASONS = {
    "buen_perfil": "Buen perfil",
    "no_es_negocio": "No es un negocio",
    "no_es_local": "No es de la zona",
    "no_necesita": "No necesita lo que ofrecemos",
    "muy_grande": "Cadena / muy grande",
    "competidor": "Competidor",
    "ya_es_cliente": "Ya es cliente",
    "otro": "Otro motivo",
}


def lead_label(lead: Lead) -> int | None:
    """1 = buen lead, -1 = mal lead, None = sin revisar.

    Prioridad: feedback explícito > lo que respondió el lead > estado."""
    from cmr_leads.replies.classifier import NEGATIVE, POSITIVE

    if lead.feedback in (1, -1):
        return lead.feedback
    outcomes = {i.outcome for i in lead.interactions if i.direction == "inbound"}
    if outcomes & POSITIVE:
        return 1
    if outcomes & NEGATIVE:
        return -1
    if lead.status in POSITIVE_STATUSES:
        return 1
    if lead.status in NEGATIVE_STATUSES:
        return -1
    return None


@dataclass
class RuleInsight:
    rule: str
    label: str
    weight: int
    samples: int = 0
    positives: int = 0
    precision: float | None = None
    lift: float | None = None
    suggested_weight: int | None = None

    @property
    def suggestion(self) -> int:
        return 0 if self.suggested_weight is None else self.suggested_weight - self.weight


@dataclass
class LearningReport:
    labeled: int = 0
    positives: int = 0
    negatives: int = 0
    accuracy: float | None = None
    insights: list[RuleInsight] = field(default_factory=list)
    false_positives: list[Lead] = field(default_factory=list)
    false_negatives: list[Lead] = field(default_factory=list)
    rejection_reasons: list[tuple[str, int]] = field(default_factory=list)

    @property
    def base_rate(self) -> float | None:
        return self.positives / self.labeled if self.labeled else None

    @property
    def enough_data(self) -> bool:
        return self.labeled >= MIN_LABELED

    @property
    def suggestions(self) -> list[RuleInsight]:
        return [i for i in self.insights if i.suggestion]


def analyze(leads: list[Lead], config: ScoringConfig) -> LearningReport:
    report = LearningReport()
    threshold = config.thresholds.get("posible", 40)
    labeled: list[tuple[Lead, int]] = []
    reasons: Counter[str] = Counter()
    for lead in leads:
        label = lead_label(lead)
        if label is None:
            continue
        labeled.append((lead, label))
        if label == -1 and lead.feedback_reason:
            reasons[lead.feedback_reason] += 1

    report.labeled = len(labeled)
    report.positives = sum(1 for _, label in labeled if label == 1)
    report.negatives = report.labeled - report.positives
    report.rejection_reasons = reasons.most_common()

    hits = 0
    for lead, label in labeled:
        predicted_good = not lead.excluded and (lead.business_score or 0) >= threshold
        if predicted_good == (label == 1):
            hits += 1
        elif predicted_good:
            report.false_positives.append(lead)
        else:
            report.false_negatives.append(lead)
    if labeled:
        report.accuracy = hits / len(labeled)
    report.false_positives.sort(key=lambda lead: -(lead.business_score or 0))
    report.false_negatives.sort(key=lambda lead: lead.business_score or 0)

    base_rate = report.base_rate
    for rule, label in RULE_LABELS.items():
        insight = RuleInsight(rule=rule, label=label, weight=get_weight(config, rule))
        with_rule = [
            lbl
            for lead, lbl in labeled
            if any(item.get("rule") == rule for item in (lead.score_breakdown or []))
        ]
        insight.samples = len(with_rule)
        insight.positives = sum(1 for lbl in with_rule if lbl == 1)
        if with_rule and base_rate is not None:
            insight.precision = insight.positives / insight.samples
            insight.lift = insight.precision - base_rate
            if report.enough_data and insight.samples >= MIN_SAMPLES:
                if insight.lift >= LIFT_THRESHOLD:
                    insight.suggested_weight = min(insight.weight + STEP, MAX_WEIGHT)
                elif insight.lift <= -LIFT_THRESHOLD:
                    insight.suggested_weight = max(insight.weight - STEP, 0)
                if insight.suggested_weight == insight.weight:
                    insight.suggested_weight = None
        report.insights.append(insight)
    return report

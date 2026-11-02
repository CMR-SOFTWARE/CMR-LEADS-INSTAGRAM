"""Scoring por reglas configurables (config/scoring.yaml).

Estructura: exclusiones duras -> FIT + NEED + REACH (0-100) -> prioridad.
Cada punto queda registrado en el desglose para que la revisión humana entienda el porqué.
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from cmr_leads.db.models import Lead
from cmr_leads.domain.enums import EnrichmentState, Priority
from cmr_leads.pipeline.normalize import normalize_text, normalize_username, phone_key


class Exclusions(BaseModel):
    usernames: list[str] = Field(default_factory=list)
    competitor_keywords: list[str] = Field(default_factory=list)
    institutional_keywords: list[str] = Field(default_factory=list)
    creator_keywords: list[str] = Field(default_factory=list)
    creator_categories: list[str] = Field(default_factory=list)


class FitRules(BaseModel):
    business_account: int = 15
    category: int = 10
    business_keywords_points: int = 10
    business_keywords: list[str] = Field(default_factory=list)
    business_type_points: int = 5
    local_points: int = 10


class LocalRules(BaseModel):
    city_keywords: list[str] = Field(default_factory=list)
    area_codes: list[str] = Field(default_factory=list)


class NeedRules(BaseModel):
    no_website: int = 20
    link_in_bio_only: int = 15
    website_unreachable: int = 10
    website_no_https: int = 5
    website_not_mobile: int = 5
    sells_by_dm_points: int = 10
    sells_by_dm_keywords: list[str] = Field(default_factory=list)
    appointments_points: int = 10
    appointments_keywords: list[str] = Field(default_factory=list)


class ReachRules(BaseModel):
    whatsapp_confirmed: int = 20
    phone: int = 12
    email: int = 8
    instagram_only: int = 3


class ScoringConfig(BaseModel):
    version: str = "1"
    thresholds: dict[str, int] = Field(default_factory=lambda: {"interesante": 70, "posible": 40})
    dimension_max: dict[str, int] = Field(
        default_factory=lambda: {"fit": 40, "need": 40, "reach": 20}
    )
    exclusions: Exclusions = Field(default_factory=Exclusions)
    fit: FitRules = Field(default_factory=FitRules)
    business_types: dict[str, list[str]] = Field(default_factory=dict)
    appointment_business_types: list[str] = Field(default_factory=list)
    nexo_business_types: list[str] = Field(default_factory=list)
    canchas_keywords: list[str] = Field(default_factory=list)
    store_business_types: list[str] = Field(default_factory=list)
    local: LocalRules = Field(default_factory=LocalRules)
    need: NeedRules = Field(default_factory=NeedRules)
    reach: ReachRules = Field(default_factory=ReachRules)


def local_overrides_path(path: Path) -> Path:
    """Ajustes hechos desde el panel (pesos aprendidos, exclusiones). No se versiona."""
    return path.with_name(f"{path.stem}.local{path.suffix}")


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def read_overrides(path: Path) -> dict:
    local = local_overrides_path(path)
    if not local.exists():
        return {}
    with open(local, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def save_overrides(path: Path, overrides: dict) -> None:
    with open(local_overrides_path(path), "w", encoding="utf-8") as fh:
        fh.write("# Generado por el panel de CMR Lead Finder. Se aplica sobre scoring.yaml.\n")
        yaml.safe_dump(overrides, fh, allow_unicode=True, sort_keys=True)
    load_scoring_config.cache_clear()


@lru_cache
def load_scoring_config(path: Path) -> ScoringConfig:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return ScoringConfig.model_validate(_deep_merge(data, read_overrides(path)))


def get_weight(config: ScoringConfig, rule: str) -> int:
    section, name = rule.split(".", 1)
    return int(getattr(getattr(config, section), name))


@dataclass
class ScoringInput:
    username: str | None = None
    display_name: str | None = None
    bio: str | None = None
    category: str | None = None
    city: str | None = None
    is_business_account: bool | None = None
    enrichment_state: str = EnrichmentState.PENDING
    has_website: bool = False
    link_in_bio_only: bool = False
    website_signals: dict[str, Any] | None = None
    phone_e164: str | None = None
    whatsapp_e164: str | None = None
    whatsapp_confirmed: bool = False
    email: str | None = None

    @classmethod
    def from_lead(cls, lead: Lead) -> "ScoringInput":
        return cls(
            username=lead.instagram_username,
            display_name=lead.display_name,
            bio=lead.bio,
            category=lead.category,
            city=lead.city,
            is_business_account=lead.is_business_account,
            enrichment_state=lead.enrichment_state,
            has_website=bool(lead.website) and not lead.has_link_in_bio_only,
            link_in_bio_only=lead.has_link_in_bio_only,
            website_signals=(lead.raw_data or {}).get("website"),
            phone_e164=lead.phone_e164,
            whatsapp_e164=lead.whatsapp_e164,
            whatsapp_confirmed=lead.whatsapp_confirmed,
            email=lead.email,
        )


@dataclass
class ScoreResult:
    total: int = 0
    fit: int = 0
    need: int = 0
    reach: int = 0
    priority: str = Priority.LOW
    excluded: bool = False
    exclusion_reason: str | None = None
    is_business: bool = False
    is_local: bool = False
    business_type: str | None = None
    recommended_service: str | None = None
    breakdown: list[dict[str, Any]] = field(default_factory=list)

    def add(self, rule: str, points: int, reason: str) -> None:
        """`rule` es la ruta del peso en la config (ej. 'need.no_website')."""
        if points:
            dimension = rule.split(".", 1)[0]
            self.breakdown.append(
                {"dimension": dimension, "rule": rule, "points": points, "reason": reason}
            )


def _contains_any(text: str, keywords: list[str]) -> str | None:
    """Devuelve la primera keyword presente como palabra/frase completa."""
    for keyword in keywords:
        kw = normalize_text(keyword)
        if kw and re.search(rf"(?<!\w){re.escape(kw)}(?!\w)", text):
            return keyword
    return None


def detect_business_type(text: str, config: ScoringConfig) -> str | None:
    for business_type, keywords in config.business_types.items():
        if _contains_any(text, keywords):
            return business_type
    return None


def score(data: ScoringInput, config: ScoringConfig) -> ScoreResult:
    result = ScoreResult()
    text = normalize_text(" ".join(filter(None, [data.display_name, data.bio, data.category])))
    username_text = (data.username or "").replace(".", " ").replace("_", " ")
    text_with_username = f"{text} {username_text}".strip()
    category = normalize_text(data.category)

    # --- Exclusiones ---
    excluded_usernames = {normalize_username(u) for u in config.exclusions.usernames}
    reason: str | None = None
    if data.username and data.username in excluded_usernames:
        reason = "excluido manualmente (cuenta propia, cliente o cadena)"
    elif kw := _contains_any(text, config.exclusions.competitor_keywords):
        reason = f"posible competidor ('{kw}')"
    elif kw := _contains_any(text, config.exclusions.institutional_keywords):
        reason = f"institucional ('{kw}')"
    elif category and _contains_any(category, config.exclusions.creator_categories):
        reason = f"creador de contenido (categoría '{data.category}')"
    elif kw := _contains_any(text, config.exclusions.creator_keywords):
        reason = f"creador de contenido ('{kw}')"
    elif data.enrichment_state == EnrichmentState.NOT_FOUND or data.is_business_account is False:
        reason = "no es cuenta comercial (personal o inexistente)"

    # --- FIT ---
    business_type = detect_business_type(text_with_username, config)
    result.business_type = business_type
    fit_rules = config.fit
    if data.is_business_account:
        result.add(
            "fit.business_account", fit_rules.business_account, "cuenta profesional de Instagram"
        )
    if data.category:
        result.add("fit.category", fit_rules.category, f"categoría: {data.category}")
    if kw := _contains_any(text, fit_rules.business_keywords):
        result.add(
            "fit.business_keywords_points",
            fit_rules.business_keywords_points,
            f"señal comercial ('{kw}')",
        )
    if business_type:
        result.add(
            "fit.business_type_points", fit_rules.business_type_points, f"rubro: {business_type}"
        )
    local_reason = None
    if data.city and _contains_any(normalize_text(data.city), config.local.city_keywords):
        local_reason = f"ciudad: {data.city}"
    elif kw := _contains_any(text, config.local.city_keywords):
        local_reason = f"menciona '{kw}'"
    else:
        for number in (data.whatsapp_e164, data.phone_e164):
            key = phone_key(number) or ""
            code = next((c for c in config.local.area_codes if key.startswith("54" + c)), None)
            if code:
                local_reason = f"característica {code}"
                break
    if local_reason:
        result.is_local = True
        result.add("fit.local_points", fit_rules.local_points, f"local ({local_reason})")

    # --- NEED ---
    need = config.need
    signals = data.website_signals or {}
    if data.link_in_bio_only:
        result.add(
            "need.link_in_bio_only",
            need.link_in_bio_only,
            "solo link en bio/redes, sin sitio propio",
        )
    elif not data.has_website:
        result.add("need.no_website", need.no_website, "sin sitio web")
    elif signals:
        if signals.get("reachable") is False:
            result.add(
                "need.website_unreachable", need.website_unreachable, "el sitio web no responde"
            )
        else:
            if signals.get("https") is False:
                result.add("need.website_no_https", need.website_no_https, "sitio sin HTTPS")
            if signals.get("mobile_viewport") is False:
                result.add(
                    "need.website_not_mobile",
                    need.website_not_mobile,
                    "sitio no adaptado a celular",
                )
    sells_by_dm = _contains_any(text, need.sells_by_dm_keywords)
    if sells_by_dm:
        result.add(
            "need.sells_by_dm_points",
            need.sells_by_dm_points,
            f"vende/recibe pedidos por mensaje ('{sells_by_dm}')",
        )
    appointments = _contains_any(text, need.appointments_keywords)
    if appointments:
        result.add(
            "need.appointments_points",
            need.appointments_points,
            f"maneja turnos/reservas ('{appointments}')",
        )

    # --- REACH ---
    reach = config.reach
    if data.whatsapp_e164 and data.whatsapp_confirmed:
        result.add("reach.whatsapp_confirmed", reach.whatsapp_confirmed, "WhatsApp publicado")
    elif data.phone_e164 or data.whatsapp_e164:
        result.add("reach.phone", reach.phone, "teléfono publicado")
    if data.email:
        result.add("reach.email", reach.email, "email publicado")
    if not (data.whatsapp_e164 or data.phone_e164 or data.email) and data.username:
        result.add("reach.instagram_only", reach.instagram_only, "solo contacto por Instagram")

    # --- Totales ---
    maxes = config.dimension_max
    for dim in ("fit", "need", "reach"):
        raw = sum(item["points"] for item in result.breakdown if item["dimension"] == dim)
        setattr(result, dim, max(0, min(raw, maxes.get(dim, 100))))
    result.total = result.fit + result.need + result.reach
    result.is_business = bool(
        data.is_business_account
        or data.category
        or business_type
        or _contains_any(text, fit_rules.business_keywords)
    )

    # Qué plan ofrecer (ver config/catalogo.md).
    web_needs_fix = any(
        i["dimension"] == "need" and "sitio" in i["reason"] for i in result.breakdown
    )
    sells_online = bool(data.has_website and (data.website_signals or {}).get("ecommerce"))
    if _contains_any(text_with_username, config.canchas_keywords):
        result.recommended_service = "cmr_canchas"
    elif business_type in config.nexo_business_types:
        result.recommended_service = "cmr_nexo"
    elif business_type in config.appointment_business_types or appointments:
        result.recommended_service = "sistema_turnos"
    elif (business_type in config.store_business_types or sells_by_dm) and not sells_online:
        result.recommended_service = "tienda_online"
    elif not data.has_website:
        result.recommended_service = "pagina_web"
    elif web_needs_fix:
        result.recommended_service = "mejora_web"
    else:
        result.recommended_service = "sistema_a_medida"

    if reason:
        result.excluded = True
        result.exclusion_reason = reason
        result.priority = Priority.LOW
    elif result.total >= config.thresholds.get("interesante", 70):
        result.priority = Priority.HIGH
    elif result.total >= config.thresholds.get("posible", 40):
        result.priority = Priority.POSSIBLE
    else:
        result.priority = Priority.LOW
    return result


def apply_score(lead: Lead, config: ScoringConfig) -> ScoreResult:
    result = score(ScoringInput.from_lead(lead), config)
    lead.fit_score = result.fit
    lead.need_score = result.need
    lead.reach_score = result.reach
    lead.business_score = result.total
    lead.priority = result.priority
    lead.excluded = result.excluded
    lead.exclusion_reason = result.exclusion_reason
    lead.is_business = result.is_business
    lead.is_local = result.is_local
    lead.business_type = result.business_type
    # Si la IA ya eligió el plan con más chances de venta, manda sobre las reglas.
    ai_offer = (lead.raw_data or {}).get("ai_offer") or {}
    lead.recommended_service = ai_offer.get("product") or result.recommended_service
    lead.score_breakdown = result.breakdown
    lead.scoring_version = config.version
    return result

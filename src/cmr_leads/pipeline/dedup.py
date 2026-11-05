"""Deduplicación: identificadores exactos + detección difusa de posibles duplicados."""

from dataclasses import dataclass

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from cmr_leads.db.models import Lead, LeadIdentifier
from cmr_leads.domain.enums import IdentifierType
from cmr_leads.pipeline.normalize import (
    address_key,
    is_aggregator_domain,
    name_key,
    normalize_text,
    phone_key,
)

# Campos que se completan al fusionar si el lead que se conserva no los tiene.
MERGEABLE_FIELDS = (
    "instagram_user_id",
    "instagram_username",
    "google_place_id",
    "display_name",
    "bio",
    "category",
    "business_type",
    "city",
    "province",
    "country",
    "address",
    "phone_e164",
    "whatsapp_e164",
    "email",
    "website",
    "website_domain",
    "followers",
    "following",
    "posts_count",
    "is_business_account",
    "assigned_to",
    "contacted_at",
    "next_follow_up_at",
)


def lead_identifiers(lead: Lead) -> set[tuple[str, str]]:
    idents: set[tuple[str, str]] = set()
    if lead.instagram_user_id:
        idents.add((IdentifierType.IG_ID, lead.instagram_user_id))
    if lead.instagram_username:
        idents.add((IdentifierType.IG_USERNAME, lead.instagram_username))
    if lead.google_place_id:
        idents.add((IdentifierType.PLACE_ID, lead.google_place_id))
    for number in (lead.phone_e164, lead.whatsapp_e164):
        key = phone_key(number)
        if key:
            idents.add((IdentifierType.PHONE, key))
    if lead.website_domain and not is_aggregator_domain(lead.website_domain):
        idents.add((IdentifierType.DOMAIN, lead.website_domain))
    if lead.email:
        idents.add((IdentifierType.EMAIL, lead.email))
    # Mismo negocio en distintas fuentes (Google, OSM, CSV): nombre + calle y número.
    name, address = name_key(lead.display_name), address_key(lead.address)
    if name and address:
        idents.add((IdentifierType.NAME_ADDRESS, f"{name}|{address}"[:200]))
    return idents


NEARBY_DEGREES = 0.0015  # ~150 m
NEARBY_NAME_SIMILARITY = 85


def find_nearby_same_name(session: Session, workspace_id: int, probe: Lead) -> Lead | None:
    """Respaldo cuando las fuentes escriben distinto la dirección: mismo nombre a menos de ~150 m."""
    name = name_key(probe.display_name)
    if not name or probe.latitude is None or probe.longitude is None:
        return None
    candidates = session.scalars(
        select(Lead).where(
            Lead.workspace_id == workspace_id,
            Lead.latitude.between(probe.latitude - NEARBY_DEGREES, probe.latitude + NEARBY_DEGREES),
            Lead.longitude.between(
                probe.longitude - NEARBY_DEGREES, probe.longitude + NEARBY_DEGREES
            ),
        )
    )
    best, best_score = None, 0.0
    for lead in candidates:
        other = name_key(lead.display_name)
        score = fuzz.token_set_ratio(name, other) if other else 0
        if score >= NEARBY_NAME_SIMILARITY and score > best_score:
            best, best_score = lead, score
    return best


def find_matching_leads(
    session: Session, workspace_id: int, idents: set[tuple[str, str]]
) -> list[Lead]:
    if not idents:
        return []
    leads: dict[int, Lead] = {}
    for ident_type, value in idents:
        row = session.scalar(
            select(LeadIdentifier).where(
                LeadIdentifier.workspace_id == workspace_id,
                LeadIdentifier.type == ident_type,
                LeadIdentifier.value == value,
            )
        )
        if row is not None:
            leads[row.lead_id] = row.lead
    return [leads[k] for k in sorted(leads)]


def sync_identifiers(session: Session, lead: Lead) -> Lead:
    """Registra los identificadores del lead. Si alguno ya pertenece a otro lead, los fusiona
    conservando el más antiguo. Devuelve el lead que sobrevive."""
    session.flush()
    for other in find_matching_leads(session, lead.workspace_id, lead_identifiers(lead)):
        if other.id == lead.id:
            continue
        keep, drop = (other, lead) if other.id < lead.id else (lead, other)
        lead = merge_leads(session, keep, drop)
    existing = {(i.type, i.value) for i in lead.identifiers}
    for ident_type, value in lead_identifiers(lead) - existing:
        lead.identifiers.append(
            LeadIdentifier(workspace_id=lead.workspace_id, type=ident_type, value=value)
        )
    session.flush()
    return lead


def merge_leads(session: Session, keep: Lead, drop: Lead) -> Lead:
    """Fusiona `drop` dentro de `keep` sin perder fuentes, interacciones ni historial."""
    for name in MERGEABLE_FIELDS:
        if getattr(keep, name) in (None, "") and getattr(drop, name) not in (None, ""):
            setattr(keep, name, getattr(drop, name))
    keep.whatsapp_confirmed = keep.whatsapp_confirmed or drop.whatsapp_confirmed
    keep.do_not_contact = keep.do_not_contact or drop.do_not_contact
    if drop.notes:
        keep.notes = f"{keep.notes}\n{drop.notes}" if keep.notes else drop.notes
    keep.raw_data = {**(drop.raw_data or {}), **(keep.raw_data or {})}

    # Reasignar el padre (en lugar de quitar de la colección) evita el borrado por huérfano.
    # Lo que ya existe en `keep` queda en `drop` y se borra en cascada con él.
    with session.no_autoflush:
        have_sources = {(s.source, s.source_ref) for s in keep.sources}
        for src in list(drop.sources):
            if (src.source, src.source_ref) not in have_sources:
                src.lead = keep
        for interaction in list(drop.interactions):
            interaction.lead = keep
        for change in list(drop.status_changes):
            change.lead = keep
        have_idents = {(i.type, i.value) for i in keep.identifiers}
        for ident in list(drop.identifiers):
            if (ident.type, ident.value) not in have_idents:
                ident.lead = keep
    session.flush()
    session.delete(drop)
    session.flush()
    return keep


@dataclass
class PossibleDuplicate:
    a: Lead
    b: Lead
    similarity: float


def find_possible_duplicates(
    session: Session, workspace_id: int, threshold: float = 90.0
) -> list[PossibleDuplicate]:
    """Nombres muy parecidos en la misma ciudad (o sin ciudad). No fusiona: lo decide una persona."""
    leads = session.scalars(
        select(Lead).where(Lead.workspace_id == workspace_id, Lead.display_name.is_not(None))
    ).all()
    names = [(lead, normalize_text(lead.display_name)) for lead in leads]
    pairs: list[PossibleDuplicate] = []
    for i, (lead_a, name_a) in enumerate(names):
        for lead_b, name_b in names[i + 1 :]:
            if (
                lead_a.city
                and lead_b.city
                and normalize_text(lead_a.city) != normalize_text(lead_b.city)
            ):
                continue
            score = fuzz.token_sort_ratio(name_a, name_b)
            if score >= threshold:
                pairs.append(PossibleDuplicate(lead_a, lead_b, score))
    return sorted(pairs, key=lambda p: -p.similarity)

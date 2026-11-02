"""Ingesta: perfil crudo -> normalización -> deduplicación -> lead guardado."""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from cmr_leads.db.models import Lead, LeadSource, Workspace, utcnow
from cmr_leads.domain.schemas import RawProfile, SourceRecord
from cmr_leads.enrichment.contact_extractor import Contacts, extract_from_text
from cmr_leads.pipeline.dedup import find_matching_leads, lead_identifiers, sync_identifiers
from cmr_leads.pipeline.normalize import (
    clean_str,
    is_aggregator_domain,
    is_mobile,
    normalize_email,
    normalize_phone,
    normalize_url,
    normalize_username,
    phone_key,
    to_whatsapp_e164,
    url_domain,
)


@dataclass
class IngestResult:
    lead: Lead
    created: bool


def _fill(lead: Lead, name: str, value, overwrite: bool) -> None:
    if value in (None, ""):
        return
    if overwrite or getattr(lead, name) in (None, ""):
        setattr(lead, name, value)


def apply_contacts(lead: Lead, contacts: Contacts) -> None:
    """Agrega contactos extraídos sin pisar los que ya tiene el lead."""
    if contacts.whatsapp and not lead.whatsapp_e164:
        lead.whatsapp_e164 = contacts.whatsapp[0]
        lead.whatsapp_confirmed = True
    whatsapp_key = phone_key(lead.whatsapp_e164)
    for phone in contacts.phones:
        if lead.phone_e164:
            break
        if phone_key(phone) != whatsapp_key:
            lead.phone_e164 = phone
    if lead.phone_e164 and phone_key(lead.phone_e164) == whatsapp_key:
        lead.phone_e164 = None  # es el mismo número que el WhatsApp
    if contacts.emails and not lead.email:
        lead.email = contacts.emails[0]
    if not lead.website:
        own_sites = [u for u in contacts.urls if not is_aggregator_domain(url_domain(u))]
        candidates = own_sites or contacts.urls
        if candidates:
            lead.website = normalize_url(candidates[0])
            lead.website_domain = url_domain(lead.website)
    lead.has_link_in_bio_only = bool(lead.website_domain) and is_aggregator_domain(
        lead.website_domain
    )


def apply_profile(lead: Lead, profile: RawProfile, country: str, overwrite: bool = True) -> None:
    """Vuelca un perfil normalizado sobre el lead. `overwrite` para datos frescos de una API."""
    _fill(lead, "instagram_username", normalize_username(profile.instagram_username), overwrite)
    _fill(lead, "instagram_user_id", clean_str(profile.instagram_user_id), overwrite)
    _fill(lead, "google_place_id", clean_str(profile.google_place_id), overwrite)
    _fill(lead, "display_name", clean_str(profile.display_name), overwrite)
    _fill(lead, "bio", clean_str(profile.bio), overwrite)
    _fill(lead, "category", clean_str(profile.category), overwrite)
    _fill(lead, "city", clean_str(profile.city), overwrite)
    _fill(lead, "province", clean_str(profile.province), overwrite)
    _fill(lead, "country", clean_str(profile.country), overwrite)
    _fill(lead, "address", clean_str(profile.address), overwrite)
    _fill(lead, "followers", profile.followers, overwrite)
    _fill(lead, "following", profile.following, overwrite)
    _fill(lead, "posts_count", profile.posts_count, overwrite)
    if profile.is_business_account is not None:
        _fill(lead, "is_business_account", profile.is_business_account, overwrite)

    website = normalize_url(profile.website)
    if website:
        _fill(lead, "website", website, overwrite)
        if lead.website == website:
            lead.website_domain = url_domain(website)

    whatsapp = to_whatsapp_e164(normalize_phone(profile.whatsapp, country))
    if whatsapp:
        _fill(lead, "whatsapp_e164", whatsapp, overwrite)
        lead.whatsapp_confirmed = True
    _fill(lead, "phone_e164", normalize_phone(profile.phone, country), overwrite)
    if not lead.whatsapp_e164 and is_mobile(lead.phone_e164):
        # Un celular casi siempre tiene WhatsApp, pero queda sin confirmar.
        lead.whatsapp_e164 = to_whatsapp_e164(lead.phone_e164)
        lead.whatsapp_confirmed = False
    _fill(lead, "email", normalize_email(profile.email), overwrite)

    if profile.notes:
        lead.notes = f"{lead.notes}\n{profile.notes}" if lead.notes else profile.notes
    if profile.raw:
        lead.raw_data = {**(lead.raw_data or {}), **profile.raw}

    contacts = extract_from_text(lead.bio, country)
    if lead.website:
        contacts.merge(extract_from_text(lead.website, country))
    apply_contacts(lead, contacts)


def ingest(
    session: Session, workspace: Workspace, record: SourceRecord, country: str
) -> IngestResult:
    probe = Lead(workspace_id=workspace.id)
    apply_profile(probe, record.profile, country)
    matches = find_matching_leads(session, workspace.id, lead_identifiers(probe))

    created = not matches
    if created:
        lead = probe
        session.add(lead)
    else:
        lead = matches[0]
        apply_profile(lead, record.profile, country, overwrite=False)

    if not any(
        s.source == record.source and s.source_ref == record.source_ref for s in lead.sources
    ):
        lead.sources.append(LeadSource(source=record.source, source_ref=record.source_ref))
    lead.updated_at = utcnow()
    survivor = sync_identifiers(session, lead)
    return IngestResult(lead=survivor, created=created and survivor is lead)

"""Enriquecimiento: Business Discovery (Instagram) + sitio web público."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from cmr_leads.db.models import Lead, Workspace, utcnow
from cmr_leads.domain.enums import EnrichmentState
from cmr_leads.domain.schemas import RawProfile
from cmr_leads.enrichment.contact_extractor import extract_from_links, extract_from_text
from cmr_leads.enrichment.instagram_discovery import AuthError, InstagramApiError, RateLimitedError
from cmr_leads.enrichment.website import WebsiteSignals
from cmr_leads.pipeline.budget import DailyBudget
from cmr_leads.pipeline.dedup import sync_identifiers
from cmr_leads.pipeline.ingest import apply_contacts, apply_profile

PROVIDER_BUSINESS_DISCOVERY = "meta_business_discovery"


class ProfileLookup(Protocol):
    def lookup(self, username: str) -> RawProfile | None: ...


@dataclass
class EnrichSummary:
    found: int = 0
    not_found: int = 0
    errors: int = 0
    skipped_no_api: int = 0
    websites_checked: int = 0
    stopped_reason: str | None = None
    lead_ids: list[int] = field(default_factory=list)


def enrich_website(lead: Lead, fetcher: Callable[[str], WebsiteSignals], country: str) -> None:
    if lead.has_link_in_bio_only:
        return  # WhatsApp, Instagram, Linktree...: no es su web, analizarla da datos falsos
    signals = fetcher(lead.website)
    lead.raw_data = {**(lead.raw_data or {}), "website": signals.to_json()}
    contacts = extract_from_links(signals.links, country)
    contacts.merge(extract_from_text(signals.text, country))
    contacts.urls = []  # no reemplazar el sitio por links internos
    apply_contacts(lead, contacts)


def enrich_pending(
    session: Session,
    workspace: Workspace,
    lookup: ProfileLookup | None,
    budget_limit: int,
    country: str,
    limit: int = 20,
    website_fetcher: Callable[[str], WebsiteSignals] | None = None,
) -> EnrichSummary:
    summary = EnrichSummary()
    budget = DailyBudget(session, workspace.id, PROVIDER_BUSINESS_DISCOVERY, budget_limit)
    leads = session.scalars(
        select(Lead)
        .where(
            Lead.workspace_id == workspace.id,
            Lead.enrichment_state == EnrichmentState.PENDING,
            Lead.do_not_contact.is_(False),
        )
        .order_by(Lead.id)
        .limit(limit)
    ).all()

    for lead in leads:
        if lead.instagram_username and lookup is None:
            # Queda pendiente hasta que se configure la API de Instagram.
            summary.skipped_no_api += 1
            continue
        if lookup is not None and lead.instagram_username:
            if budget.remaining() <= 0:
                summary.stopped_reason = f"tope diario de Business Discovery ({budget_limit})"
                break
            try:
                budget.consume()
                profile = lookup.lookup(lead.instagram_username)
            except RateLimitedError as exc:
                summary.stopped_reason = f"rate limit de Meta: {exc}"
                break
            except AuthError as exc:
                summary.stopped_reason = f"token inválido o vencido: {exc}"
                break
            except InstagramApiError as exc:
                lead.enrichment_state = EnrichmentState.ERROR
                lead.notes = f"{lead.notes or ''}\n[enrich] {exc}".strip()
                summary.errors += 1
                continue
            lead.last_checked_at = utcnow()
            if profile is None:
                lead.enrichment_state = EnrichmentState.NOT_FOUND
                lead.is_business_account = False
                summary.not_found += 1
                summary.lead_ids.append(lead.id)
                continue
            apply_profile(lead, profile, country, overwrite=True)
            summary.found += 1

        if website_fetcher is not None and lead.website:
            enrich_website(lead, website_fetcher, country)
            summary.websites_checked += 1

        lead.enrichment_state = EnrichmentState.DONE
        lead.last_checked_at = utcnow()
        lead = sync_identifiers(session, lead)
        summary.lead_ids.append(lead.id)

    session.flush()
    return summary

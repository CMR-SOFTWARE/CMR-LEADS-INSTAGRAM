"""Búsqueda de negocios en Google Places con tope diario de requests."""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from cmr_leads.db.models import Workspace
from cmr_leads.pipeline.budget import DailyBudget
from cmr_leads.pipeline.ingest import ingest
from cmr_leads.pipeline.scoring import ScoringConfig, apply_score
from cmr_leads.sources.google_places import (
    PAGE_SIZE,
    GooglePlacesClient,
    GooglePlacesSource,
    PlacesApiError,
)

PROVIDER_GOOGLE_PLACES = "google_places"


@dataclass
class GoogleSearchResult:
    created: int = 0
    existing: int = 0
    closed: int = 0
    requests: int = 0
    stopped_reason: str | None = None

    def summary(self, query: str) -> str:
        text = f'"{query}": {self.created} nuevos, {self.existing} ya estaban'
        if self.closed:
            text += f", {self.closed} cerrados (ignorados)"
        if self.stopped_reason:
            text += f" · {self.stopped_reason}"
        return text


def run_google_search(
    session: Session,
    workspace: Workspace,
    client: GooglePlacesClient,
    query: str,
    area: str,
    max_results: int,
    budget_limit: int,
    country: str,
    config: ScoringConfig,
) -> GoogleSearchResult:
    result = GoogleSearchResult()
    budget = DailyBudget(session, workspace.id, PROVIDER_GOOGLE_PLACES, budget_limit)
    remaining = budget.remaining()
    if remaining <= 0:
        result.stopped_reason = f"se alcanzó el tope diario de Google ({budget_limit} búsquedas)"
        return result

    pages_wanted = max(1, -(-max_results // PAGE_SIZE))
    source = GooglePlacesSource(
        client, query, area, max_results=max_results, max_requests=min(pages_wanted, remaining)
    )
    try:
        for record in source.discover():
            ingested = ingest(session, workspace, record, country)
            apply_score(ingested.lead, config)
            if ingested.created:
                result.created += 1
            else:
                result.existing += 1
    except PlacesApiError as exc:
        result.stopped_reason = f"error de Google: {exc}"
    finally:
        for _ in range(source.requests_made):
            budget.consume()
    result.requests = source.requests_made
    result.closed = source.skipped_closed
    if (
        source.requests_made < pages_wanted
        and not result.stopped_reason
        and remaining < pages_wanted
    ):
        result.stopped_reason = "se alcanzó el tope diario de Google"
    return result

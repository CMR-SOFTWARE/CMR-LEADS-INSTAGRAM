import httpx
import pytest

from cmr_leads.db.models import Lead
from cmr_leads.pipeline.google_search import run_google_search
from cmr_leads.sources.google_places import (
    GooglePlacesClient,
    GooglePlacesSource,
    PlacesApiError,
    place_to_profile,
)


def _place(i, **extra):
    place = {
        "id": f"place{i}",
        "displayName": {"text": f"Peluquería {i}"},
        "formattedAddress": f"Calle {i}, San Nicolás de los Arroyos, Buenos Aires",
        "addressComponents": [
            {"longText": "San Nicolás de los Arroyos", "types": ["locality", "political"]},
            {"longText": "Buenos Aires", "types": ["administrative_area_level_1"]},
        ],
        "internationalPhoneNumber": f"+54 336 442-12{i:02d}",
        "primaryTypeDisplayName": {"text": "Peluquería"},
        "googleMapsUri": f"https://maps.google.com/?cid={i}",
        "businessStatus": "OPERATIONAL",
        "rating": 4.6,
        "userRatingCount": 30,
    }
    place.update(extra)
    return place


def _client(pages, calls=None):
    """`pages`: lista de respuestas; cada request devuelve la siguiente."""
    responses = iter(pages)

    def handler(request):
        if calls is not None:
            calls.append(request)
        status, body = next(responses)
        return httpx.Response(status, json=body)

    return GooglePlacesClient("key", http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_place_to_profile_maps_fields():
    profile = place_to_profile(_place(1, websiteUri="https://peluqueria1.com.ar"))
    assert profile.google_place_id == "place1"
    assert profile.display_name == "Peluquería 1"
    assert profile.category == "Peluquería"
    assert profile.city == "San Nicolás de los Arroyos"
    assert profile.website == "https://peluqueria1.com.ar"
    assert profile.raw["google_places"]["rating"] == 4.6


def test_instagram_as_website_becomes_username():
    profile = place_to_profile(_place(1, websiteUri="https://www.instagram.com/pelu.uno/"))
    assert profile.instagram_username == "pelu.uno"
    assert profile.website is None


def test_source_paginates_and_skips_closed():
    calls = []
    client = _client(
        [
            (
                200,
                {
                    "places": [_place(1), _place(2, businessStatus="CLOSED_PERMANENTLY")],
                    "nextPageToken": "t2",
                },
            ),
            (200, {"places": [_place(3)]}),
        ],
        calls,
    )
    source = GooglePlacesSource(client, "peluquería", "San Nicolás", max_results=60, max_requests=3)
    records = list(source.discover())
    assert [r.profile.google_place_id for r in records] == ["place1", "place3"]
    assert source.requests_made == 2 and source.skipped_closed == 1
    assert calls[0].headers["X-Goog-Api-Key"] == "key"
    assert "places.websiteUri" in calls[0].headers["X-Goog-FieldMask"]
    assert b'"pageToken":"t2"' in calls[1].content.replace(b" ", b"")
    assert all(r.source_ref == "peluquería" for r in records)


def test_api_error_is_raised():
    client = _client([(403, {"error": {"message": "API key not valid"}})])
    with pytest.raises(PlacesApiError, match="API key not valid"):
        client.search_text("x")


def test_run_search_ingests_dedups_and_scores(session, workspace, scoring_config):
    page = (200, {"places": [_place(1), _place(2)]})
    first = run_google_search(
        session,
        workspace,
        _client([page]),
        "peluquería",
        "San Nicolás",
        20,
        100,
        "AR",
        scoring_config,
    )
    second = run_google_search(
        session,
        workspace,
        _client([page]),
        "peluquería",
        "San Nicolás",
        20,
        100,
        "AR",
        scoring_config,
    )
    assert (first.created, second.created, second.existing) == (2, 0, 2)
    lead = session.query(Lead).filter_by(google_place_id="place1").one()
    assert lead.phone_e164 == "+543364421201"
    assert lead.is_local and lead.business_type == "estetica"
    assert lead.business_score > 40
    assert {s.source for s in lead.sources} == {"google_places"}


def test_run_search_respects_daily_budget(session, workspace, scoring_config):
    page = (200, {"places": [_place(1)], "nextPageToken": "more"})
    result = run_google_search(
        session, workspace, _client([page, page, page]), "x", "y", 60, 1, "AR", scoring_config
    )
    assert result.requests == 1
    result = run_google_search(
        session, workspace, _client([page]), "x", "y", 20, 1, "AR", scoring_config
    )
    assert result.requests == 0 and "tope diario" in result.stopped_reason


def test_google_lead_merges_with_instagram_lead_by_phone(session, workspace, scoring_config):
    from cmr_leads.domain.enums import SourceType
    from cmr_leads.domain.schemas import RawProfile, SourceRecord
    from cmr_leads.pipeline.ingest import ingest

    ingest(
        session,
        workspace,
        SourceRecord(
            profile=RawProfile(
                instagram_username="pelu.uno", bio="Turnos 📲 WhatsApp 336 442-1201"
            ),
            source=SourceType.CMR_FOLLOWERS,
        ),
        "AR",
    )
    run_google_search(
        session,
        workspace,
        _client([(200, {"places": [_place(1)]})]),
        "peluquería",
        "San Nicolás",
        20,
        100,
        "AR",
        scoring_config,
    )
    lead = session.query(Lead).one()
    assert lead.instagram_username == "pelu.uno" and lead.google_place_id == "place1"
    assert {s.source for s in lead.sources} == {"cmr_followers", "google_places"}


def test_monthly_limit_stops_searches(session, workspace, scoring_config):
    from cmr_leads.db.models import ApiUsage
    from cmr_leads.pipeline.google_search import PROVIDER_GOOGLE_PLACES

    for _ in range(3):
        session.add(ApiUsage(workspace_id=workspace.id, provider=PROVIDER_GOOGLE_PLACES))
    session.flush()

    class NeverCalled:
        def search_text(self, query, page_token=None):
            raise AssertionError("no debería consultar a Google")

    result = run_google_search(
        session,
        workspace,
        NeverCalled(),
        "gimnasio",
        "San Nicolás",
        20,
        100,
        "AR",
        scoring_config,
        monthly_limit=3,
    )
    assert result.requests == 0
    assert "tope mensual" in result.stopped_reason

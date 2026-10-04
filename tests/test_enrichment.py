import httpx
import pytest

from cmr_leads.db.models import Lead
from cmr_leads.domain.enums import EnrichmentState, SourceType
from cmr_leads.domain.schemas import RawProfile, SourceRecord
from cmr_leads.enrichment.instagram_discovery import (
    BusinessDiscoveryClient,
    RateLimitedError,
)
from cmr_leads.enrichment.website import WebsiteSignals, fetch_website_signals
from cmr_leads.pipeline.enrich import enrich_pending
from cmr_leads.pipeline.ingest import ingest


def _client(handler):
    return BusinessDiscoveryClient(
        "token",
        "17841400000000000",
        "v23.0",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_business_discovery_parses_profile():
    def handler(request):
        assert "business_discovery.username(estetica.sofi)" in request.url.params["fields"]
        return httpx.Response(
            200,
            json={
                "business_discovery": {
                    "id": "999",
                    "username": "estetica.sofi",
                    "name": "Estética Sofi",
                    "biography": "Turnos 📲 WhatsApp 3364-123456",
                    "website": "https://linktr.ee/sofi",
                    "followers_count": 1200,
                    "follows_count": 300,
                    "media_count": 80,
                }
            },
        )

    profile = _client(handler).lookup("@estetica.sofi")
    assert profile.instagram_user_id == "999"
    assert profile.is_business_account is True
    assert profile.followers == 1200


def test_business_discovery_personal_account_returns_none():
    def handler(request):
        return httpx.Response(
            400,
            json={"error": {"code": 110, "error_subcode": 2207013, "message": "Cannot find User"}},
        )

    assert _client(handler).lookup("persona") is None


def test_business_discovery_rate_limit_raises():
    def handler(request):
        return httpx.Response(
            400, json={"error": {"code": 4, "message": "Application request limit reached"}}
        )

    with pytest.raises(RateLimitedError):
        _client(handler).lookup("x")


def test_invalid_username_is_rejected_before_calling_api():
    with pytest.raises(ValueError):
        _client(lambda r: httpx.Response(500)).lookup("bad name){id}")


class FakeLookup:
    def __init__(self, profiles):
        self.profiles = profiles
        self.calls = []

    def lookup(self, username):
        self.calls.append(username)
        return self.profiles.get(username)


def _seed(session, workspace, *usernames):
    for username in usernames:
        ingest(
            session,
            workspace,
            SourceRecord(
                profile=RawProfile(instagram_username=username), source=SourceType.CMR_FOLLOWERS
            ),
            "AR",
        )


def test_enrich_pending_updates_and_marks_not_found(session, workspace):
    _seed(session, workspace, "estetica.sofi", "persona.x")
    lookup = FakeLookup(
        {
            "estetica.sofi": RawProfile(
                instagram_user_id="999",
                instagram_username="estetica.sofi",
                display_name="Estética Sofi",
                bio="Turnos 📲 WhatsApp 3364-123456",
                is_business_account=True,
            )
        }
    )
    summary = enrich_pending(session, workspace, lookup, budget_limit=10, country="AR")
    assert summary.found == 1 and summary.not_found == 1
    sofi = session.query(Lead).filter_by(instagram_username="estetica.sofi").one()
    assert sofi.enrichment_state == EnrichmentState.DONE
    assert sofi.whatsapp_e164 == "+5493364123456"
    persona = session.query(Lead).filter_by(instagram_username="persona.x").one()
    assert persona.enrichment_state == EnrichmentState.NOT_FOUND


def test_enrich_respects_daily_budget(session, workspace):
    _seed(session, workspace, "a1", "a2", "a3")
    lookup = FakeLookup({})
    summary = enrich_pending(session, workspace, lookup, budget_limit=2, country="AR")
    assert len(lookup.calls) == 2
    assert "tope diario" in summary.stopped_reason
    # El tope es por 24 h: una segunda corrida no consulta nada.
    summary = enrich_pending(session, workspace, lookup, budget_limit=2, country="AR")
    assert len(lookup.calls) == 2


def test_enrich_without_api_keeps_instagram_leads_pending(session, workspace):
    _seed(session, workspace, "a1")
    summary = enrich_pending(session, workspace, None, budget_limit=10, country="AR")
    assert summary.skipped_no_api == 1
    assert session.query(Lead).one().enrichment_state == EnrichmentState.PENDING


def test_enrich_uses_website_contacts(session, workspace):
    ingest(
        session,
        workspace,
        SourceRecord(
            profile=RawProfile(display_name="Ferretería", website="ferreteria.com.ar"),
            source=SourceType.MANUAL,
        ),
        "AR",
    )

    def fetcher(url):
        return WebsiteSignals(
            url=url,
            reachable=True,
            https=True,
            mobile_viewport=False,
            links=["https://wa.me/5493364999999", "mailto:ventas@ferreteria.com.ar"],
        )

    enrich_pending(session, workspace, None, budget_limit=10, country="AR", website_fetcher=fetcher)
    lead = session.query(Lead).one()
    assert lead.whatsapp_e164 == "+5493364999999"
    assert lead.email == "ventas@ferreteria.com.ar"
    assert lead.raw_data["website"]["mobile_viewport"] is False


def test_fetch_website_signals_respects_robots():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /")
        raise AssertionError("no debería pedir la home")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    signals = fetch_website_signals("https://negocio.com.ar", client=client)
    assert not signals.reachable
    assert "robots" in signals.skipped_reason


def test_fetch_website_signals_extracts_links():
    html = (
        '<html><head><title>Negocio</title><meta name="viewport" content="width=device-width"></head>'
        '<body><a href="https://wa.me/5493364123456">WA</a><a href="/contacto">c</a></body></html>'
    )

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=html)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    signals = fetch_website_signals("https://negocio.com.ar", client=client)
    assert signals.reachable and signals.https and signals.mobile_viewport
    assert signals.title == "Negocio"
    assert "https://wa.me/5493364123456" in signals.links
    assert "https://negocio.com.ar/contacto" in signals.links

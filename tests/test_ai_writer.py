import json

import httpx
import pytest

from cmr_leads.db.models import Lead
from cmr_leads.domain.schemas import RawProfile
from cmr_leads.messaging.ai_writer import (
    AiWriterError,
    build_lead_facts,
    describe_website,
    write_message,
)
from cmr_leads.pipeline.ingest import apply_profile
from cmr_leads.pipeline.normalize import is_mobile
from cmr_leads.settings import Settings


def _settings(**kwargs) -> Settings:
    return Settings(_env_file=None, gemini_api_key="test-key", **kwargs)


def _gym(**kwargs) -> Lead:
    defaults = dict(
        display_name="Gimnasio IronMan",
        category="Gimnasio",
        city="San Nicolás de Los Arroyos",
        instagram_username="ironmangymsn",
        recommended_service="sistema_turnos",
        raw_data={"google_places": {"rating": 4.7, "reviews": 120}},
    )
    return Lead(**{**defaults, **kwargs})


def test_is_mobile_only_for_argentine_cellphones():
    assert is_mobile("+5493364608360")
    assert not is_mobile("+543364201666")
    assert not is_mobile(None)


def test_google_cellphone_becomes_unconfirmed_whatsapp():
    lead = Lead()
    apply_profile(lead, RawProfile(phone="+54 9 336 460-8360"), "AR")
    assert lead.whatsapp_e164 == "+5493364608360"
    assert lead.whatsapp_confirmed is False


def test_google_landline_is_not_whatsapp():
    lead = Lead()
    apply_profile(lead, RawProfile(phone="+54 336 420-1666"), "AR")
    assert lead.whatsapp_e164 is None
    assert lead.phone_e164 == "+543364201666"


def test_describe_website_cases():
    assert "Instagram como vidriera" in describe_website(_gym())
    lead = _gym(
        website="https://gym.com.ar",
        website_domain="gym.com.ar",
        raw_data={"website": {"reachable": True, "mobile_viewport": False, "https": True}},
    )
    assert "no está adaptada al celular" in describe_website(lead)
    broken = _gym(
        website="https://x.com.ar",
        website_domain="x.com.ar",
        raw_data={"website": {"reachable": False}},
    )
    assert "no funciona" in describe_website(broken)


def test_facts_include_rubro_and_service_but_not_ratings():
    facts = build_lead_facts(_gym())
    assert "Rubro: Gimnasio" in facts
    assert "estrellas" not in facts and "reseñas" not in facts
    assert "Plan Gestión con turnos" in facts


def test_write_message_calls_gemini_and_skips_thoughts():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers["x-goog-api-key"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "pensando...", "thought": True},
                                {"text": '"Hola, ¿cómo andan? Soy Tomás."\n'},
                            ]
                        }
                    }
                ]
            },
        )

    text = write_message(_gym(), _settings(), httpx.Client(transport=httpx.MockTransport(handler)))
    assert text == "Hola, ¿cómo andan? Soy Tomás."
    assert seen["key"] == "test-key"
    assert "gemini-" in seen["url"]
    assert "Gimnasio IronMan" in seen["body"]["contents"][0]["parts"][0]["text"]


def test_write_message_reports_api_errors():
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(400, json={"error": {"message": "API key not valid"}})
        )
    )
    with pytest.raises(AiWriterError, match="API key not valid"):
        write_message(_gym(), _settings(), client)


def test_write_message_requires_key():
    with pytest.raises(AiWriterError):
        write_message(_gym(), Settings(_env_file=None))


def test_write_message_falls_back_when_model_is_busy():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(503, json={"error": {"message": "high demand"}})
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "Hola"}]}}]}
        )

    text = write_message(_gym(), _settings(), httpx.Client(transport=httpx.MockTransport(handler)))
    assert text == "Hola"
    assert len(calls) == 2 and calls[0] != calls[1]


def test_catalog_is_sent_to_gemini(tmp_path):
    catalog = tmp_path / "catalogo.md"
    catalog.write_text("CMR Nexo: turnos online", encoding="utf-8")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "Hola"}]}}]}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    write_message(_gym(recommended_service="cmr_nexo"), _settings(catalog_path=catalog), client)
    assert "CMR Nexo: turnos online" in seen["body"]["systemInstruction"]["parts"][0]["text"]
    assert "CMR Nexo" in seen["body"]["contents"][0]["parts"][0]["text"]

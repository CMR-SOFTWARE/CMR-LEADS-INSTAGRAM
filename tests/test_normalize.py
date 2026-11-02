import pytest

from cmr_leads.pipeline.normalize import (
    is_aggregator_domain,
    normalize_phone,
    normalize_text,
    normalize_url,
    normalize_username,
    phone_key,
    to_whatsapp_e164,
    url_domain,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("@Mi.Negocio ", "mi.negocio"),
        ("https://www.instagram.com/mi.negocio/", "mi.negocio"),
        ("instagram.com/_u/mi_negocio", "mi_negocio"),
        ("con espacios", None),
        ("", None),
    ],
)
def test_normalize_username(raw, expected):
    assert normalize_username(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0336 15 4123456", "+5493364123456"),
        ("+54 9 336 412-3456", "+5493364123456"),
        ("5493364123456", "+5493364123456"),
        ("(0336) 442-1234", "+543364421234"),
        ("15000", None),
        ("abc", None),
    ],
)
def test_normalize_phone_argentina(raw, expected):
    assert normalize_phone(raw, "AR") == expected


def test_phone_key_ignores_mobile_nine():
    assert phone_key("+5493364123456") == phone_key("+543364123456") == "543364123456"


def test_to_whatsapp_adds_mobile_nine():
    assert to_whatsapp_e164("+543364123456") == "+5493364123456"
    assert to_whatsapp_e164("+5493364123456") == "+5493364123456"


def test_urls_and_domains():
    assert normalize_url("minegocio.com.ar") == "https://minegocio.com.ar"
    assert normalize_url("no-es-url") is None
    assert url_domain("https://www.MiNegocio.com.ar/contacto") == "minegocio.com.ar"
    assert is_aggregator_domain("linktr.ee")
    assert is_aggregator_domain("l.instagram.com")
    assert not is_aggregator_domain("minegocio.com.ar")


def test_normalize_text_removes_accents():
    assert normalize_text("  Estética   SAN Nicolás ") == "estetica san nicolas"


def test_whatsapp_chat_link_detects_short_links():
    from cmr_leads.pipeline.normalize import whatsapp_chat_link

    assert whatsapp_chat_link("https://wa.me/message/TU2R7RUDF3E2B1") == (
        "https://wa.me/message/TU2R7RUDF3E2B1"
    )
    assert whatsapp_chat_link(None, "Escribinos https://wa.link/abc123") == "https://wa.link/abc123"
    assert whatsapp_chat_link("https://estetica.com.ar") is None

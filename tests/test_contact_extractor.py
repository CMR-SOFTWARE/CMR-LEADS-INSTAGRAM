from cmr_leads.enrichment.contact_extractor import extract_from_links, extract_from_text


def test_wa_me_link_in_bio():
    found = extract_from_text("Pedidos 👉 wa.me/5493364123456")
    assert found.whatsapp == ["+5493364123456"]


def test_api_whatsapp_link():
    found = extract_from_links(["https://api.whatsapp.com/send?phone=5493364123456&text=hola"])
    assert found.whatsapp == ["+5493364123456"]


def test_phone_next_to_whatsapp_word_is_whatsapp():
    found = extract_from_text("Turnos\n📲 WhatsApp 3364-123456\nSan Nicolás")
    assert found.whatsapp == ["+5493364123456"]
    assert found.phones == []


def test_plain_phone_and_email():
    found = extract_from_text("Tel 0336 442-1234 · info@minegocio.com.ar · desde $15.000")
    assert found.phones == ["+543364421234"]
    assert found.emails == ["info@minegocio.com.ar"]
    assert found.whatsapp == []


def test_tel_and_mailto_links():
    found = extract_from_links(["tel:+543364421234", "mailto:hola@negocio.com?subject=x"])
    assert found.phones == ["+543364421234"]
    assert found.emails == ["hola@negocio.com"]


def test_urls_in_bio():
    found = extract_from_text("Nuestra tienda: www.minegocio.com.ar")
    assert "www.minegocio.com.ar" in found.urls

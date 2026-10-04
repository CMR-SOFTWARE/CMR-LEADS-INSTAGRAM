from urllib.parse import quote


def whatsapp_link(e164: str, text: str) -> str:
    """Link click-to-chat oficial: abre WhatsApp con el mensaje precargado; la persona envía."""
    return f"https://wa.me/{e164.lstrip('+')}?text={quote(text)}"


def instagram_dm_link(username: str) -> str:
    return f"https://ig.me/m/{username}"

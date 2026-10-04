"""Normalización de usernames, teléfonos, URLs y texto."""

import re
import unicodedata
from urllib.parse import urlparse

import phonenumbers

USERNAME_RE = re.compile(r"^[a-z0-9._]{1,30}$")

# Dominios de "link en bio" o redes: no son un sitio propio ni sirven para deduplicar.
AGGREGATOR_DOMAINS = (
    "linktr.ee",
    "beacons.ai",
    "bio.link",
    "taplink.cc",
    "lnk.bio",
    "linkin.bio",
    "campsite.bio",
    "solo.to",
    "msha.ke",
    "linkbio.co",
    "wa.me",
    "wa.link",
    "whatsapp.com",
    "instagram.com",
    "facebook.com",
    "fb.com",
    "fb.me",
    "m.me",
    "ig.me",
    "tiktok.com",
    "youtube.com",
    "youtu.be",
    "twitter.com",
    "x.com",
    "forms.gle",
    "goo.gl",
    "bit.ly",
)


def normalize_username(value: str | None) -> str | None:
    """'@Mi.Negocio ' o 'https://instagram.com/mi.negocio/' -> 'mi.negocio'."""
    if not value:
        return None
    value = value.strip()
    if "instagram.com/" in value:
        path = urlparse(value if "://" in value else f"https://{value}").path
        parts = [p for p in path.split("/") if p and p != "_u"]
        value = parts[0] if parts else ""
    value = value.lstrip("@").strip().lower()
    return value if USERNAME_RE.match(value) else None


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def normalize_text(text: str | None) -> str:
    """Minúsculas, sin acentos y con espacios colapsados (para matchear keywords)."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", strip_accents(text).lower()).strip()


def clean_str(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def normalize_phone(value: str | None, country: str = "AR") -> str | None:
    """Devuelve E.164 (+5493364123456) o None si no es un número válido."""
    if not value:
        return None
    value = value.strip()
    digits = re.sub(r"\D", "", value)
    # Los links de WhatsApp vienen en formato internacional sin '+': 5493364123456
    if not value.startswith("+") and digits.startswith("54") and len(digits) >= 12:
        value = f"+{digits}"
    try:
        number = phonenumbers.parse(value, country)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(number):
        return None
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def phone_key(e164: str | None) -> str | None:
    """Clave de deduplicación: en Argentina el mismo número aparece con y sin el 9 de celular."""
    if not e164:
        return None
    digits = e164.lstrip("+")
    if digits.startswith("549"):
        digits = "54" + digits[3:]
    return digits


def to_whatsapp_e164(e164: str | None) -> str | None:
    """WhatsApp en Argentina usa el formato de celular: +54 9 <área> <número>."""
    if not e164:
        return None
    if e164.startswith("+54") and not e164.startswith("+549"):
        return "+549" + e164[3:]
    return e164


def normalize_url(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlparse(value)
    if not parsed.netloc or "." not in parsed.netloc:
        return None
    return value


def url_domain(url: str | None) -> str | None:
    if not url:
        return None
    host = urlparse(url if "://" in url else f"https://{url}").hostname or ""
    host = host.lower().removeprefix("www.")
    return host or None


def is_aggregator_domain(domain: str | None) -> bool:
    if not domain:
        return False
    return any(domain == d or domain.endswith("." + d) for d in AGGREGATOR_DOMAINS)


def normalize_email(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip().lower()
    return value if re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", value) else None

"""Extrae datos de contacto PÚBLICOS de textos (bio, sitio web) y links."""

import re
from dataclasses import dataclass, field

import phonenumbers

from cmr_leads.pipeline.normalize import normalize_email, normalize_phone, to_whatsapp_e164

WHATSAPP_LINK_RES = (
    re.compile(r"wa\.me/\+?(\d{8,15})", re.I),
    re.compile(r"(?:api\.)?whatsapp\.com/send/?\?(?:[^\s\"'<>]*?&)?phone=\+?(\d{8,15})", re.I),
)
WHATSAPP_WORD_RE = re.compile(r"whats\s?app|\bwsp\b|\bwpp\b|\bwhats\b|📲|💬", re.I)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s\"'<>]+|\b[\w-]+\.(?:com|ar|net|com\.ar|site|store|shop|online)(?:/[^\s\"'<>]*)?\b",
    re.I,
)
TEL_LINK_RE = re.compile(r"tel:([+\d\s().-]{8,20})", re.I)


@dataclass
class Contacts:
    whatsapp: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)

    def merge(self, other: "Contacts") -> None:
        for name in ("whatsapp", "phones", "emails", "urls"):
            target = getattr(self, name)
            for value in getattr(other, name):
                if value not in target:
                    target.append(value)


def _add(items: list[str], value: str | None) -> None:
    if value and value not in items:
        items.append(value)


def extract_from_links(links: list[str], country: str = "AR") -> Contacts:
    found = Contacts()
    for link in links:
        for regex in WHATSAPP_LINK_RES:
            for match in regex.finditer(link):
                _add(found.whatsapp, to_whatsapp_e164(normalize_phone(match.group(1), country)))
        for match in TEL_LINK_RE.finditer(link):
            _add(found.phones, normalize_phone(match.group(1), country))
        if link.lower().startswith("mailto:"):
            _add(found.emails, normalize_email(link[7:].split("?")[0]))
    return found


def extract_from_text(text: str | None, country: str = "AR") -> Contacts:
    found = Contacts()
    if not text:
        return found
    found.merge(extract_from_links(re.findall(r"\S+", text), country))
    # Un teléfono en la misma línea que "WhatsApp"/"wsp"/📲 se considera WhatsApp publicado.
    for line in text.splitlines():
        is_whatsapp_line = bool(WHATSAPP_WORD_RE.search(line))
        for match in phonenumbers.PhoneNumberMatcher(line, country):
            e164 = phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.E164)
            if is_whatsapp_line:
                _add(found.whatsapp, to_whatsapp_e164(e164))
            else:
                _add(found.phones, e164)
    for match in EMAIL_RE.finditer(text):
        _add(found.emails, normalize_email(match.group(0)))
    for match in URL_RE.finditer(text):
        url = match.group(0).rstrip(".,;)")
        if "@" not in url:
            _add(found.urls, url)
    return found

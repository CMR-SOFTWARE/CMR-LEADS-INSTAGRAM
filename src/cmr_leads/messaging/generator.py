"""Genera un BORRADOR de mensaje para revisión humana. Nunca envía nada."""

import re
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from cmr_leads.db.models import Lead
from cmr_leads.domain.enums import Channel
from cmr_leads.messaging.links import instagram_dm_link, whatsapp_link
from cmr_leads.settings import Settings

TEMPLATES_DIR = Path(__file__).parent / "templates"
SERVICE_TEMPLATES = {
    "pagina_web": "pagina_web.j2",
    "tienda_online": "tienda_online.j2",
    "sistema_turnos": "sistema_turnos.j2",
    "cmr_nexo": "cmr_nexo.j2",
    "cmr_canchas": "cmr_canchas.j2",
    "mejora_web": "mejora_web.j2",
}
DEFAULT_TEMPLATE = "generico.j2"

BUSINESS_TYPE_LABELS = {
    "gastronomia": "gastronomía",
    "estetica": "estética",
    "salud": "salud",
    "deportes": "deportes",
    "indumentaria": "indumentaria",
    "inmobiliaria": "el rubro inmobiliario",
    "automotor": "el rubro automotor",
    "profesional": "servicios profesionales",
    "educacion": "educación",
    "eventos": "eventos",
    "comercio": "comercio",
}

_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    autoescape=False,
)


@dataclass
class MessageDraft:
    text: str
    channel: Channel
    link: str | None
    template: str


def _clean(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build_context(lead: Lead, settings: Settings) -> dict:
    return {
        "business": lead.display_name
        or (f"@{lead.instagram_username}" if lead.instagram_username else "tu negocio"),
        "rubro": BUSINESS_TYPE_LABELS.get(lead.business_type or ""),
        "has_website": bool(lead.website) and not lead.has_link_in_bio_only,
        "link_in_bio_only": lead.has_link_in_bio_only,
        "is_local": bool(lead.is_local),
        "sender": settings.sender_name,
        "company": settings.company_name,
        "city": settings.company_city,
    }


def generate_message(lead: Lead, settings: Settings) -> MessageDraft:
    template_name = SERVICE_TEMPLATES.get(lead.recommended_service or "", DEFAULT_TEMPLATE)
    text = _clean(_env.get_template(template_name).render(**build_context(lead, settings)))
    if lead.whatsapp_e164:
        return MessageDraft(
            text, Channel.WHATSAPP, whatsapp_link(lead.whatsapp_e164, text), template_name
        )
    if lead.instagram_username:
        return MessageDraft(
            text, Channel.INSTAGRAM_DM, instagram_dm_link(lead.instagram_username), template_name
        )
    if lead.email:
        return MessageDraft(text, Channel.EMAIL, f"mailto:{lead.email}", template_name)
    if lead.phone_e164:
        return MessageDraft(text, Channel.PHONE, f"tel:{lead.phone_e164}", template_name)
    return MessageDraft(text, Channel.OTHER, None, template_name)

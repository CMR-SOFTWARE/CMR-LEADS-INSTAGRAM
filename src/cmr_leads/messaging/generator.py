"""Genera un BORRADOR de mensaje para revisión humana. Nunca envía nada."""

import re
from collections import defaultdict
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


APPOINTMENT_HINTS = ("turno", "turnos", "reserva", "reservas", "agenda", "cita")
ORDER_HINTS = ("pedido", "pedidos", "envio", "envios", "delivery", "encarga")


def detect_problem(lead: Lead) -> str:
    """Clave del problema detectado (ver `problemas:` en config/plantillas.yaml)."""
    from cmr_leads.pipeline.normalize import normalize_text

    bio = normalize_text(lead.bio)
    by_message = any(w in bio for w in ("dm", "mensaje", "md", "whatsapp", "wsp", "privado"))
    if by_message and any(w in bio for w in APPOINTMENT_HINTS):
        return "turnos_por_mensaje"
    if by_message and any(w in bio for w in ORDER_HINTS):
        return "pedidos_por_mensaje"
    if lead.has_link_in_bio_only:
        return "link_en_bio"
    if not lead.website:
        return "solo_instagram" if lead.instagram_username else "sin_web"
    signals = (lead.raw_data or {}).get("website") or {}
    if signals and not signals.get("reachable"):
        return "web_caida"
    if signals.get("mobile_viewport") is False or signals.get("https") is False:
        return "web_vieja"
    return "generico"


def _load_yaml_templates(path) -> dict:
    import yaml

    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


def render_from_config(lead: Lead, settings: Settings) -> tuple[str, str] | None:
    """Plantilla de config/plantillas.yaml por plan + rubro. None si no hay archivo."""
    config = _load_yaml_templates(settings.templates_path)
    planes = config.get("planes") or {}
    if not planes:
        return None
    plan = lead.recommended_service or "generico"
    by_rubro = (config.get("rubros") or {}).get(plan) or {}
    template = by_rubro.get(lead.business_type or "") or planes.get(plan) or planes.get("generico")
    if not template:
        return None
    problems = config.get("problemas") or {}
    key = detect_problem(lead)
    values = {
        "nombre": lead.display_name or "su negocio",
        "rubro": BUSINESS_TYPE_LABELS.get(lead.business_type or "", "su rubro"),
        "problema_detectado": problems.get(key) or problems.get("generico", ""),
        "empresa": settings.company_name,
        "ciudad": settings.company_city,
    }

    class _Safe(dict):
        def __missing__(self, k):  # una variable mal escrita no rompe el mensaje
            return "{" + k + "}"

    name = f"{plan}/{lead.business_type}" if lead.business_type in by_rubro else plan
    return " ".join(template.split()).format_map(_Safe(values)), name


def follow_up_message(lead: Lead, settings: Settings, number: int) -> str:
    """Mensaje de seguimiento (1 = día 3, 2 = día 7) desde config/plantillas.yaml."""
    config = _load_yaml_templates(settings.templates_path)
    texts = config.get("seguimientos") or {}
    template = texts.get(number) or texts.get(str(number)) or texts.get(1) or ""
    if not template:
        return f"Hola, ¿cómo andan? Les escribí hace unos días por {lead.display_name or 'su negocio'}."
    values = {
        "nombre": lead.display_name or "su negocio",
        "rubro": BUSINESS_TYPE_LABELS.get(lead.business_type or "", "su rubro"),
        "empresa": settings.company_name,
        "ciudad": settings.company_city,
    }
    return " ".join(template.split()).format_map(defaultdict(str, values))


def generate_message(lead: Lead, settings: Settings) -> MessageDraft:
    rendered = render_from_config(lead, settings)
    if rendered:
        text, template_name = rendered
    else:
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

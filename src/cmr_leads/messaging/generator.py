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


# Profesionales que trabajan solos: se les habla de vos, no de "ustedes".
_INDIVIDUAL = re.compile(
    r"\b(lic|licenciad[ao]|dra|dr|psic|psicolog[ao]|psicopedagog[ao]|kinesiolog[ao]|"
    r"nutricionista|odontolog[ao]|fonoaudiolog[ao]|podolog[ao]|cosmiatra|cosmetolog[ao]|"
    r"terapeuta|abogad[ao]|contador[a]?|arq|arquitect[ao])\b"
)
_ALTERNATIVE = re.compile(r"\{([^{}|]*)\|([^{}|]*)\}")
MAX_WORDS = 45


def is_individual(lead: Lead) -> bool:
    from cmr_leads.pipeline.normalize import normalize_text

    return bool(_INDIVIDUAL.search(normalize_text(lead.display_name)))


def apply_style(text: str) -> str:
    """Reglas de estilo de CMR: preguntas solo con "?" (sin "¿" ni "¡") y espacios prolijos."""
    text = text.replace("¿", "").replace("¡", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" +([?!.,:;])", r"\1", text)
    return text.strip()


def word_count(text: str) -> int:
    return len(text.split())


def template_key(lead: Lead) -> str:
    """Qué plantilla base le toca según rubro y servicio (ver config/plantillas.yaml)."""
    service = lead.recommended_service or ""
    rubro = lead.business_type or ""
    if service == "cmr_canchas":
        return "canchas"
    if rubro in ("estetica", "salud", "gastronomia"):
        return rubro
    if rubro == "deportes":
        return "gimnasio"
    if service in ("cmr_nexo", "sistema_turnos"):
        return "turnos"
    if service == "mejora_web":
        return "mejora_web"
    if not lead.website or lead.has_link_in_bio_only:
        return "comercio_sin_web"
    return "generico"


def _fill(template: str, lead: Lead, firma: str) -> str:
    singular = is_individual(lead)
    text = " ".join(template.split())
    text = _ALTERNATIVE.sub(lambda m: m.group(2 if singular else 1), text)

    class _Safe(dict):
        def __missing__(self, k):  # una variable mal escrita no rompe el mensaje
            return "{" + k + "}"

    values = {"firma": firma, "nombre": lead.display_name or "su negocio"}
    return apply_style(text.format_map(_Safe(values)))


def _variant(lead: Lead, options: dict) -> tuple[str, str]:
    """A/B: a cada lead le toca siempre la misma versión (según su número)."""
    keys = sorted(str(k) for k in options)
    key = keys[(lead.id or 0) % len(keys)]
    return key, options.get(key) or options.get(int(key) if key.isdigit() else key)


def render_from_config(lead: Lead, settings: Settings) -> tuple[str, str] | None:
    """Plantilla de config/plantillas.yaml por rubro, con versión A/B. None si no hay archivo."""
    config = _load_yaml_templates(settings.templates_path)
    plantillas = config.get("plantillas") or {}
    if not plantillas:
        return None
    key = template_key(lead)
    options = plantillas.get(key) or plantillas.get("generico")
    if not options:
        return None
    if key not in plantillas:
        key = "generico"
    if isinstance(options, str):
        options = {"a": options}
    variant, template = _variant(lead, options)
    firma = config.get("firma") or f"{settings.sender_name} de {settings.company_name}"
    return _fill(template, lead, firma), f"{key}/{variant}"


TEMPLATE_LABELS = {
    "estetica": "Estética",
    "gimnasio": "Gimnasio / pilates",
    "salud": "Salud",
    "gastronomia": "Gastronomía",
    "canchas": "Canchas",
    "comercio_sin_web": "Comercio sin web",
    "turnos": "Turnos",
    "mejora_web": "Renovar web",
    "generico": "Genérico",
    "seguimiento": "Seguimiento",
}


def template_label(name: str | None) -> str:
    """'estetica/a' → 'Estética · versión A' (para las estadísticas de respuestas)."""
    if not name:
        return "sin registrar"
    if name == "ia":
        return "Redactado con IA"
    key, _, variant = name.partition("/")
    if key not in TEMPLATE_LABELS:
        return f"{name} (anterior)"  # plantillas viejas, de antes del A/B
    label = TEMPLATE_LABELS[key]
    if key == "seguimiento":
        return f"{label} {variant}"
    return f"{label} · versión {variant.upper()}" if variant else label


def follow_up_message(lead: Lead, settings: Settings, number: int) -> str:
    """Mensaje de seguimiento (1 = día 3, 2 = día 7) desde config/plantillas.yaml."""
    config = _load_yaml_templates(settings.templates_path)
    texts = config.get("seguimientos") or {}
    template = texts.get(number) or texts.get(str(number)) or texts.get(1) or ""
    if not template:
        name = lead.display_name or "su negocio"
        return f"Hola, cómo andan? Les escribí hace unos días por {name}."
    firma = config.get("firma") or f"{settings.sender_name} de {settings.company_name}"
    return _fill(template, lead, firma)


def generate_message(lead: Lead, settings: Settings) -> MessageDraft:
    rendered = render_from_config(lead, settings)
    if rendered:
        text, template_name = rendered
    else:
        template_name = SERVICE_TEMPLATES.get(lead.recommended_service or "", DEFAULT_TEMPLATE)
        text = _clean(_env.get_template(template_name).render(**build_context(lead, settings)))
        text = apply_style(text)
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

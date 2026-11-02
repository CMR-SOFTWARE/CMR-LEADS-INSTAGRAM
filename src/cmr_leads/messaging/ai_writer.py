"""Redacta un BORRADOR de primer mensaje con Gemini. Nunca envía nada: lo revisa una persona."""

import re

import httpx

from cmr_leads.db.models import Lead
from cmr_leads.settings import Settings

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
FALLBACK_MODELS = ("gemini-3.5-flash", "gemini-3.1-flash-lite")
BUSY_STATUSES = (429, 503)

SERVICES = {
    "cmr_nexo": "CMR Nexo (turnos online para negocios de servicios)",
    "cmr_canchas": "CMR Canchas (reservas online de canchas)",
    "pagina_web": "Plan Web (página web propia)",
    "tienda_online": "Plan Gestión (catálogo y pedidos online)",
    "sistema_turnos": "Plan Gestión con turnos online",
    "mejora_web": "renovar su web actual con el Plan Web",
    "sistema_a_medida": "un sistema a medida para su gestión",
}

SYSTEM_PROMPT = """\
Escribís el PRIMER mensaje de WhatsApp que alguien de {company}, una empresa de software de \
{city}, le manda a un negocio local que no lo conoce.

Esto es lo que vendemos (usalo para elegir qué ofrecer y cómo contarlo):
<catalogo>
{catalog}
</catalogo>

Tiene que sonar como un vecino que les escribe desde el celular, no como una empresa ni una IA:
- Español rioplatense y bien coloquial, tratando al negocio de "ustedes".
- Entre 2 y 3 oraciones, máximo 50 palabras.
- Saludo corto y natural. NO digas tu nombre ni "soy de…": a lo sumo "les escribo de {company}, \
acá de {city}" o directamente entrá en tema.
- Mencioná UNA observación concreta sobre su negocio que esté en la ficha (por ejemplo: que no \
tienen web, que su web no se adapta al celular, que se manejan solo con Instagram).
- Ofrecé UN solo producto del catálogo, el que más le sirva según su rubro (la ficha trae una \
sugerencia). Nombralo si es CMR Nexo o CMR Canchas, y contá qué ganarían ellos en el día a día.
- Si en "Clientes" del catálogo hay uno del mismo rubro, podés nombrarlo como ejemplo. Si no hay, \
no nombres ninguno.
- No pongas precios, links ni teléfonos. Para CMR Nexo podés mencionar la promo de lanzamiento.
- Cerrá con una pregunta corta y sin presión.
- Prohibido: emojis, listas, comillas, hashtags, firmas, "espero que estés bien", "potenciar", \
"llevar al siguiente nivel", "solución integral", "transformación digital", "presencia digital", \
"no dudes en", "me encantaría".
- Nunca menciones estrellas, calificaciones ni reseñas de Google.
- No inventes nada que no esté en la ficha o el catálogo (ni seguidores, ni actividad en redes). \
No elogies cosas que no figuren en la ficha y no afirmes que no tienen turnos online, tienda o \
reservas si la ficha no lo dice.

Respondé SOLO con el texto del mensaje."""


class AiWriterError(Exception):
    pass


def describe_website(lead: Lead) -> str:
    """Resume en una frase la situación web del negocio para darle contexto al modelo."""
    if lead.has_link_in_bio_only:
        return "No tiene web propia: solo un link tipo Linktree."
    if not lead.website:
        if lead.instagram_username:
            return "No tiene web propia: usa Instagram como vidriera."
        return "No tiene web ni Instagram conocidos."
    signals = (lead.raw_data or {}).get("website")
    if not signals:
        return f"Tiene web ({lead.website_domain or lead.website}), sin analizar."
    if not signals.get("reachable"):
        return f"Tiene una web cargada ({lead.website_domain}) pero no funciona."
    notes = []
    if signals.get("mobile_viewport") is False:
        notes.append("no está adaptada al celular")
    if signals.get("https") is False:
        notes.append("no tiene candado de seguridad (sin HTTPS)")
    if signals.get("ecommerce"):
        notes.append("ya vende online")
    detail = "; ".join(notes) if notes else "se ve correcta"
    return f"Tiene web ({lead.website_domain}): {detail}."


def build_lead_facts(lead: Lead) -> str:
    facts = [
        f"Negocio: {lead.display_name or '@' + (lead.instagram_username or '?')}",
        f"Rubro: {lead.category or lead.business_type or 'desconocido'}",
        f"Ciudad: {lead.city or 'desconocida'}",
        f"Web: {describe_website(lead)}",
    ]
    if lead.instagram_username:
        followers = f" ({lead.followers} seguidores)" if lead.followers else ""
        facts.append(f"Instagram: @{lead.instagram_username}{followers}")
    if lead.bio:
        facts.append(f"Bio: {lead.bio[:300]}")
    if service := SERVICES.get(lead.recommended_service or ""):
        facts.append(f"Lo que más le serviría (según nuestro análisis): {service}")
    return "\n".join(facts)


def load_catalog(settings: Settings) -> str:
    """Catálogo editable de productos, precios y clientes (config/catalogo.md)."""
    if settings.catalog_path.exists():
        return settings.catalog_path.read_text(encoding="utf-8").strip()
    return "Páginas web, tiendas online, sistemas de turnos y sistemas a medida."


def _clean(text: str) -> str:
    text = text.strip().strip('"').strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def generate(
    settings: Settings,
    system: str,
    user: str,
    generation_config: dict,
    http: httpx.Client | None = None,
) -> str:
    """Llama a Gemini y devuelve el texto. Si un modelo está saturado, prueba con el siguiente."""
    if not settings.gemini_api_key:
        raise AiWriterError("Falta GEMINI_API_KEY en el archivo .env")
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": generation_config,
    }
    client = http or httpx.Client(timeout=60)
    models = dict.fromkeys([settings.gemini_model, *FALLBACK_MODELS])
    for model in models:
        try:
            response = client.post(
                GEMINI_URL.format(model=model),
                json=body,
                headers={"x-goog-api-key": settings.gemini_api_key},
            )
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AiWriterError(f"No se pudo conectar con Gemini ({type(exc).__name__})") from exc
        # Modelo saturado o sin cupo: se prueba con el siguiente.
        if response.status_code not in BUSY_STATUSES:
            break
    if response.status_code >= 400 or "error" in payload:
        message = (payload.get("error") or {}).get("message", f"HTTP {response.status_code}")
        raise AiWriterError(f"Gemini: {message}")
    parts = ((payload.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text.strip():
        raise AiWriterError("Gemini no devolvió texto")
    return text


def write_message(lead: Lead, settings: Settings, http: httpx.Client | None = None) -> str:
    system = SYSTEM_PROMPT.format(
        company=settings.company_name, city=settings.company_city, catalog=load_catalog(settings)
    )
    text = generate(settings, system, build_lead_facts(lead), {"temperature": 0.9}, http)
    return _clean(text)

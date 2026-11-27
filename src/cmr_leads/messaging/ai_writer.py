"""Redacta un BORRADOR de primer mensaje con Gemini. Nunca envía nada: lo revisa una persona."""

import re

import httpx

from cmr_leads.db.models import Lead
from cmr_leads.settings import Settings

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
FALLBACK_MODELS = ("gemini-3.5-flash", "gemini-3.8-flash")
BUSY_STATUSES = (429, 503)
WHATSAPP_DOMAINS = ("wa.me", "wa.link", "whatsapp.com")
SOCIAL_DOMAINS = (
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
)

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
- Entre 3 y 4 oraciones, máximo 70 palabras.
- Saludo corto y natural. NO digas tu nombre ni "soy de…": a lo sumo "les escribo de {company}, \
acá de {city}" o directamente entrá en tema.
- Si la ficha trae "Qué ofrecerle (ya analizado)", el mensaje se arma sobre ESE análisis: \
ofrecé ese producto (no cambies a otro) y apoyate en el "Por qué": usá el dato concreto de su \
negocio que lo justifica (su trayectoria, que todo pasa por un Linktree, que no tienen web, etc.) \
y contá en concreto qué ganarían ellos con eso en el día a día (más clientes que los encuentran, \
consultas directas por WhatsApp, menos tiempo respondiendo, etc.).
- Si no hay análisis, mencioná UNA observación concreta de la ficha y ofrecé UN solo producto del \
catálogo, el que más le sirva según su rubro (la ficha trae una sugerencia).
- Si ofrecés el Plan Web, no prometas catálogo de productos, tienda ni carga de precios: eso es \
del Plan Gestión. Con el Plan Web hablá de que los encuentren en Google, vean quiénes son y les \
escriban por WhatsApp.
- Nombrá el producto si es CMR Nexo o CMR Canchas. Hablá de su rubro con sus palabras (una óptica \
habla de anteojos y clientes, una peluquería de turnos), no en abstracto.
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
        # Decir qué link es de verdad: si no, la IA inventa que "usan Linktree".
        domain = lead.website_domain or ""
        if any(domain == d or domain.endswith("." + d) for d in WHATSAPP_DOMAINS):
            return "No tiene web propia: el link que tiene cargado es su WhatsApp directo."
        if any(domain == d or domain.endswith("." + d) for d in SOCIAL_DOMAINS):
            return (
                f"No tiene web propia: el link que tiene cargado lleva a una red social ({domain})."
            )
        return f"No tiene web propia: solo una página de links ({domain})."
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


def build_lead_facts(lead: Lead, include_offer: bool = True) -> str:
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
        from cmr_leads.pipeline.normalize import redact_personal_data

        facts.append(f"Bio: {redact_personal_data(lead.bio)[:300]}")
    for item in lead.score_breakdown or []:
        if item.get("rule") == "need.competitor_system_points":
            facts.append(
                f"Ya usa un sistema de otra empresa: {item['reason']}. No le ofrezcas lo mismo "
                "como si no tuviera nada; si le escribís, preguntá cómo le funciona."
            )
    ai_offer = (lead.raw_data or {}).get("ai_offer") if include_offer else None
    if ai_offer:
        facts.append(f"Qué ofrecerle (ya analizado): {ai_offer['offer']}. {ai_offer['pitch']}")
        if ai_offer.get("reason"):
            facts.append(f"Por qué le sirve (ya analizado): {ai_offer['reason']}")
    elif service := SERVICES.get(lead.recommended_service or ""):
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
    # 20 s por modelo: si uno se cuelga, se pasa al siguiente en vez de dejar la pantalla esperando.
    client = http or httpx.Client(timeout=20)
    models = list(dict.fromkeys([settings.gemini_model, *FALLBACK_MODELS]))
    response, payload, failure = None, {}, None
    for model in models:
        try:
            response = client.post(
                GEMINI_URL.format(model=model),
                json=body,
                headers={"x-goog-api-key": settings.gemini_api_key},
            )
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            failure, response = exc, None  # sin respuesta (colgado o caído): probar el siguiente
            continue
        # Modelo saturado o sin cupo: se prueba con el siguiente.
        if response.status_code not in BUSY_STATUSES:
            break
    if response is None:
        raise AiWriterError(f"No se pudo conectar con Gemini ({type(failure).__name__})")
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


OFFER_PROMPT = """\
Sos el vendedor de {company} ({city}). Analizás un negocio local y decidís qué ofrecerle para \
que sea lo más probable que nos contrate. Usá SOLO lo que vendemos:
<catalogo>
{catalog}
</catalogo>

Reglas:
- Elegí la oferta que más le resuelva un problema real según su rubro y la ficha. Puede ser un \
plan tal cual, un producto (CMR Nexo, CMR Canchas) o una combinación armada a medida, \
siempre dentro del catálogo.
- El Plan Web es una web de presentación: quiénes son, qué hacen, ubicación, horarios, fotos y \
WhatsApp. NO tiene catálogo de productos, precios cargables, stock, carrito ni panel. Si la \
propuesta incluye mostrar sus productos en un catálogo, una tienda o pedidos, es Plan Gestión \
(necesita base de datos y panel para cargarlos). No mezcles: "Plan Web con catálogo" no existe.
- Si el negocio trabaja con turnos, reservas o clases (peluquería, estética, pilates, gimnasio, \
consultorio), ofrecé CMR Nexo, no Plan Gestión.
- Preferí lo más simple y barato que le sirva: es más fácil que acepten algo chico y después crecer.
- "probability" es la chance (0-100) de que contrate si le escribimos, según qué tan clara es su \
necesidad, si ya tiene web o sistema, si es un negocio activo y si hay forma de contactarlo.
- "pitch" es el argumento principal en una frase, en español rioplatense, sin precios.
- No inventes datos del negocio."""

OFFER_SCHEMA = {
    "type": "object",
    "properties": {
        "product": {"type": "string", "enum": list(SERVICES)},
        "offer": {"type": "string"},
        "pitch": {"type": "string"},
        "probability": {"type": "integer"},
        "reason": {"type": "string"},
    },
    "required": ["product", "offer", "pitch", "probability", "reason"],
}


def recommend_offer(lead: Lead, settings: Settings, http: httpx.Client | None = None) -> dict:
    """Pide a Gemini la oferta con más chances de venta. Devuelve el dict validado."""
    import json

    system = OFFER_PROMPT.format(
        company=settings.company_name, city=settings.company_city, catalog=load_catalog(settings)
    )
    raw = generate(
        settings,
        system,
        build_lead_facts(lead, include_offer=False),
        {
            "temperature": 0.2,
            "responseMimeType": "application/json",
            "responseJsonSchema": OFFER_SCHEMA,
        },
        http,
    )
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise AiWriterError("Gemini devolvió una respuesta inválida") from exc
    if data.get("product") not in SERVICES:
        data["product"] = lead.recommended_service
    data["probability"] = max(0, min(100, int(data.get("probability") or 0)))
    for key in ("offer", "pitch", "reason"):
        data[key] = str(data.get(key) or "")[:300]
    return data


def apply_offer(lead: Lead, offer: dict) -> None:
    """Guarda la recomendación de la IA en el lead (no pisa la del algoritmo de reglas)."""
    lead.ai_score = offer["probability"]
    if offer.get("product"):
        lead.recommended_service = offer["product"]
    lead.ai_summary = f"{offer['offer']} — {offer['reason']}"
    lead.raw_data = {**(lead.raw_data or {}), "ai_offer": offer}

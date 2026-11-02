"""Clasificación de respuestas de leads: reglas siempre; IA (Claude) opcional.

Categorías:
- interesado: quiere avanzar (precio + ganas, "me interesa", pide reunión)
- pide_info: pregunta precio, plazos, ejemplos
- ahora_no: "más adelante", "por ahora no"
- no_interesa: rechazo
- no_contactar: pide no recibir más mensajes
- automatica: respuesta automática de WhatsApp Business / fuera de horario
- otro
"""

import re
from dataclasses import asdict, dataclass, field

from cmr_leads.pipeline.normalize import normalize_text

CATEGORIES = {
    "interesado": "Interesado",
    "pide_info": "Pide info / precio",
    "ahora_no": "Ahora no",
    "no_interesa": "No le interesa",
    "no_contactar": "No volver a escribir",
    "automatica": "Respuesta automática",
    "otro": "Otro",
}
POSITIVE = {"interesado", "pide_info"}
NEGATIVE = {"no_interesa", "no_contactar"}

OBJECTIONS = {
    "precio": "Precio / presupuesto",
    "ya_tiene": "Ya tiene web o sistema",
    "no_necesita": "No lo necesita",
    "sin_tiempo": "Sin tiempo ahora",
    "consultar": "Lo tiene que consultar",
    "desconfianza": "Desconfianza / no conoce a CMR",
}

# El orden importa: la primera regla que matchea gana.
CATEGORY_RULES: list[tuple[str, list[str]]] = [
    (
        "no_contactar",
        [
            "no me escribas",
            "no me escriban",
            "no escriban mas",
            "no me manden",
            "dejen de escribir",
            "borrame",
            "eliminen mi numero",
            "no molesten",
            "spam",
            "denunciar",
            "bloquear",
        ],
    ),
    (
        "automatica",
        [
            "respuesta automatica",
            "gracias por comunicarte",
            "gracias por escribirnos",
            "en breve te respondemos",
            "te responderemos a la brevedad",
            "fuera de horario",
            "nuestro horario de atencion",
            "mensaje automatico",
        ],
    ),
    (
        "no_interesa",
        [
            "no me interesa",
            "no nos interesa",
            "no gracias",
            "no, gracias",
            "no estamos interesados",
            "no necesitamos",
            "no necesito",
            "no estoy interesado",
            "no estoy interesada",
        ],
    ),
    (
        "ahora_no",
        [
            "mas adelante",
            "por ahora no",
            "en otro momento",
            "ahora no",
            "el ano que viene",
            "proximo mes",
            "despues te escribo",
            "lo vemos despues",
            "ahora estoy con",
            "no es el momento",
            "te aviso",
        ],
    ),
    (
        "interesado",
        [
            "me interesa",
            "nos interesa",
            "me encantaria",
            "dale",
            "coordinemos",
            "podemos hablar",
            "llamame",
            "llamanos",
            "cuando podemos",
            "reunion",
            "pasate",
            "vení",
            "veni",
            "arranquemos",
            "hagamoslo",
            "quiero hacer",
            "queremos hacer",
            "si, contame",
            "si contame",
        ],
    ),
    (
        "pide_info",
        [
            "precio",
            "cuanto sale",
            "cuanto cuesta",
            "cuanto cobran",
            "costo",
            "presupuesto",
            "valor",
            "trabajos",
            "ejemplos",
            "como funciona",
            "que incluye",
            "cuanto tarda",
            "plazo",
            "contame",
            "info",
            "informacion",
            "?",
        ],
    ),
]

OBJECTION_RULES: dict[str, list[str]] = {
    "precio": [
        "caro",
        "muy caro",
        "presupuesto",
        "no tengo plata",
        "no hay plata",
        "economic",
        "costo",
    ],
    "ya_tiene": [
        "ya tengo",
        "ya tenemos",
        "ya trabajo con",
        "ya trabajamos con",
        "tenemos pagina",
        "tenemos web",
        "mi sobrino",
        "un conocido",
    ],
    "no_necesita": [
        "no necesito",
        "no necesitamos",
        "no hace falta",
        "me arreglo con",
        "con instagram me alcanza",
        "con whatsapp me alcanza",
    ],
    "sin_tiempo": [
        "no tengo tiempo",
        "estoy a full",
        "estamos a full",
        "complicado ahora",
        "temporada",
    ],
    "consultar": [
        "consultarlo",
        "consultar con",
        "hablarlo con",
        "lo hablo con",
        "mi socio",
        "mi jefe",
        "el dueno",
    ],
    "desconfianza": [
        "quien sos",
        "quien te paso",
        "de donde sacaste",
        "como conseguiste",
        "no te conozco",
    ],
}


@dataclass
class ReplyAnalysis:
    category: str
    objections: list[str] = field(default_factory=list)
    summary: str = ""
    suggested_action: str = ""
    source: str = "reglas"

    def to_json(self) -> dict:
        return asdict(self)


SUGGESTED_ACTIONS = {
    "interesado": "Responder hoy y proponer una llamada o reunión.",
    "pide_info": "Mandar precios orientativos y 2-3 trabajos parecidos a su rubro.",
    "ahora_no": "Agendar seguimiento en 2-3 meses.",
    "no_interesa": "Agradecer y cerrar. No insistir.",
    "no_contactar": "Disculparse si corresponde y no volver a escribir.",
    "automatica": "Esperar la respuesta de una persona; seguimiento en 3 días.",
    "otro": "Leer y responder a mano.",
}


def _has(text: str, phrase: str) -> bool:
    phrase = normalize_text(phrase)
    if phrase == "?":
        return "?" in text
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def classify_with_rules(text: str) -> ReplyAnalysis:
    normalized = normalize_text(text)
    category = "otro"
    for name, phrases in CATEGORY_RULES:
        if any(_has(normalized, p) for p in phrases):
            category = name
            break
    objections = [
        key for key, phrases in OBJECTION_RULES.items() if any(_has(normalized, p) for p in phrases)
    ]
    summary = re.sub(r"\s+", " ", text).strip()
    if len(summary) > 140:
        summary = summary[:137] + "…"
    return ReplyAnalysis(
        category=category,
        objections=objections,
        summary=summary,
        suggested_action=SUGGESTED_ACTIONS[category],
    )


class ReplyClassifier:
    """Usa la IA si está configurada; si falla o no está, usa reglas."""

    def __init__(self, ai=None) -> None:
        self.ai = ai

    def classify(self, text: str, context: dict | None = None) -> ReplyAnalysis:
        if self.ai is not None:
            try:
                result = self.ai.classify(text, context or {})
                if result is not None:
                    return result
            except Exception:  # noqa: BLE001 - la IA nunca debe frenar el registro de la respuesta
                pass
        return classify_with_rules(text)

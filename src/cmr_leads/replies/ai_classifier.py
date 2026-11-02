"""Clasificación de respuestas con IA: Gemini (GEMINI_API_KEY) o Claude (ANTHROPIC_API_KEY)."""

import json

from cmr_leads.replies.classifier import CATEGORIES, OBJECTIONS, ReplyAnalysis

SYSTEM_PROMPT = """Clasificás respuestas que dueños de negocios de Argentina le mandan a CMR Software \
Solutions (desarrollo de páginas web, sistemas y apps) después de un primer mensaje comercial por \
WhatsApp o Instagram.

Categorías:
- interesado: quiere avanzar, pide llamada o reunión, dice que sí.
- pide_info: pregunta precio, plazos, ejemplos o cómo funciona, sin comprometerse.
- ahora_no: le interesa pero más adelante.
- no_interesa: rechaza.
- no_contactar: pide que no le escriban más o se molesta por el mensaje.
- automatica: respuesta automática del negocio (bienvenida, fuera de horario).
- otro: cualquier otra cosa.

Objeciones posibles (cero o más): precio, ya_tiene, no_necesita, sin_tiempo, consultar, desconfianza.

"summary": una frase corta en español rioplatense con lo que dijo.
"suggested_action": el próximo paso concreto para CMR, en una frase. Si la categoría es \
no_contactar o no_interesa, el paso es no insistir."""

SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": list(CATEGORIES)},
        "objections": {"type": "array", "items": {"type": "string", "enum": list(OBJECTIONS)}},
        "summary": {"type": "string"},
        "suggested_action": {"type": "string"},
    },
    "required": ["category", "objections", "summary", "suggested_action"],
    "additionalProperties": False,
}


def _user_prompt(text: str, context: dict) -> str:
    context_lines = "\n".join(f"{k}: {v}" for k, v in context.items() if v)
    return (
        f"Datos del negocio:\n{context_lines or '(sin datos)'}\n\n"
        f"Respuesta recibida:\n<respuesta>\n{text}\n</respuesta>"
    )


def _to_analysis(data: dict) -> ReplyAnalysis | None:
    if data.get("category") not in CATEGORIES:
        return None
    return ReplyAnalysis(
        category=data["category"],
        objections=[o for o in data.get("objections", []) if o in OBJECTIONS],
        summary=data.get("summary", "")[:300],
        suggested_action=data.get("suggested_action", "")[:300],
        source="ia",
    )


class GeminiReplyClassifier:
    def __init__(self, settings, http=None) -> None:
        self.settings = settings
        self.http = http

    def classify(self, text: str, context: dict) -> ReplyAnalysis | None:
        from cmr_leads.messaging.ai_writer import generate

        schema = {k: v for k, v in SCHEMA.items() if k != "additionalProperties"}
        raw = generate(
            self.settings,
            SYSTEM_PROMPT,
            _user_prompt(text, context),
            {
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
            },
            self.http,
        )
        return _to_analysis(json.loads(raw))


class ClaudeReplyClassifier:
    def __init__(self, api_key: str, model: str = "claude-opus-5-5", client=None) -> None:
        import anthropic

        self.model = model
        self.client = client or anthropic.Anthropic(api_key=api_key, timeout=30.0)

    def classify(self, text: str, context: dict) -> ReplyAnalysis | None:
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=1024,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _user_prompt(text, context)}],
        )
        if response.stop_reason == "refusal":
            return None
        raw = next((b.text for b in response.content if b.type == "text"), None)
        if not raw:
            return None
        return _to_analysis(json.loads(raw))

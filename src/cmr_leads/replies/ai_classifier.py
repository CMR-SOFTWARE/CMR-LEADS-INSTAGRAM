"""Clasificación de respuestas con Claude (opcional, requiere ANTHROPIC_API_KEY)."""

import json

import anthropic

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


class ClaudeReplyClassifier:
    def __init__(self, api_key: str, model: str = "claude-opus-5-5", client=None) -> None:
        self.model = model
        self.client = client or anthropic.Anthropic(api_key=api_key, timeout=30.0)

    def classify(self, text: str, context: dict) -> ReplyAnalysis | None:
        context_lines = "\n".join(f"{k}: {v}" for k, v in context.items() if v)
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=1024,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"Datos del negocio:\n{context_lines or '(sin datos)'}\n\n"
                    f"Respuesta recibida:\n<respuesta>\n{text}\n</respuesta>",
                }
            ],
        )
        if response.stop_reason == "refusal":
            return None
        raw = next((b.text for b in response.content if b.type == "text"), None)
        if not raw:
            return None
        data = json.loads(raw)
        return ReplyAnalysis(
            category=data["category"],
            objections=[o for o in data.get("objections", []) if o in OBJECTIONS],
            summary=data.get("summary", "")[:300],
            suggested_action=data.get("suggested_action", "")[:300],
            source="ia",
        )

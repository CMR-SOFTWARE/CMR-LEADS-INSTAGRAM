"""Análisis de los números filtrados (Publicidad o Instagram) con Gemini: qué decisión tomar.

Solo se mandan números agregados y nombres de campañas o publicaciones: nunca datos de leads.
"""

import json

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from cmr_leads.db.models import Advice, utcnow
from cmr_leads.messaging.ai_writer import AiWriterError, generate
from cmr_leads.settings import Settings

COMMON_RULES = """\
Reglas:
- Español rioplatense, directo y sin tecnicismos. Frases cortas.
- Basate SOLO en los números que te paso. No inventes datos ni promedios del mercado.
- Si hay pocos datos para concluir algo, decilo en "cuidado" y no lo afirmes.
- Cada acción tiene que ser concreta (qué hacer, con qué, cuánto o cuándo) y su porqué tiene que \
citar un número del resumen.
- Nunca uses la raya larga. No menciones inteligencia artificial ni modelos.
- Montos en pesos argentinos con punto de miles (por ejemplo $12.500)."""

PROMPTS = {
    "publicidad": """\
Sos el responsable de la publicidad paga en Instagram y Facebook de {company}, una empresa de \
software de {city}.

PRIORIDAD DEL NEGOCIO: el ALCANCE, o sea llegar a la mayor cantidad de personas únicas al menor \
costo ("costo_cada_1000_personas"). Después importan las interacciones y las visitas al perfil.

Cada campaña se paga para un objetivo distinto ("se_pago_para": visitas al perfil, mensajes, \
alcance, interacción, etc.) y Meta la optimiza para eso. Evaluá cada una por SU objetivo y por su \
alcance: nunca critiques una campaña de visitas al perfil por no traer mensajes. Los mensajes son \
pocos y pueden ser accidentales: con menos de 10 mensajes no saques conclusiones de ellos.

Con los números del período (campañas, anuncios y, si vienen, el rendimiento orgánico de cada \
formato en Instagram) decidí DÓNDE CONVIENE PONER LA PLATA para llegar a más gente: qué escalar, \
qué cambiar, qué pausar y cuánto presupuesto mover. Si el orgánico muestra un formato que llega a \
mucha más gente, proponé usarlo en los anuncios. Si viene "que_promocionar", decí qué publicación conviene promocionar (o volver a promocionar) y cuál no repetir, con su motivo.

"""
    + COMMON_RULES,
    "instagram": """\
Sos el responsable de las redes de {company}, una empresa de software de {city}. Analizás el \
Instagram de la empresa para conseguir clientes (que visiten el perfil, toquen el link y escriban).

Con los números del período decidí QUÉ PUBLICAR, CUÁNDO Y CON QUÉ FRECUENCIA: qué formato rinde \
más, qué día y en qué franja horaria conviene subir cada formato, cuántas historias por día y qué \
hacer para que más gente toque el link o el botón de contacto. Si viene "que_promocionar", mencioná qué publicación conviene promocionar y por qué.

"""
    + COMMON_RULES,
}

SCHEMA = {
    "type": "object",
    "properties": {
        "resumen": {"type": "string", "description": "2 o 3 frases con lo más importante"},
        "decision": {"type": "string", "description": "la decisión principal, en una frase"},
        "acciones": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"accion": {"type": "string"}, "porque": {"type": "string"}},
                "required": ["accion", "porque"],
            },
        },
        "cuidado": {"type": "string", "description": "límites del análisis, puede ir vacío"},
    },
    "required": ["resumen", "decision", "acciones", "cuidado"],
}


def _clean(text) -> str:
    return " ".join(str(text or "").replace("—", ", ").replace("–", "-").split())[:600]


def get_advice(session: Session, workspace_id: int, scope: str, key: str) -> Advice | None:
    return session.scalar(
        select(Advice).where(
            Advice.workspace_id == workspace_id, Advice.scope == scope, Advice.key == key
        )
    )


def request_advice(
    session: Session,
    workspace_id: int,
    settings: Settings,
    scope: str,
    key: str,
    facts: dict,
    http: httpx.Client | None = None,
) -> Advice:
    system = PROMPTS[scope].format(company=settings.company_name, city=settings.company_city)
    raw = generate(
        settings,
        system,
        "Números del período (JSON):\n" + json.dumps(facts, ensure_ascii=False, default=str),
        {"temperature": 0.3, "responseMimeType": "application/json", "responseJsonSchema": SCHEMA},
        http,
    )
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise AiWriterError("La respuesta del análisis vino incompleta, probá de nuevo") from exc
    data = {
        "resumen": _clean(parsed.get("resumen")),
        "decision": _clean(parsed.get("decision")),
        "acciones": [
            {"accion": _clean(a.get("accion")), "porque": _clean(a.get("porque"))}
            for a in (parsed.get("acciones") or [])[:6]
            if isinstance(a, dict) and a.get("accion")
        ],
        "cuidado": _clean(parsed.get("cuidado")),
    }
    advice = get_advice(session, workspace_id, scope, key)
    if advice is None:
        advice = Advice(workspace_id=workspace_id, scope=scope, key=key, data=data)
        session.add(advice)
    advice.data = data
    advice.created_at = utcnow()
    session.flush()
    return advice

"""Para qué se pagó cada campaña y con qué número se mide.

Meta cobra distinto según el objetivo: no se puede medir una campaña de visitas al perfil por los
mensajes que trajo. Cada fila se evalúa con el resultado de su objetivo, y el alcance (personas
únicas) es el número que se compara entre todas.
"""

from dataclasses import dataclass

MESSAGE_DESTINATIONS = {
    "INSTAGRAM_DIRECT",
    "MESSENGER",
    "WHATSAPP",
    "MESSAGING_INSTAGRAM_DIRECT_MESSENGER",
    "MESSAGING_INSTAGRAM_DIRECT_WHATSAPP",
    "MESSAGING_MESSENGER_WHATSAPP",
    "MESSAGING_INSTAGRAM_DIRECT_MESSENGER_WHATSAPP",
}
REACH_GOALS = {"REACH", "IMPRESSIONS", "AD_RECALL_LIFT"}
REACH_OBJECTIVES = {"OUTCOME_AWARENESS", "REACH", "BRAND_AWARENESS"}
VIDEO_GOALS = {"THRUPLAY", "TWO_SECOND_CONTINUOUS_VIDEO_VIEWS"}
ENGAGEMENT_GOALS = {"POST_ENGAGEMENT", "PAGE_LIKES", "EVENT_RESPONSES"}
TRAFFIC_GOALS = {"LINK_CLICKS", "LANDING_PAGE_VIEWS"}


@dataclass(frozen=True)
class Objective:
    key: str
    label: str  # para qué se pagó
    result: str  # nombre del resultado
    unit: str  # "costo por ..."
    attr: str  # campo de Totals con el resultado


OBJECTIVES = {
    "alcance": Objective("alcance", "Alcance", "Personas alcanzadas", "1.000 personas", "reach"),
    "visitas_perfil": Objective(
        "visitas_perfil", "Visitas al perfil", "Visitas al perfil", "visita", "link_clicks"
    ),
    "trafico": Objective("trafico", "Tráfico", "Clics al enlace", "clic", "link_clicks"),
    "interaccion": Objective(
        "interaccion", "Interacción", "Interacciones", "interacción", "engagement"
    ),
    "video": Objective("video", "Reproducciones", "Reproducciones", "reproducción", "video_views"),
    "mensajes": Objective("mensajes", "Mensajes", "Mensajes", "mensaje", "messages"),
}


def objective_key(
    campaign_objective: str | None, optimization_goal: str | None, destination: str | None
) -> str:
    """Traduce objetivo de campaña + objetivo de optimización + destino a una clave propia."""
    goal = (optimization_goal or "").upper()
    dest = (destination or "").upper()
    objective = (campaign_objective or "").upper()
    if dest in MESSAGE_DESTINATIONS or goal == "CONVERSATIONS" or objective == "MESSAGES":
        return "mensajes"
    if goal == "VISIT_INSTAGRAM_PROFILE" or dest == "INSTAGRAM_PROFILE":
        return "visitas_perfil"
    if goal in REACH_GOALS or objective in REACH_OBJECTIVES:
        return "alcance"
    if goal in VIDEO_GOALS or objective == "VIDEO_VIEWS":
        return "video"
    if goal in ENGAGEMENT_GOALS or objective in ("OUTCOME_ENGAGEMENT", "POST_ENGAGEMENT"):
        return "interaccion"
    if goal in TRAFFIC_GOALS or objective in ("LINK_CLICKS", "OUTCOME_TRAFFIC"):
        return "trafico"
    return "alcance"


def result_of(key: str, totals) -> int | None:
    return getattr(totals, OBJECTIVES[key].attr)


def cost_per_result(key: str, totals) -> float | None:
    """Costo por resultado; para alcance, costo cada 1.000 personas alcanzadas."""
    value = result_of(key, totals)
    if not value or not totals.spend:
        return None
    if key == "alcance":
        return totals.spend / value * 1000
    return totals.spend / value

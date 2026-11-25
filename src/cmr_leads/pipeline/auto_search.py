"""Búsqueda automática: al abrir la app busca unos pocos rubros/localidades por día.

Va recorriendo rubro × localidad en orden de prioridad (primero los rubros donde mejor encajan
nuestros productos y primero San Nicolás). Cada combinación se vuelve a buscar recién después de
`REFRESH_DAYS`, para traer negocios nuevos sin gastar consultas de más. El estado se guarda en un
JSON chico en `data/`.
"""

import json
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from cmr_leads.db.models import LeadSource, Workspace
from cmr_leads.domain.enums import SourceType
from cmr_leads.pipeline.google_search import run_google_search
from cmr_leads.pipeline.scoring import ScoringConfig
from cmr_leads.sources.google_places import SUGGESTED_AREAS, SUGGESTED_QUERIES

REFRESH_DAYS = 60
# Rubros donde mejor encajan CMR Nexo, CMR Canchas y los planes: se buscan primero.
PRIORITY_QUERIES = (
    "peluquería",
    "barbería",
    "centro de estética",
    "cancha de pádel",
    "cancha de fútbol 5",
    "complejo deportivo",
    "gimnasio",
    "consultorio odontológico",
    "kinesiología",
    "psicólogo",
    "nutricionista",
    "uñas",
    "masajes",
    "pilates",
    "tienda de ropa",
)


@dataclass
class AutoSearchResult:
    ran: bool = False
    searches: list[str] = field(default_factory=list)
    created: int = 0
    existing: int = 0
    stopped_reason: str | None = None

    def summary(self) -> str:
        if not self.ran:
            return self.stopped_reason or "sin búsquedas pendientes"
        text = f"{len(self.searches)} búsquedas: {self.created} nuevos, {self.existing} ya estaban"
        if self.stopped_reason:
            text += f" · {self.stopped_reason}"
        return text


def search_order() -> list[tuple[str, str]]:
    queries = list(dict.fromkeys([*PRIORITY_QUERIES, *SUGGESTED_QUERIES]))
    return [(q, a) for a in SUGGESTED_AREAS for q in queries]


def _key(query: str, area: str) -> str:
    return f"{query}|{area}"


def load_state(path) -> dict:
    """`path` es un archivo JSON o un DbState (estado guardado en la base)."""
    if hasattr(path, "load"):
        return path.load()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(path, state: dict) -> None:
    if hasattr(path, "save"):
        path.save(state)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def plan_searches(done: dict[str, str], today: date, per_day: int) -> list[tuple[str, str]]:
    """Primero lo nunca buscado (en orden de prioridad); después lo más viejo que REFRESH_DAYS."""
    order = search_order()
    never = [c for c in order if _key(*c) not in done]
    if len(never) >= per_day:
        return never[:per_day]
    limit = (today - timedelta(days=REFRESH_DAYS)).isoformat()
    stale = sorted(
        (c for c in order if _key(*c) in done and done[_key(*c)] <= limit),
        key=lambda c: done[_key(*c)],
    )
    return (never + stale)[:per_day]


def _manual_searches(session: Session, workspace_id: int, default_area: str) -> set[str]:
    """Rubros que ya se buscaron a mano: se toman como hechos para la zona por defecto."""
    from cmr_leads.db.models import Lead

    refs = session.scalars(
        select(LeadSource.source_ref)
        .join(Lead, Lead.id == LeadSource.lead_id)
        .where(Lead.workspace_id == workspace_id, LeadSource.source == SourceType.GOOGLE_PLACES)
        .distinct()
    )
    return {_key(ref, default_area) for ref in refs if ref}


def run_auto_search(
    session: Session,
    workspace: Workspace,
    client,
    state_path,
    today: date,
    per_day: int,
    default_area: str,
    daily_limit: int,
    monthly_limit: int,
    country: str,
    config: ScoringConfig,
) -> AutoSearchResult:
    result = AutoSearchResult()
    state = load_state(state_path)
    if state.get("last_run") == today.isoformat():
        result.stopped_reason = "ya se buscó hoy"
        return result
    done: dict[str, str] = state.setdefault("done", {})
    if not state.get("seeded"):
        for key in _manual_searches(session, workspace.id, default_area):
            done.setdefault(key, today.isoformat())
        state["seeded"] = True

    for query, area in plan_searches(done, today, per_day):
        search = run_google_search(
            session,
            workspace,
            client,
            query,
            area,
            20,
            daily_limit,
            country,
            config,
            monthly_limit=monthly_limit,
        )
        session.commit()
        result.ran = True
        result.searches.append(f"{query} en {area.split(',')[0]}")
        result.created += search.created
        result.existing += search.existing
        if search.requests:
            done[_key(query, area)] = today.isoformat()
        if search.stopped_reason:
            result.stopped_reason = search.stopped_reason
            break

    state["last_run"] = today.isoformat()
    state["last_result"] = {
        "date": today.isoformat(),
        "searches": result.searches,
        "created": result.created,
        "existing": result.existing,
        "stopped_reason": result.stopped_reason,
    }
    save_state(state_path, state)
    return result

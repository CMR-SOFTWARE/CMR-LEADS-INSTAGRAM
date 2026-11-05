"""Borradores por lote fuera de la app: exportar pendientes -> redactarlos (p. ej. con Claude Code)
-> importarlos. Así no se depende de la cuota de Gemini.

Formato CSV (UTF-8, separado por comas): id, negocio, rubro, plan, problema, contexto, borrador.
Al importar solo se lee `id` y `borrador`.
"""

import csv
from pathlib import Path

from sqlalchemy.orm import Session

from cmr_leads.db.models import Lead
from cmr_leads.domain.enums import LeadStatus
from cmr_leads.messaging.ai_writer import SERVICES, build_lead_facts
from cmr_leads.messaging.generator import detect_problem
from cmr_leads.sales.repository import SqlSalesRepository

FIELDS = ["id", "negocio", "rubro", "plan", "problema", "contexto", "borrador"]


def export_pending(session: Session, workspace_id: int, path: Path) -> int:
    """Calificados sin contactar y sin borrador propio."""
    repo = SqlSalesRepository(session, workspace_id)
    leads = [
        lead
        for lead in repo.leads_in_stages([LeadStatus.APPROVED])
        if not (lead.raw_data or {}).get("ai_message")
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for lead in leads:
            writer.writerow(
                {
                    "id": lead.id,
                    "negocio": lead.label,
                    "rubro": lead.category or lead.business_type or "",
                    "plan": SERVICES.get(lead.recommended_service or "", ""),
                    "problema": detect_problem(lead),
                    "contexto": build_lead_facts(lead).replace("\n", " | "),
                    "borrador": "",
                }
            )
    return len(leads)


def import_drafts(session: Session, workspace_id: int, path: Path) -> tuple[int, int]:
    """Guarda cada borrador como mensaje del lead (lo muestra la ficha). Devuelve (ok, salteados)."""
    repo = SqlSalesRepository(session, workspace_id)
    ok = skipped = 0
    with path.open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            draft = (row.get("borrador") or "").strip()
            lead: Lead | None = None
            if (row.get("id") or "").strip().isdigit():
                lead = repo.get_lead(int(row["id"]))
            if not draft or lead is None or lead.do_not_contact:
                skipped += 1
                continue
            lead.raw_data = {**(lead.raw_data or {}), "ai_message": draft[:1500]}
            ok += 1
    repo.flush()
    return ok, skipped

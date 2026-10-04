import csv
from collections.abc import Sequence
from pathlib import Path

from cmr_leads.db.models import Lead

COLUMNS = (
    "id",
    "instagram_username",
    "instagram_url",
    "display_name",
    "business_type",
    "category",
    "city",
    "phone_e164",
    "whatsapp_e164",
    "whatsapp_confirmed",
    "email",
    "website",
    "followers",
    "business_score",
    "fit_score",
    "need_score",
    "reach_score",
    "priority",
    "recommended_service",
    "excluded",
    "exclusion_reason",
    "status",
    "do_not_contact",
    "sources",
    "notes",
    "first_seen_at",
    "contacted_at",
)


def lead_row(lead: Lead) -> list:
    row = []
    for column in COLUMNS:
        if column == "sources":
            value = ", ".join(
                f"{s.source}:{s.source_ref}" if s.source_ref else s.source for s in lead.sources
            )
        else:
            value = getattr(lead, column)
        row.append(value)
    return row


def export_csv(leads: Sequence[Lead], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(COLUMNS)
        for lead in leads:
            writer.writerow(lead_row(lead))
    return path


def export_xlsx(leads: Sequence[Lead], path: Path) -> Path:
    from openpyxl import Workbook

    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Leads"
    sheet.append(list(COLUMNS))
    for lead in leads:
        sheet.append(lead_row(lead))
    sheet.freeze_panes = "A2"
    workbook.save(path)
    return path

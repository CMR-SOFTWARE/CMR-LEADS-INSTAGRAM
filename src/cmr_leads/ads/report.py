"""Reporte mensual: arma una foto congelada del mes y la guarda con su PDF."""

from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from cmr_leads.ads.metrics import account_totals, level_rows, pct_change
from cmr_leads.ads.recommendations import build_recommendations
from cmr_leads.ads.sync import month_bounds
from cmr_leads.db.models import AdCampaign, AdReport, AdSet, utcnow

MONTHS = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)
TOP_N = 3
ACTIVE = ("ACTIVE",)


def month_label(value: date) -> str:
    return f"{MONTHS[value.month - 1]} {value.year}"


def parse_month(text: str) -> date:
    year, month = text.split("-")
    return date(int(year), int(month), 1)


def previous_month(today: date) -> date:
    return (today.replace(day=1) - timedelta(days=1)).replace(day=1)


def _ad_dict(row) -> dict:
    t = row.totals
    return {
        "name": row.name,
        "campaign": row.parent,
        "status": row.status,
        "spend": round(t.spend, 2),
        "impressions": t.impressions,
        "link_clicks": t.link_clicks,
        "messages": t.messages,
        "reach": t.reach,
        "cost_per_message": t.cost_per_message,
    }


def rank_ads(ads: list[dict]) -> tuple[list[dict], list[dict]]:
    """Mejores: más baratos por mensaje. Peores: más caros, y primero los que no trajeron nada.

    Con pocos anuncios se reparten mitad y mitad, para que las dos listas tengan algo que
    comparar (con 2 anuncios: 1 mejor y 1 peor).
    """
    spent = [a for a in ads if a["spend"] > 0]
    with_results = sorted(
        (a for a in spent if a["messages"]), key=lambda a: (a["cost_per_message"], -a["messages"])
    )
    best_count = min(TOP_N, max(1, len(spent) // 2)) if len(spent) > 1 else len(with_results)
    best = with_results[:best_count]
    best_names = {id(a) for a in best}
    no_results = sorted((a for a in spent if not a["messages"]), key=lambda a: -a["spend"])
    worst_pool = no_results + list(reversed(with_results))
    worst = [a for a in worst_pool if id(a) not in best_names][:TOP_N]
    return best, worst


def planned_budget(session: Session, workspace_id: int, days: int) -> float:
    """Presupuesto diario de lo que está activo (campaña o, si no tiene, sus conjuntos) × días."""
    total = 0.0
    campaigns = session.scalars(
        select(AdCampaign).where(
            AdCampaign.workspace_id == workspace_id, AdCampaign.effective_status.in_(ACTIVE)
        )
    )
    for campaign in campaigns:
        if campaign.daily_budget:
            total += campaign.daily_budget
            continue
        total += sum(
            s.daily_budget or 0
            for s in session.scalars(
                select(AdSet).where(
                    AdSet.campaign_id == campaign.id, AdSet.effective_status.in_(ACTIVE)
                )
            )
        )
    return total * days


def build_report_data(
    session: Session, workspace_id: int, account_meta_id: str | None, month: date
) -> dict:
    since, until = month_bounds(month)
    prev_since, prev_until = month_bounds(since - timedelta(days=1))
    totals = account_totals(session, workspace_id, since, until, account_meta_id)
    previous = account_totals(session, workspace_id, prev_since, prev_until, account_meta_id)
    ads = [_ad_dict(r) for r in level_rows(session, workspace_id, "anuncios", since, until)]
    campaigns = [_ad_dict(r) for r in level_rows(session, workspace_id, "campanas", since, until)]
    best, worst = rank_ads(ads)
    current, prev = totals.as_dict(), previous.as_dict()
    data = {
        "month": f"{since:%Y-%m}",
        "label": month_label(since),
        "since": since.isoformat(),
        "until": until.isoformat(),
        "totals": current,
        "previous": prev,
        "previous_label": month_label(prev_since),
        "changes": {
            key: pct_change(current[key], prev[key])
            for key in ("spend", "reach", "link_clicks", "messages", "cost_per_message")
        },
        "campaigns": [c for c in campaigns if c["spend"] > 0],
        "ads": ads,
        "best_ads": best,
        "worst_ads": worst,
        "planned_budget": planned_budget(session, workspace_id, (until - since).days + 1),
        "generated_at": utcnow().isoformat(timespec="minutes"),
    }
    data["recommendations"] = build_recommendations(data)
    return data


def save_report(
    session: Session,
    workspace_id: int,
    data: dict,
    reports_dir: Path,
    render_pdf: Callable[[dict, Path], None],
) -> AdReport:
    report = session.scalar(
        select(AdReport).where(
            AdReport.workspace_id == workspace_id, AdReport.month == data["month"]
        )
    )
    path = reports_dir / f"reporte-publicidad-{data['month']}.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    render_pdf(data, path)
    if report is None:
        report = AdReport(workspace_id=workspace_id, month=data["month"], data=data)
        session.add(report)
    report.data = data
    report.pdf_path = str(path)
    report.generated_at = utcnow()
    session.flush()
    return report

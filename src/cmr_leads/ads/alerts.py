"""Alertas de la cuenta publicitaria: sin saldo, anuncio rechazado y costo en alza.

Cada condición tiene una clave fija (`dedupe_key`): mientras siga abierta no se repite, y
cuando la condición desaparece se cierra sola.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from cmr_leads.db.models import Ad, AdAccount, AdAlert, utcnow

# account_status de Meta
DISABLED_STATUSES = {
    2: "deshabilitada",
    7: "en revisión de Meta",
    100: "por cerrarse",
    101: "cerrada",
}
PAYMENT_STATUSES = {
    3: "tiene un pago pendiente",
    8: "tiene un pago pendiente",
    9: "está en período de gracia por falta de pago",
}
SYNC_ERROR_KEY = "error_sincronizacion"
MIN_MESSAGES_TO_COMPARE = 3


@dataclass
class AlertSpec:
    key: str
    kind: str
    severity: str
    title: str
    message: str


def ars(value: float) -> str:
    return "$" + f"{value:,.0f}".replace(",", ".")


def parse_amount(text: str | None) -> float | None:
    """Saca el monto de textos como "Saldo disponible ($12.345,67 ARS)"."""
    if not text:
        return None
    match = re.search(r"(\d[\d.,]*)", text)
    if not match:
        return None
    raw = match.group(1).rstrip(".,")
    decimals = re.search(r"[.,](\d{1,2})$", raw)
    if decimals:
        whole, cents = raw[: decimals.start()], decimals.group(1)
    else:
        whole, cents = raw, "0"
    try:
        return float(re.sub(r"[.,]", "", whole) + "." + cents)
    except ValueError:
        return None


def _account_specs(account: AdAccount, low_balance: float) -> list[AlertSpec]:
    specs = []
    status = account.account_status
    if status in DISABLED_STATUSES:
        specs.append(
            AlertSpec(
                "cuenta_deshabilitada",
                "cuenta_deshabilitada",
                "critica",
                "La cuenta publicitaria no está activa",
                f"Meta marca la cuenta como {DISABLED_STATUSES[status]}. Los anuncios no se "
                "están mostrando. Revisá el Administrador de anuncios.",
            )
        )
    if status in PAYMENT_STATUSES:
        specs.append(
            AlertSpec(
                "sin_saldo:pago",
                "sin_saldo",
                "critica",
                "Problema con el pago de la cuenta",
                f"La cuenta {PAYMENT_STATUSES[status]}. Revisá el método de pago para que los "
                "anuncios no se frenen.",
            )
        )
    left = account.spend_cap_left
    if left is not None and left < low_balance:
        specs.append(
            AlertSpec(
                "sin_saldo:limite",
                "sin_saldo",
                "aviso" if left > 0 else "critica",
                "La cuenta se está quedando sin límite de gasto",
                f"Al límite de gasto le quedan {ars(left)}. Cuando llegue a cero, los anuncios "
                "se frenan. Subí el límite o reinicialo.",
            )
        )
    prepaid = parse_amount(account.funding_display) if account.is_prepay else None
    if prepaid is not None and prepaid < low_balance:
        specs.append(
            AlertSpec(
                "sin_saldo:prepago",
                "sin_saldo",
                "aviso" if prepaid > 0 else "critica",
                "Queda poco saldo cargado",
                f"El saldo disponible es de {ars(prepaid)}. Cargá saldo para que los anuncios "
                "sigan saliendo.",
            )
        )
    return specs


def _feedback_text(feedback: dict | None) -> str:
    if not feedback:
        return ""
    reasons = [str(v) for v in (feedback.get("global") or feedback).values() if v]
    if not reasons:
        return ""
    text = " ".join(reasons)[:300].rstrip(". ")
    return f" Motivo: {text}."


def _ad_specs(session: Session, workspace_id: int) -> list[AlertSpec]:
    specs = []
    ads = session.scalars(
        select(Ad).where(
            Ad.workspace_id == workspace_id, Ad.effective_status.in_(("DISAPPROVED", "WITH_ISSUES"))
        )
    )
    for ad in ads:
        rejected = ad.effective_status == "DISAPPROVED"
        specs.append(
            AlertSpec(
                f"anuncio_rechazado:{ad.meta_id}",
                "anuncio_rechazado",
                "critica" if rejected else "aviso",
                f"Anuncio {'rechazado' if rejected else 'con problemas'}: {ad.name}",
                (
                    "Meta no aprobó este anuncio y no se está mostrando."
                    if rejected
                    else "Meta marcó un problema en este anuncio."
                )
                + _feedback_text(ad.review_feedback)
                + " Corregilo y volvé a enviarlo a revisión.",
            )
        )
    return specs


MIN_RESULTS_TO_COMPARE = {"mensajes": 3, "alcance": 1000}
DEFAULT_MIN_RESULTS = 20


def _window(session: Session, workspace_id: int, since: date, until: date) -> dict[int, tuple]:
    """Por campaña: (objetivo, inversión, resultado) en la ventana.

    Para campañas de alcance se usan las impresiones (el alcance no se puede sumar por días).
    """
    from cmr_leads.ads.metrics import level_rows
    from cmr_leads.ads.objectives import result_of

    rows = level_rows(session, workspace_id, "campanas", since, until)
    window = {}
    for r in rows:
        value = (
            r.totals.impressions if r.objective == "alcance" else result_of(r.objective, r.totals)
        )
        window[r.id] = (r.objective, r.totals.spend, int(value or 0), r.name)
    return window


def _cost_specs(session: Session, workspace_id: int, today: date, pct: float) -> list[AlertSpec]:
    """Costo por resultado (según el objetivo) de los últimos 7 días contra los 7 anteriores."""
    from cmr_leads.ads.objectives import OBJECTIVES

    current = _window(session, workspace_id, today - timedelta(days=6), today)
    previous = _window(session, workspace_id, today - timedelta(days=13), today - timedelta(days=7))
    specs = []
    for campaign_id, (objective, spend, result, name) in current.items():
        _o, prev_spend, prev_result, _n = previous.get(campaign_id, (objective, 0.0, 0, name))
        minimum = MIN_RESULTS_TO_COMPARE.get(objective, DEFAULT_MIN_RESULTS)
        if prev_result < minimum or not spend:
            continue
        goal = OBJECTIVES[objective]
        per = 1000 if objective == "alcance" else 1
        unit = "1.000 impresiones" if objective == "alcance" else goal.unit
        prev_cost = prev_spend / prev_result * per
        key = f"costo_en_alza:{campaign_id}"
        if result == 0:
            if spend >= prev_cost * 2 / per:
                specs.append(
                    AlertSpec(
                        key,
                        "costo_en_alza",
                        "aviso",
                        f"{name}: gastó sin conseguir {goal.result.lower()}",
                        f"En los últimos 7 días invirtió {ars(spend)} y no consiguió "
                        f"{goal.result.lower()}. Antes pagaba {ars(prev_cost)} por {unit}.",
                    )
                )
            continue
        cost = spend / result * per
        rise = (cost - prev_cost) / prev_cost * 100
        if rise > pct:
            specs.append(
                AlertSpec(
                    key,
                    "costo_en_alza",
                    "aviso",
                    f"{name}: el costo por {unit} subió {rise:.0f}%",
                    f"Pasó de {ars(prev_cost)} a {ars(cost)} por {unit} (últimos 7 días contra "
                    "los 7 anteriores). Revisá las creatividades y el público.",
                )
            )
    return specs


def evaluate_alerts(
    session: Session,
    workspace_id: int,
    account: AdAccount,
    today: date,
    cost_alert_pct: float,
    low_balance: float,
) -> list[AdAlert]:
    """Abre las alertas nuevas y cierra las que ya no corresponden. Devuelve las nuevas."""
    specs = (
        _account_specs(account, low_balance)
        + _ad_specs(session, workspace_id)
        + _cost_specs(session, workspace_id, today, cost_alert_pct)
    )
    wanted = {s.key: s for s in specs}
    open_alerts = {
        a.dedupe_key: a
        for a in session.scalars(
            select(AdAlert).where(AdAlert.workspace_id == workspace_id, AdAlert.status == "abierta")
        )
    }
    now = utcnow()
    for key, alert in open_alerts.items():
        if key not in wanted:  # incluye el error de sincronización: esta vez anduvo
            alert.status, alert.resolved_at = "resuelta", now
    created = []
    for key, spec in wanted.items():
        if key in open_alerts:
            open_alerts[key].message = spec.message
            continue
        alert = AdAlert(
            workspace_id=workspace_id,
            kind=spec.kind,
            severity=spec.severity,
            dedupe_key=key,
            title=spec.title,
            message=spec.message,
        )
        session.add(alert)
        created.append(alert)
    session.flush()
    return created


def record_sync_error(session: Session, workspace_id: int, message: str) -> None:
    alert = session.scalar(
        select(AdAlert).where(
            AdAlert.workspace_id == workspace_id,
            AdAlert.dedupe_key == SYNC_ERROR_KEY,
            AdAlert.status == "abierta",
        )
    )
    if alert:
        alert.message = message
    else:
        session.add(
            AdAlert(
                workspace_id=workspace_id,
                kind="error_sincronizacion",
                severity="aviso",
                dedupe_key=SYNC_ERROR_KEY,
                title="No se pudo actualizar la publicidad",
                message=message,
            )
        )
    session.flush()

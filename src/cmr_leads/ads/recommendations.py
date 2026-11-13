"""Recomendaciones para el mes siguiente, con reglas simples y explicables.

Recibe los datos ya calculados del reporte (dicts) para poder testearse sin base de datos.
"""

from statistics import median

from cmr_leads.ads.alerts import ars

MAX_RECOMMENDATIONS = 6
HIGH_FREQUENCY = 3.0
UNSPENT_RATIO = 0.7
EXPENSIVE_RATIO = 1.5
LOW_REACH_RATIO = 0.25
COST_CHANGE = 0.2


def _cpm(item: dict) -> float | None:
    return item["spend"] / item["messages"] if item.get("messages") else None


def build_recommendations(data: dict) -> list[dict]:
    """Devuelve [{"tipo": ..., "texto": ...}] ordenadas por importancia."""
    totals, previous = data["totals"], data.get("previous") or {}
    ads = [a for a in data.get("ads", []) if a["spend"] > 0]
    recs: list[dict] = []

    def add(kind: str, text: str) -> None:
        recs.append({"tipo": kind, "texto": text})

    if not totals["spend"]:
        add(
            "arrancar",
            "No hubo inversión en el mes. Activá al menos una campaña para empezar a medir "
            "resultados.",
        )
        return recs

    avg_cost = totals.get("cost_per_message")
    total_spend = totals["spend"]

    # 1. Anuncios que gastaron y no trajeron mensajes.
    threshold = max((avg_cost or 0) * 1.5, total_spend * 0.05)
    for ad in sorted(ads, key=lambda a: -a["spend"]):
        if not ad["messages"] and ad["spend"] >= threshold:
            add(
                "pausar",
                f'Pausá "{ad["name"]}": invirtió {ars(ad["spend"])} y no trajo mensajes. '
                "Usá ese presupuesto en los anuncios que sí funcionan.",
            )

    # 2. Anuncios caros contra el promedio.
    if avg_cost:
        for ad in ads:
            cost = _cpm(ad)
            if cost and cost > avg_cost * EXPENSIVE_RATIO and ad["spend"] >= total_spend * 0.1:
                add(
                    "cambiar",
                    f'Cambiá la imagen o el texto de "{ad["name"]}": cada mensaje costó '
                    f"{ars(cost)} y el promedio de la cuenta fue {ars(avg_cost)}.",
                )

    # 3. Presupuesto sin gastar.
    planned = data.get("planned_budget") or 0
    if planned and total_spend < planned * UNSPENT_RATIO:
        used = total_spend / planned * 100
        add(
            "presupuesto",
            f"Se usó solo el {used:.0f}% del presupuesto previsto ({ars(total_spend)} de "
            f"{ars(planned)}). Revisá si hay anuncios frenados o públicos muy chicos.",
        )

    # 4. Frecuencia alta: la misma gente ve los anuncios muchas veces.
    frequency = totals.get("frequency")
    if frequency and frequency > HIGH_FREQUENCY:
        add(
            "frecuencia",
            f"Cada persona vio los anuncios {frequency:.1f} veces en promedio. Sumá "
            "creatividades nuevas o ampliá la zona para no cansar al público.",
        )

    # 5. Anuncios con poco alcance.
    reaches = [a["reach"] for a in ads if a.get("reach")]
    if len(reaches) >= 3:
        mid = median(reaches)
        for ad in ads:
            if ad.get("reach") and ad["reach"] < mid * LOW_REACH_RATIO:
                add(
                    "alcance",
                    f'"{ad["name"]}" llegó a pocas personas ({ad["reach"]}). Revisá el '
                    "presupuesto de su conjunto o si el público es demasiado chico.",
                )

    # 6. Costo por mensaje contra el mes anterior.
    prev_cost = previous.get("cost_per_message")
    if avg_cost and prev_cost:
        change = (avg_cost - prev_cost) / prev_cost
        if change > COST_CHANGE:
            add(
                "costo",
                f"El costo por mensaje subió {change * 100:.0f}% contra el mes anterior "
                f"({ars(prev_cost)} a {ars(avg_cost)}). Probá creatividades nuevas antes de "
                "subir el presupuesto.",
            )
        elif change < -COST_CHANGE:
            add(
                "costo",
                f"El costo por mensaje bajó {abs(change) * 100:.0f}% contra el mes anterior. "
                "Mantené los anuncios que funcionan y no cambies todo de golpe.",
            )

    # 7. Escalar el mejor anuncio.
    best = data.get("best_ads") or []
    if best and best[0].get("messages", 0) >= 3:
        add(
            "escalar",
            f'Subile de a 20% el presupuesto al conjunto de "{best[0]["name"]}", que consiguió '
            f"mensajes al menor costo ({ars(_cpm(best[0]))} cada uno).",
        )

    if not recs:
        add(
            "mantener",
            "Los números del mes están estables. Mantené las campañas y renová al menos una "
            "creatividad por campaña.",
        )
    return recs[:MAX_RECOMMENDATIONS]

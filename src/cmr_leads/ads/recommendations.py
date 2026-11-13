"""Recomendaciones para el mes siguiente, con reglas simples y explicables.

Cada anuncio se evalúa con el resultado de su objetivo (para qué se pagó) y el alcance es el
número que se compara entre todos. Recibe los datos ya calculados del reporte (dicts) para
poder testearse sin base de datos.
"""

from statistics import mean, median

from cmr_leads.ads.alerts import ars

MAX_RECOMMENDATIONS = 6
HIGH_FREQUENCY = 3.0
UNSPENT_RATIO = 0.7
EXPENSIVE_RATIO = 1.5
LOW_REACH_RATIO = 0.25
COST_CHANGE = 0.2
NO_RESULT_SHARE = 0.15  # anuncios sin resultado que se llevaron al menos el 15% de la inversión
MIN_REACH_TO_SCALE = 500


def _unit_cost(ad: dict) -> str:
    unit = ad.get("result_unit") or "resultado"
    return f"{ars(ad['cost_per_result'])} por {unit}"


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

    total_spend = totals["spend"]

    # 1. Anuncios que gastaron una parte grande y no trajeron el resultado de su objetivo.
    for ad in sorted(ads, key=lambda a: -a["spend"]):
        if not ad.get("result") and ad["spend"] >= total_spend * NO_RESULT_SHARE:
            label = (ad.get("result_label") or "resultados").lower()
            add(
                "pausar",
                f'Revisá "{ad["name"]}": se pagó para {label} e invirtió {ars(ad["spend"])} '
                f"sin conseguir ninguno. Si no mejora en una semana, pausalo.",
            )

    # 2. Anuncios caros contra otros del mismo objetivo.
    groups: dict[str, list[dict]] = {}
    for ad in ads:
        if ad.get("cost_per_result"):
            groups.setdefault(ad.get("objective") or "alcance", []).append(ad)
    for items in groups.values():
        if len(items) < 2:
            continue
        avg = mean(a["cost_per_result"] for a in items)
        for ad in items:
            if ad["cost_per_result"] > avg * EXPENSIVE_RATIO and ad["spend"] >= total_spend * 0.1:
                add(
                    "cambiar",
                    f'Cambiá la imagen o el texto de "{ad["name"]}": pagó {_unit_cost(ad)} y '
                    f"el promedio de los anuncios con su mismo objetivo fue {ars(avg)}.",
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

    # 6. Costo de llegar a 1.000 personas contra el mes anterior.
    cost = totals.get("cost_per_thousand_reach")
    prev_cost = previous.get("cost_per_thousand_reach")
    if cost and prev_cost:
        change = (cost - prev_cost) / prev_cost
        if change > COST_CHANGE:
            add(
                "costo",
                f"Llegar a 1.000 personas costó {change * 100:.0f}% más que el mes anterior "
                f"({ars(prev_cost)} a {ars(cost)}). Probá creatividades nuevas antes de subir "
                "el presupuesto.",
            )
        elif change < -COST_CHANGE:
            add(
                "costo",
                f"Llegar a 1.000 personas costó {abs(change) * 100:.0f}% menos que el mes "
                "anterior. Mantené los anuncios que funcionan y no cambies todo de golpe.",
            )

    # 7. Escalar el anuncio que llegó a más gente por peso.
    best = data.get("best_ads") or []
    if best and (best[0].get("reach") or 0) >= MIN_REACH_TO_SCALE:
        add(
            "escalar",
            f'Subile de a 20% el presupuesto a "{best[0]["name"]}": llegó a '
            f"{best[0]['reach']} personas a {ars(best[0]['cost_per_thousand_reach'])} cada "
            "1.000, el mejor costo de alcance del mes.",
        )

    if not recs:
        add(
            "mantener",
            "Los números del mes están estables. Mantené las campañas y renová al menos una "
            "creatividad por campaña.",
        )
    return recs[:MAX_RECOMMENDATIONS]

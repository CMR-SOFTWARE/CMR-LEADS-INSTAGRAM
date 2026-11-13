"""Arma la configuración de los gráficos (la dibuja static/charts.js con Chart.js)."""

import json

from markupsafe import Markup


def dataset(label, data, color=None, kind=None, axis="y", fmt=None) -> dict:
    return {
        "label": label,
        "data": list(data),
        "color": color,
        "type": kind,
        "axis": axis,
        "format": fmt,
    }


def chart(chart_id: str, kind: str, labels, datasets, **options) -> Markup:
    """Devuelve el <script type="application/json"> que lee el <canvas data-chart="chart_id">."""
    spec = {"type": kind, "labels": list(labels), "datasets": datasets, **options}
    payload = json.dumps(spec, ensure_ascii=False).replace("</", "<\\u002f")
    return Markup(f'<script type="application/json" id="{chart_id}">{payload}</script>')


def metric(key: str, label: str, data, color: str | None = None, fmt: str | None = None) -> dict:
    """Un dato que se puede elegir en un gráfico comparador."""
    return {
        "key": key,
        "label": label,
        "data": [v if v is None else round(v, 2) for v in data],
        "color": color,
        "format": fmt,
    }


def compare(
    chart_id: str, labels, metrics: list[dict], left: str, right: str | None, over_time: bool = True
) -> Markup:
    """Gráfico de doble eje elegible desde la página.

    En el tiempo el segundo dato va en línea (tendencia); entre categorías (campañas)
    va en barras, porque una línea entre campañas sugiere una continuidad que no existe.
    """
    return chart(
        chart_id,
        "compare",
        labels,
        [],
        metrics=metrics,
        left=left,
        right=right,
        right_kind="line" if over_time else "bar",
    )

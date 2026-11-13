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

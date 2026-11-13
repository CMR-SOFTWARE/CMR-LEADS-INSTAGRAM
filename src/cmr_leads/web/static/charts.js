/* Gráficos de Publicidad e Instagram con Chart.js.
   Cada <canvas data-chart="ID"> lee su configuración de <script type="application/json" id="ID">. */
(function () {
  if (!window.Chart) return;
  const css = getComputedStyle(document.documentElement);
  const v = (name, fallback) => (css.getPropertyValue(name) || "").trim() || fallback;
  const COLORS = {
    navy: v("--cmr-navy", "#0A1F44"),
    azul: v("--cmr-azul", "#2E5EFF"),
    turquesa: v("--cmr-turquesa", "#1FA59C"),
    ig: v("--ig", "#C13584"),
    violeta: v("--nexo-violeta", "#6366F1"),
    alerta: v("--cmr-alerta", "#A15C07"),
    gris: v("--cmr-gris-ui", "#7B86A1"),
    error: v("--cmr-error", "#C42B2B"),
  };
  const SERIES = ["azul", "turquesa", "ig", "violeta", "alerta", "navy", "gris"];
  const pick = (c, i) => COLORS[c] || c || COLORS[SERIES[i % SERIES.length]];
  const nf = new Intl.NumberFormat("es-AR", { maximumFractionDigits: 0 });
  const fmt = (value, kind) => {
    if (value === null || value === undefined) return "s/d";
    if (kind === "money") return "$" + nf.format(value);
    if (kind === "pct") return nf.format(value) + "%";
    return nf.format(value);
  };

  Chart.defaults.font.family = v("--font-cmr", "Poppins, system-ui, sans-serif");
  Chart.defaults.color = v("--cmr-texto-1", "#56688A");
  Chart.defaults.plugins.legend.labels.boxWidth = 12;
  Chart.defaults.maintainAspectRatio = false;

  function build(canvas) {
    const source = document.getElementById(canvas.dataset.chart);
    if (!source) return;
    const spec = JSON.parse(source.textContent);
    const round = spec.type === "doughnut";
    const datasets = spec.datasets.map((d, i) => {
      const color = round ? d.data.map((_, j) => pick(null, j)) : pick(d.color, i);
      return {
        label: d.label,
        data: d.data,
        type: d.type || undefined,
        yAxisID: round ? undefined : d.axis || "y",
        backgroundColor: d.type === "line" ? color : color,
        borderColor: round ? "#fff" : color,
        borderWidth: d.type === "line" ? 2 : round ? 2 : 0,
        borderRadius: d.type === "line" || round ? 0 : 4,
        pointRadius: d.type === "line" ? 3 : undefined,
        tension: 0.3,
        order: d.type === "line" ? 0 : 1,
        _format: d.format,
      };
    });
    const axes = {};
    if (!round) {
      const used = new Set(datasets.map((d) => d.yAxisID));
      const indexAxis = spec.horizontal ? "y" : "x";
      axes[indexAxis] = { grid: { display: false }, stacked: !!spec.stacked, ticks: { autoSkip: true, maxRotation: 0 } };
      used.forEach((id) => {
        const ds = datasets.find((d) => d.yAxisID === id);
        const axisKey = spec.horizontal ? "x" : id;
        axes[axisKey] = {
          position: id === "y1" ? "right" : "left",
          beginAtZero: true,
          stacked: !!spec.stacked,
          grid: { display: id !== "y1", color: "rgba(10,31,68,.06)" },
          ticks: { callback: (value) => fmt(value, ds._format) },
        };
      });
    }
    new Chart(canvas, {
      type: spec.type || "bar",
      data: { labels: spec.labels, datasets },
      options: {
        indexAxis: spec.horizontal ? "y" : "x",
        interaction: { mode: round ? "nearest" : "index", intersect: round },
        scales: axes,
        cutout: round ? "62%" : undefined,
        plugins: {
          legend: { display: round || datasets.length > 1, position: round ? "right" : "top" },
          tooltip: {
            callbacks: {
              label: (ctx) => {
                const ds = ctx.dataset;
                const raw = spec.horizontal ? ctx.parsed.x : round ? ctx.parsed : ctx.parsed.y;
                return " " + (round ? ctx.label : ds.label) + ": " + fmt(raw, ds._format);
              },
            },
          },
        },
      },
    });
  }

  document.querySelectorAll("canvas[data-chart]").forEach(build);
})();

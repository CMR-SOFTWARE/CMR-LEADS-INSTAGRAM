/* Gráficos de Publicidad e Instagram con Chart.js.
   Cada <canvas data-chart="ID"> lee su configuración de <script type="application/json" id="ID">.

   Criterios para que se lean fácil:
   - un dato por gráfico (dos solo si comparten escala),
   - el valor escrito sobre cada barra,
   - un color base y el mejor (o el peor) resaltado,
   - rankings en barras horizontales. */
(function () {
  if (!window.Chart) return;
  const css = getComputedStyle(document.documentElement);
  const v = (name, fallback) => (css.getPropertyValue(name) || "").trim() || fallback;
  const COLORS = {
    navy: v("--cmr-navy", "#0A1F44"),
    azul: v("--cmr-azul", "#2E5EFF"),
    suave: "#A9BEF7", // barras normales cuando hay una resaltada
    turquesa: v("--cmr-turquesa", "#1FA59C"),
    ig: v("--ig", "#C13584"),
    violeta: v("--nexo-violeta", "#6366F1"),
    alerta: v("--cmr-alerta", "#A15C07"),
    gris: v("--cmr-gris-ui", "#7B86A1"),
    texto: v("--cmr-texto-2", "#3B4D6E"),
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
  const short = (value) => {
    const abs = Math.abs(value);
    if (abs >= 1e6) return (value / 1e6).toFixed(1).replace(".", ",") + " M";
    if (abs >= 1e4) return Math.round(value / 1e3) + " mil";
    return nf.format(value);
  };

  // Escribe el valor sobre cada barra (como las etiquetas de datos de Power BI).
  const valueLabels = {
    id: "cmrValueLabels",
    afterDatasetsDraw(chart, _args, opts) {
      if (!opts || !opts.enabled) return;
      const { ctx } = chart;
      const horizontal = chart.options.indexAxis === "y";
      ctx.save();
      ctx.font = "600 11px " + Chart.defaults.font.family;
      ctx.fillStyle = COLORS.texto;
      chart.data.datasets.forEach((ds, i) => {
        if (ds.type === "line") return;
        const meta = chart.getDatasetMeta(i);
        if (meta.hidden) return;
        if (meta.data.length > 16 && !horizontal) return; // muchas barras: se tapan
        meta.data.forEach((bar, j) => {
          const raw = ds.data[j];
          if (raw === null || raw === undefined || raw === 0) return;
          const text = ds._format === "money" ? "$" + short(raw) : short(raw);
          if (horizontal) {
            ctx.textAlign = "left";
            ctx.textBaseline = "middle";
            ctx.fillText(text, bar.x + 6, bar.y);
          } else {
            ctx.textAlign = "center";
            ctx.textBaseline = "bottom";
            ctx.fillText(text, bar.x, bar.y - 4);
          }
        });
      });
      ctx.restore();
    },
  };
  Chart.register(valueLabels);

  Chart.defaults.font.family = v("--font-cmr", "Poppins, system-ui, sans-serif");
  Chart.defaults.color = v("--cmr-texto-1", "#56688A");
  Chart.defaults.plugins.legend.labels.boxWidth = 12;
  Chart.defaults.maintainAspectRatio = false;

  // Colores por barra: todas suaves y la mejor (o la peor) en el color fuerte.
  function barColors(data, highlight, base) {
    const values = data.filter((x) => x !== null && x !== undefined && x !== 0);
    if (!highlight || values.length < 2) return pick(base, 0);
    const target = highlight === "min" ? Math.min(...values) : Math.max(...values);
    return data.map((x) => (x === target ? pick(base, 0) : COLORS.suave));
  }

  function build(canvas) {
    const source = document.getElementById(canvas.dataset.chart);
    if (!source) return;
    const spec = JSON.parse(source.textContent);
    const round = spec.type === "doughnut";
    const horizontal = !!spec.horizontal;
    const datasets = spec.datasets.map((d, i) => {
      const line = d.type === "line" || spec.type === "line";
      let color = pick(d.color, i);
      if (round) color = d.data.map((_, j) => pick(null, j));
      else if (!line && spec.highlight && spec.datasets.length === 1) {
        color = barColors(d.data, spec.highlight, d.color || "azul");
      }
      return {
        label: d.label,
        data: d.data,
        type: d.type || undefined,
        yAxisID: round || horizontal ? undefined : d.axis || "y",
        backgroundColor: line ? pick(d.color, i) : color,
        borderColor: round ? "#fff" : line ? pick(d.color, i) : color,
        borderWidth: line ? 2.5 : round ? 2 : 0,
        borderRadius: line || round ? 0 : 6,
        maxBarThickness: 48,
        pointRadius: line ? 3 : undefined,
        pointHoverRadius: line ? 5 : undefined,
        tension: 0.3,
        fill: false,
        order: line ? 0 : 1,
        _format: d.format,
      };
    });
    const axes = {};
    if (!round) {
      const indexAxis = horizontal ? "y" : "x";
      const valueAxis = horizontal ? "x" : "y";
      axes[indexAxis] = {
        grid: { display: false },
        ticks: {
          autoSkip: !horizontal,
          maxRotation: 0,
          // Rankings: el nombre completo se ve en el tooltip, acá se corta.
          callback(value) {
            const label = this.getLabelForValue(value);
            return horizontal && label.length > 28 ? label.slice(0, 27) + "…" : label;
          },
        },
      };
      const used = new Set(datasets.map((d) => d.yAxisID || valueAxis));
      used.forEach((id) => {
        const ds = datasets.find((d) => (d.yAxisID || valueAxis) === id);
        axes[horizontal ? "x" : id] = {
          position: id === "y1" ? "right" : horizontal ? "bottom" : "left",
          beginAtZero: true,
          grace: "12%", // lugar para el número arriba de la barra
          grid: { display: id !== "y1", color: "rgba(10,31,68,.06)" },
          ticks: { callback: (value) => (ds._format === "money" ? "$" : "") + short(value) },
        };
      });
    }
    new Chart(canvas, {
      type: spec.type || "bar",
      data: { labels: spec.labels, datasets },
      options: {
        indexAxis: horizontal ? "y" : "x",
        interaction: { mode: round ? "nearest" : "index", intersect: round },
        scales: axes,
        cutout: round ? "62%" : undefined,
        layout: { padding: { right: horizontal ? 48 : 8, top: 4 } },
        plugins: {
          cmrValueLabels: { enabled: spec.labels_on !== false && !round },
          legend: { display: round || datasets.length > 1, position: round ? "right" : "top" },
          tooltip: {
            callbacks: {
              label: (ctx) => {
                const ds = ctx.dataset;
                const raw = horizontal ? ctx.parsed.x : round ? ctx.parsed : ctx.parsed.y;
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

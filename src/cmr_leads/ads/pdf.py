"""PDF del reporte mensual con la identidad de CMR (navy, gris claro y azul de acento)."""

from pathlib import Path

from fpdf import FPDF

from cmr_leads.ads.alerts import ars

NAVY = (10, 31, 68)
GRAY = (232, 236, 242)
ACCENT = (46, 94, 255)
TEXT = (59, 77, 110)
MUTED = (86, 104, 138)
GREEN = (15, 110, 98)
RED = (196, 43, 43)
LOGO = Path(__file__).resolve().parents[1] / "web" / "static" / "img" / "cmr-cubo-blanco.png"


def clean(text) -> str:
    """Las fuentes base del PDF solo aceptan latin-1: se quitan emojis y símbolos raros."""
    return str(text if text is not None else "").encode("latin-1", "ignore").decode("latin-1")


def fmt_int(value) -> str:
    return "s/d" if value is None else f"{int(value):,}".replace(",", ".")


def fmt_money(value) -> str:
    return "s/d" if value is None else ars(value)


def fmt_change(value, lower_is_better: bool = False) -> tuple[str, tuple]:
    if value is None:
        return "sin dato del mes anterior", MUTED
    good = value < 0 if lower_is_better else value > 0
    color = GREEN if good else (RED if value else MUTED)
    return f"{value * 100:+.0f}% vs. mes anterior", color


class ReportPDF(FPDF):
    company = "CMR Software Solutions"

    def footer(self) -> None:
        self.set_y(-14)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 6, clean(f"{self.company} · cmrsoftwaresolutions.com"), align="L")
        self.set_x(self.l_margin)
        self.cell(0, 6, f"{self.page_no()}", align="R")


def _section(pdf: FPDF, title: str) -> None:
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(*NAVY)
    pdf.cell(0, 8, clean(title.upper()), new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(*ACCENT)
    pdf.set_line_width(0.6)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + 18, pdf.get_y())
    pdf.ln(3)


def _kpi(pdf: FPDF, x: float, y: float, w: float, label: str, value: str, change) -> None:
    pdf.set_fill_color(*GRAY)
    pdf.rect(x, y, w, 26, style="F")
    pdf.set_xy(x + 4, y + 3)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*MUTED)
    pdf.cell(w - 8, 4, clean(label.upper()))
    pdf.set_xy(x + 4, y + 9)
    pdf.set_font("Helvetica", "B", 15)
    pdf.set_text_color(*NAVY)
    pdf.cell(w - 8, 8, clean(value))
    text, color = change
    pdf.set_xy(x + 4, y + 18)
    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(*color)
    pdf.cell(w - 8, 4, clean(text))


def _table(pdf: FPDF, headers: list[str], rows: list[list[str]], widths: list[float]) -> None:
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.set_fill_color(*NAVY)
    pdf.set_text_color(255, 255, 255)
    for header, w in zip(headers, widths, strict=True):
        pdf.cell(w, 7, clean(header), fill=True, align="L" if header == headers[0] else "R")
    pdf.ln()
    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(*TEXT)
    for i, row in enumerate(rows):
        pdf.set_fill_color(*(GRAY if i % 2 else (255, 255, 255)))
        for j, (value, w) in enumerate(zip(row, widths, strict=True)):
            text = clean(value)
            if j == 0:
                while text and pdf.get_string_width(text) > w - 2:
                    text = text[:-2]
            pdf.cell(w, 7, text, fill=True, align="L" if j == 0 else "R")
        pdf.ln()


def _ads_table(pdf: FPDF, ads: list[dict]) -> None:
    if not ads:
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*MUTED)
        pdf.cell(0, 6, "No hay más anuncios para comparar este mes.", new_x="LMARGIN", new_y="NEXT")
        return
    _table(
        pdf,
        ["Anuncio", "Inversión", "Mensajes", "Costo por mensaje"],
        [
            [
                a["name"],
                fmt_money(a["spend"]),
                fmt_int(a["messages"]),
                fmt_money(a["cost_per_message"]) if a["messages"] else "sin mensajes",
            ]
            for a in ads
        ],
        [86, 32, 26, 38],
    )


def render_report_pdf(data: dict, path: Path, company: str = "CMR Software Solutions") -> None:
    pdf = ReportPDF(format="A4")
    pdf.company = company
    pdf.set_margins(14, 14, 14)
    pdf.set_auto_page_break(True, margin=18)
    pdf.add_page()

    # Encabezado navy con logo
    pdf.set_fill_color(*NAVY)
    pdf.rect(0, 0, pdf.w, 38, style="F")
    if LOGO.exists():
        pdf.image(str(LOGO), x=14, y=9, h=20)
    pdf.set_xy(40, 10)
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 9, "REPORTE DE PUBLICIDAD", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(40)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(201, 211, 230)
    pdf.cell(0, 7, clean(f"{company} · {data['label'].capitalize()} · Instagram y Facebook"))
    pdf.set_y(46)

    totals, changes = data["totals"], data["changes"]
    _section(pdf, "Resumen del mes")
    width = (pdf.w - pdf.l_margin - pdf.r_margin - 8) / 3
    y = pdf.get_y()
    kpis = [
        ("Inversión", fmt_money(totals["spend"]), fmt_change(changes["spend"])),
        ("Alcance (personas)", fmt_int(totals["reach"]), fmt_change(changes["reach"])),
        ("Clics al enlace", fmt_int(totals["link_clicks"]), fmt_change(changes["link_clicks"])),
        ("Mensajes recibidos", fmt_int(totals["messages"]), fmt_change(changes["messages"])),
        (
            "Costo por mensaje",
            fmt_money(totals["cost_per_message"]),
            fmt_change(changes["cost_per_message"], lower_is_better=True),
        ),
        (
            "Impresiones",
            fmt_int(totals["impressions"]),
            (
                f"Frecuencia {totals['frequency']:.1f}".replace(".", ",")
                if totals.get("frequency")
                else "",
                MUTED,
            ),
        ),
    ]
    for i, (label, value, change) in enumerate(kpis):
        col, row = i % 3, i // 3
        _kpi(pdf, pdf.l_margin + col * (width + 4), y + row * 30, width, label, value, change)
    pdf.set_y(y + 60)

    _section(pdf, "Campañas")
    if data["campaigns"]:
        _table(
            pdf,
            ["Campaña", "Inversión", "Alcance", "Mensajes", "Costo por mensaje"],
            [
                [
                    c["name"],
                    fmt_money(c["spend"]),
                    fmt_int(c["reach"]),
                    fmt_int(c["messages"]),
                    fmt_money(c["cost_per_message"]) if c["messages"] else "sin mensajes",
                ]
                for c in data["campaigns"]
            ],
            [70, 30, 26, 22, 34],
        )
    else:
        _ads_table(pdf, [])

    _section(pdf, "Los 3 mejores anuncios")
    _ads_table(pdf, data["best_ads"])
    _section(pdf, "Los 3 que menos rindieron")
    _ads_table(pdf, data["worst_ads"])

    _section(pdf, "Recomendaciones para el mes que viene")
    pdf.set_font("Helvetica", "", 9.5)
    pdf.set_text_color(*TEXT)
    for i, rec in enumerate(data["recommendations"], 1):
        pdf.set_font("Helvetica", "B", 9.5)
        pdf.set_text_color(*ACCENT)
        pdf.cell(7, 6, f"{i}.")
        pdf.set_font("Helvetica", "", 9.5)
        pdf.set_text_color(*TEXT)
        pdf.multi_cell(0, 6, clean(rec["texto"]), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)

    pdf.ln(4)
    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(*MUTED)
    pdf.multi_cell(
        0,
        4,
        clean(
            "Datos de Meta (Instagram y Facebook). Los mensajes son conversaciones iniciadas "
            "desde los anuncios. El alcance cuenta personas únicas del mes completo."
        ),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(path))

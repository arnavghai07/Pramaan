"""
reports/pdf.py — PRAMAAN
=========================
ReportData laid out as a PDF with ReportLab.

WHY PLATYPUS AND NOT canvas.drawString()
-----------------------------------------
ReportLab has two APIs. The canvas draws at coordinates you supply, which
means YOU are responsible for noticing that a finding is 300 characters
long and now overlaps the section below it, and for deciding where page 2
starts. Platypus (SimpleDocTemplate + a list of flowables) takes the
opposite approach: each element reports the height it needs and the frame
breaks pages around them.

For this document that is not a convenience, it is correctness. A findings
list is unbounded — a wrapped violation that silently overprints the next
line is a compliance report that hides a violation, which is CLAUDE.md
rule 2 in a different costume. Every cell of free text below is therefore
a Paragraph, which wraps, and every table is allowed to split across pages
with its header repeated.

This module renders; it never reads the database and never decides
anything. See reports/builder.py.
"""
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)
from reportlab.platypus import Image as RLImage

from reports.builder import NOT_CAPTURED, ReportData
from reports.images import load_evidence

# Enforcement documents are read under time pressure; the verdict has to be
# findable without reading. One accent colour per stored status, and a
# neutral for anything a future build might add.
_STATUS_COLOUR = {
    "COMPLIANT": colors.HexColor("#15803d"),
    "NON_COMPLIANT": colors.HexColor("#b91c1c"),
    "NEEDS_MANUAL_REVIEW": colors.HexColor("#b45309"),
}
_NEUTRAL = colors.HexColor("#334155")

_STATE_COLOUR = {
    "PRESENT": colors.HexColor("#15803d"),
    "MISSING": colors.HexColor("#b91c1c"),
    "REVIEW": colors.HexColor("#b45309"),
}

_INK = colors.HexColor("#0f172a")
_MUTED = colors.HexColor("#475569")
_RULE = colors.HexColor("#cbd5e1")
_HEADER_BG = colors.HexColor("#f1f5f9")

_PAGE_MARGIN = 18 * mm
#: Usable text width — every table and image is sized against this rather
#: than a hard-coded number, so changing the margin cannot push content off
#: the page.
_CONTENT_WIDTH = A4[0] - 2 * _PAGE_MARGIN


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    body = ParagraphStyle("Body", parent=base["BodyText"], fontSize=9.5,
                          leading=13, textColor=_INK, spaceAfter=0)
    return {
        "title": ParagraphStyle("Title", parent=base["Title"], fontSize=20,
                                leading=24, textColor=_INK, spaceAfter=2),
        "subtitle": ParagraphStyle("Subtitle", parent=body, fontSize=9.5,
                                   textColor=_MUTED, alignment=TA_CENTER),
        "h2": ParagraphStyle("H2", parent=base["Heading2"], fontSize=12.5,
                             leading=16, textColor=_INK, spaceBefore=14,
                             spaceAfter=6),
        "body": body,
        "muted": ParagraphStyle("Muted", parent=body, textColor=_MUTED),
        "cell": ParagraphStyle("Cell", parent=body, fontSize=9, leading=11.5),
        "cellhead": ParagraphStyle("CellHead", parent=body, fontSize=9,
                                   leading=11.5, fontName="Helvetica-Bold"),
        "caption": ParagraphStyle("Caption", parent=body, fontSize=8.5,
                                  textColor=_MUTED, spaceBefore=4),
    }


def _page_furniture(canvas, doc, data: ReportData) -> None:
    """
    The rule and footer drawn on every page. Page numbers matter here: a
    printed inspection report that has lost a page should be obviously
    incomplete, and "Page 2 of 4" is what makes that visible.
    """
    canvas.saveState()
    canvas.setStrokeColor(_RULE)
    canvas.setLineWidth(0.5)
    footer_y = _PAGE_MARGIN - 6 * mm
    canvas.line(_PAGE_MARGIN, footer_y + 4 * mm,
                A4[0] - _PAGE_MARGIN, footer_y + 4 * mm)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(_MUTED)
    canvas.drawString(_PAGE_MARGIN, footer_y,
                      f"PRAMAAN compliance report - inspection #{data.inspection_id}")
    canvas.drawRightString(A4[0] - _PAGE_MARGIN, footer_y,
                           f"Page {canvas.getPageNumber()}")
    canvas.restoreState()


def _kv_table(styles, pairs: list[tuple[str, str]]) -> Table:
    """A two-column label/value block. Values wrap; labels are fixed width."""
    rows = [[Paragraph(label, styles["cellhead"]), Paragraph(value, styles["cell"])]
            for label, value in pairs]
    table = Table(rows, colWidths=[55 * mm, _CONTENT_WIDTH - 55 * mm],
                  repeatRows=0, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("LINEBELOW", (0, 0), (-1, -2), 0.25, _RULE),
    ]))
    return table


def _verdict_banner(styles, data: ReportData) -> Table:
    accent = _STATUS_COLOUR.get(data.overall_status, _NEUTRAL)
    heading = ParagraphStyle("Verdict", parent=styles["body"], fontSize=15,
                             leading=19, fontName="Helvetica-Bold",
                             textColor=accent)
    cell = [Paragraph(f"OVERALL: {data.status_label}", heading)]
    if data.status_note:
        cell.append(Paragraph(data.status_note, styles["muted"]))

    table = Table([[cell]], colWidths=[_CONTENT_WIDTH])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), _HEADER_BG),
        ("BOX", (0, 0), (-1, -1), 0.75, accent),
        # A colour bar on the left edge, so the verdict is legible even in a
        # black-and-white photocopy of the report where the tint is lost.
        ("LINEBEFORE", (0, 0), (0, -1), 4, accent),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))
    return table


def _rule6_table(styles, data: ReportData) -> Table:
    header = ["Declaration", "State", "Extracted value", "Requirement"]
    rows = [[Paragraph(h, styles["cellhead"]) for h in header]]
    for row in data.rule6_rows:
        state_style = ParagraphStyle(
            f"state{row.state}", parent=styles["cell"],
            fontName="Helvetica-Bold",
            textColor=_STATE_COLOUR.get(row.state, _INK))
        rows.append([
            Paragraph(row.field, styles["cell"]),
            Paragraph(row.state, state_style),
            Paragraph(row.value, styles["cell"]),
            Paragraph(row.requirement, styles["cell"]),
        ])

    widths = [44 * mm, 20 * mm, _CONTENT_WIDTH - 44 * mm - 20 * mm - 24 * mm, 24 * mm]
    # repeatRows=1 reprints the header on every page the table spills onto —
    # without it a second page of declarations is four unlabelled columns.
    table = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.25, _RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return table


def _bullets(styles, items: list[str]) -> list:
    return [Paragraph(f"&bull;&nbsp;&nbsp;{_escape(item)}", styles["body"])
            for item in items]


def _escape(text: str) -> str:
    """
    Paragraph text is parsed as mini-HTML, so a stored value containing
    "<" or "&" would either vanish or raise. Extracted label text is
    arbitrary — it comes off a photograph — so it is always escaped.
    """
    return (str(text).replace("&", "&amp;")
                     .replace("<", "&lt;")
                     .replace(">", "&gt;"))


def render_pdf(data: ReportData) -> bytes:
    """The finished PDF for one stored inspection, as bytes."""
    styles = _styles()
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=_PAGE_MARGIN, rightMargin=_PAGE_MARGIN,
        topMargin=_PAGE_MARGIN, bottomMargin=_PAGE_MARGIN + 6 * mm,
        title=f"PRAMAAN compliance report - inspection {data.inspection_id}",
        author="PRAMAAN", subject="Legal Metrology (Packaged Commodities) inspection",
    )

    story: list = [
        Paragraph("PRAMAAN", styles["title"]),
        Paragraph("Legal Metrology (Packaged Commodities) "
                  "compliance inspection report", styles["subtitle"]),
        Spacer(1, 10),
        _verdict_banner(styles, data),
        Spacer(1, 4),

        Paragraph("Inspection record", styles["h2"]),
        _kv_table(styles, [
            ("Inspection ID", f"#{data.inspection_id}"),
            ("Inspection date and time", data.inspected_at),
            ("Inspecting officer", _escape(data.inspector_name)),
            ("Product", _escape(data.product_name)),
            ("Manufacturer", _escape(data.manufacturer)),
            ("Maximum retail price", _escape(data.mrp)),
            ("Net quantity", _escape(data.net_quantity)),
            ("Manufacture / packing date", _escape(data.mfg_date)),
        ]),

        Paragraph("Rule 6 &mdash; mandatory declarations", styles["h2"]),
        Paragraph(_escape(data.mandatory_summary), styles["body"]),
        Spacer(1, 6),
        _rule6_table(styles, data),
        Spacer(1, 8),
    ]

    if data.missing_mandatory:
        story.append(Paragraph("Missing or unresolved mandatory declarations",
                               styles["cellhead"]))
        story.extend(_bullets(styles, data.missing_mandatory))
    else:
        story.append(Paragraph("No mandatory declaration was found missing.",
                               styles["body"]))

    if data.rule6_problems:
        story.append(Spacer(1, 6))
        story.append(Paragraph("Cross-check problems", styles["cellhead"]))
        story.extend(_bullets(styles, data.rule6_problems))

    story.append(Paragraph("Rule 7 &mdash; physical character height", styles["h2"]))
    story.append(_kv_table(styles, [(label, _escape(value))
                                    for label, value in data.rule7_rows]))
    if data.rule7_note:
        story.append(Spacer(1, 5))
        story.append(Paragraph(_escape(data.rule7_note), styles["muted"]))

    story.append(Paragraph("Findings", styles["h2"]))
    if data.findings:
        story.extend(_bullets(styles, data.findings))
    else:
        # Never a blank section: an empty findings list under a heading
        # reads as "not checked" rather than "nothing found".
        story.append(Paragraph("No findings were recorded for this inspection.",
                               styles["body"]))

    # Evidence starts on its own page. The photographs are large, so letting
    # them flow would routinely leave one heading stranded at a page foot.
    story.append(PageBreak())
    story.append(Paragraph("Evidence", styles["h2"]))
    for item in data.evidence:
        image = load_evidence(item.path)
        block: list = [Paragraph(_escape(item.caption), styles["cellhead"])]
        if image is None:
            block.append(Paragraph(NOT_CAPTURED, styles["muted"]))
        else:
            width, height = image.size_for(_CONTENT_WIDTH, 195 * mm)
            block.append(Spacer(1, 4))
            block.append(RLImage(image.stream(), width=width, height=height))
        block.append(Spacer(1, 12))
        # KeepTogether stops a caption being separated from its photograph
        # across a page break — a picture with no caption is not evidence.
        story.append(KeepTogether(block))

    story.append(Spacer(1, 6))
    story.append(Paragraph(
        f"This document was generated by PRAMAAN on {data.generated_at}. It "
        f"reproduces inspection #{data.inspection_id} exactly as recorded on "
        f"{data.inspected_at}; no check was re-run and no verdict was "
        "recalculated to produce it.", styles["caption"]))

    furniture = lambda canvas, doc_: _page_furniture(canvas, doc_, data)  # noqa: E731
    doc.build(story, onFirstPage=furniture, onLaterPages=furniture)
    return buffer.getvalue()

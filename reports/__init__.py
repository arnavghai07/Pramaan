"""
reports/ — PRAMAAN compliance report export
============================================
Turns ONE STORED inspection record into a downloadable document.

WHAT THIS PACKAGE MAY AND MAY NOT DO
-------------------------------------
It is a renderer, not a second engine. It never calls the VLM, never
re-measures a photograph, and never decides or adjusts a verdict: every
value it prints was decided by engine/verdict.py and written by
storage/repository.py at inspection time. A record that was stored as
NEEDS_MANUAL_REVIEW prints as NEEDS_MANUAL_REVIEW forever, whatever the
thresholds say today — that is what makes the document evidence rather
than a fresh opinion.

The split inside the package follows from that:

    builder.py  reads the ORM row and produces ReportData — one plain,
                already-formatted view model. ALL the decisions about what
                appears in a report, and how a missing value is worded,
                live here and nowhere else.
    images.py   turns an evidence file on disk into a right-sized JPEG in
                memory, shared by both renderers.
    pdf.py      lays ReportData out with ReportLab.
    docx.py     lays the SAME ReportData out with python-docx.

Neither renderer touches the database or the engine, so the two documents
cannot drift apart in content: a difference between the PDF and the DOCX
can only ever be a difference of layout.
"""
from reports.builder import ReportData, build_report_data, report_filename
from reports.docx import render_docx
from reports.pdf import render_pdf

__all__ = ["ReportData", "build_report_data", "report_filename",
           "render_pdf", "render_docx"]

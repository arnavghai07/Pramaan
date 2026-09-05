"""
reports/builder.py — PRAMAAN
=============================
The one place that turns a stored Inspection row into the view model both
report renderers print.

WHY A VIEW MODEL AND NOT "JUST PASS THE ORM ROW"
-------------------------------------------------
A PDF and a DOCX have nothing in common at the layout level, but they must
say exactly the same things — the same wording for a missing declaration,
the same threshold to the same decimal, the same "Not captured for this
inspection". Handing both renderers the ORM row would mean writing those
decisions twice and letting them drift; the first divergence would be two
documents about one inspection that disagree, which is worse than either
being wrong on its own.

So every judgement about presentation is made once, here, and both
renderers receive strings they only have to place on a page.

NOTHING IN THIS FILE DECIDES ANYTHING (CLAUDE.md rule 1)
---------------------------------------------------------
overall_status, the Rule 7 verdict and the findings list are copied out of
the record verbatim. There is no branch below that could turn a stored
REVIEW into a PASS, and no threshold arithmetic is repeated — the measured
height and the threshold are printed as stored, never re-compared.

And nothing is silently omitted (CLAUDE.md rule 2). A check that did not
run prints the reason it did not run; an absent value prints "Not
recorded"; an inspection with no violations prints a sentence saying so.
A blank space in an enforcement document reads as a pass.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from storage import repository
from storage.models import Inspection

#: Wording used wherever a value the record does not carry would otherwise
#: leave a gap. Defined once so the PDF and the DOCX cannot word it
#: differently.
NOT_RECORDED = "Not recorded"
NOT_CAPTURED = "Not captured for this inspection"

#: How the three stored verdicts are spelled out for a reader who is not
#: reading JSON. The key is the exact string in the database.
STATUS_LABEL = {
    "COMPLIANT": "COMPLIANT",
    "NON_COMPLIANT": "NON-COMPLIANT",
    "NEEDS_MANUAL_REVIEW": "NEEDS MANUAL REVIEW",
}

STATUS_NOTE = {
    "COMPLIANT": "All mandatory declarations were found and every check that "
                 "could run, passed.",
    "NON_COMPLIANT": "At least one requirement of the Legal Metrology "
                     "(Packaged Commodities) Rules was not met.",
    "NEEDS_MANUAL_REVIEW": "One or more checks could not be decided "
                           "automatically and require an officer's judgement.",
}

#: The evidence images a report may carry, in the order they are shown, with
#: the caption each gets. Keys are repository.EVIDENCE_KINDS keys.
EVIDENCE_CAPTIONS = [
    ("rule6", "Rule 6 — declaration panel photograph"),
    ("overlay", "Rule 7 — measurement evidence overlay"),
    ("rule7", "Rule 7 — calibrated measurement photograph"),
]


def _label(field_name: str) -> str:
    """`net_quantity_value` -> `Net quantity value`, matching the console."""
    return field_name.replace("_", " ").strip().capitalize()


def _text(value: Any) -> Optional[str]:
    """A stored value as a printable string, or None when there isn't one."""
    if value is None:
        return None
    s = str(value).strip()
    return s or None


@dataclass
class Rule6Row:
    field: str          # already humanised
    state: str          # PRESENT | MISSING | REVIEW, exactly as stored
    value: str          # the extracted text, or NOT_RECORDED
    requirement: str    # "Mandatory" | "Optional"


@dataclass
class EvidenceItem:
    caption: str
    path: Optional[Path]     # None when this inspection has no such image


@dataclass
class ReportData:
    """Everything a PRAMAAN compliance report prints, already formatted."""

    inspection_id: int
    generated_at: str            # when this DOCUMENT was made
    inspected_at: str            # when the INSPECTION was recorded
    inspector_name: str
    product_name: str
    manufacturer: str
    mrp: str
    net_quantity: str
    mfg_date: str

    overall_status: str          # raw stored string
    status_label: str
    status_note: str

    rule6_rows: list[Rule6Row] = field(default_factory=list)
    mandatory_summary: str = ""
    missing_mandatory: list[str] = field(default_factory=list)
    rule6_problems: list[str] = field(default_factory=list)

    #: Rule 7 is presented as ordered label/value pairs because which pairs
    #: exist depends on how far the measurement got: a photo whose marker
    #: was rejected has a reason but no height, and a photo with no target
    #: selected has rows but no verdict.
    rule7_rows: list[tuple[str, str]] = field(default_factory=list)
    rule7_note: str = ""

    findings: list[str] = field(default_factory=list)
    evidence: list[EvidenceItem] = field(default_factory=list)

    @property
    def base_filename(self) -> str:
        return f"PRAMAAN_Inspection_{self.inspection_id}"


def _format_timestamp(value: datetime) -> str:
    """
    Stored timestamps are naive UTC by convention (storage/models.py). The
    document says UTC explicitly: a compliance report read six months later
    in another office must not leave the reader guessing which clock it was.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%d %B %Y, %H:%M UTC")


def _rule6_section(rule6: dict[str, Any]) -> tuple[list[Rule6Row], list[str], str, list[str]]:
    rows_in = rule6.get("rows") or []
    rows: list[Rule6Row] = []
    missing: list[str] = []

    for row in rows_in:
        name = _label(str(row.get("field", "")))
        state = str(row.get("state", "")).upper() or "UNKNOWN"
        mandatory = bool(row.get("mandatory"))
        rows.append(Rule6Row(
            field=name,
            state=state,
            value=_text(row.get("value")) or NOT_RECORDED,
            requirement="Mandatory" if mandatory else "Optional",
        ))
        if mandatory and state != "PRESENT":
            missing.append(f"{name} ({state})")

    present = rule6.get("mandatory_present", 0)
    total = rule6.get("mandatory_total", 0)
    summary = f"{present} of {total} mandatory declarations present"

    problems = [str(p) for p in (rule6.get("problems") or []) if str(p).strip()]
    return rows, missing, summary, problems


def _rule7_section(rule7: Optional[dict[str, Any]]) -> tuple[list[tuple[str, str]], str]:
    """
    Rule 7 has four distinct outcomes and an enforcement report must be able
    to tell them apart: never attempted, attempted but the calibration photo
    was unusable, measured but no target numeral chosen, and measured with a
    verdict. Collapsing the first three into a blank would read as "Rule 7
    was fine" — the exact failure CLAUDE.md rule 2 exists to prevent.
    """
    if not rule7 or not rule7.get("attempted"):
        return ([("Measurement performed", "No")],
                "No calibrated reference photograph was supplied, so character "
                "height was not measured. This does not affect the Rule 6 "
                "result above.")

    rows: list[tuple[str, str]] = [("Measurement performed", "Yes")]

    problem = _text(rule7.get("problem"))
    if problem:
        rows.append(("Outcome", "Not assessed"))
        return rows, f"The calibration photograph could not be used: {problem}"

    measured = rule7.get("measured_height_mm")
    threshold = rule7.get("threshold_mm")
    verdict = _text(rule7.get("verdict"))
    method = _text(rule7.get("selection_method"))

    rows.append(("Measured character height",
                 f"{float(measured):.2f} mm" if measured is not None else NOT_RECORDED))
    rows.append(("Required minimum height",
                 f"{float(threshold):.1f} mm" if threshold is not None else NOT_RECORDED))
    rows.append(("Rule 7 verdict", verdict or "No verdict issued"))
    rows.append(("Target selection method", method or NOT_RECORDED))

    scale = rule7.get("capture_scale_ppm")
    if scale is not None:
        rows.append(("Capture scale", f"{float(scale):.2f} px/mm"))
    tilt = rule7.get("tilt_spread_pct")
    if tilt is not None:
        rows.append(("Marker aspect deviation", f"{float(tilt):.2f} %"))

    if verdict is None:
        note = ("A calibration photograph was measured, but no target numeral "
                "was selected, so no Rule 7 verdict was issued.")
    elif verdict == "REVIEW":
        note = ("The measurement fell too close to the threshold, or was read "
                "at low confidence, to be decided automatically.")
    else:
        note = ""
    return rows, note


def build_report_data(inspection: Inspection) -> ReportData:
    """
    Read one stored inspection into the printable view model.

    Takes the ORM row the caller already loaded rather than an id, so this
    module needs no session of its own and cannot accidentally widen a
    route's database access.
    """
    rule6 = inspection.rule6_result_json or {}
    # include_overlay=False: the renderers read the overlay from its file on
    # disk, so decoding ~9 MB of base64 here would be pure waste.
    rule7 = repository.rule7_for_response(inspection, include_overlay=False)

    rows, missing, summary, problems = _rule6_section(rule6)
    rule7_rows, rule7_note = _rule7_section(rule7)

    status = inspection.overall_status
    findings = [str(f) for f in (inspection.findings_json or []) if str(f).strip()]

    evidence = [EvidenceItem(caption=caption,
                             path=repository.evidence_path(inspection, kind))
                for kind, caption in EVIDENCE_CAPTIONS]

    return ReportData(
        inspection_id=inspection.id,
        generated_at=_format_timestamp(datetime.now(timezone.utc)),
        inspected_at=_format_timestamp(inspection.created_at),
        inspector_name=_text(inspection.inspector_name) or NOT_RECORDED,
        product_name=_text(inspection.product_name) or NOT_RECORDED,
        manufacturer=_text(inspection.manufacturer) or NOT_RECORDED,
        mrp=(f"Rs. {inspection.mrp:.2f}" if inspection.mrp is not None
             else NOT_RECORDED),
        net_quantity=_text(inspection.net_quantity) or NOT_RECORDED,
        mfg_date=_text(inspection.mfg_date) or NOT_RECORDED,
        overall_status=status,
        status_label=STATUS_LABEL.get(status, status),
        status_note=STATUS_NOTE.get(status, ""),
        rule6_rows=rows,
        mandatory_summary=summary,
        missing_mandatory=missing,
        rule6_problems=problems,
        rule7_rows=rule7_rows,
        rule7_note=rule7_note,
        findings=findings,
        evidence=evidence,
    )


def report_filename(inspection_id: int, extension: str) -> str:
    """`PRAMAAN_Inspection_12.pdf`. One naming rule for both formats."""
    return f"PRAMAAN_Inspection_{inspection_id}.{extension.lstrip('.')}"

"""
storage/repository.py — PRAMAAN
================================
Every read and write of inspection history, in one place. Route handlers in
api/main.py call these functions; they never build a query themselves.

WHAT THIS FILE IS CAREFUL ABOUT
--------------------------------
1. It NEVER decides anything. overall_status, findings and the Rule 7
   verdict arrive already decided by engine/verdict.py and are copied in
   verbatim. There is no branch in this file that could turn a stored
   REVIEW into a stored PASS — CLAUDE.md rule 1 applies to the storage
   layer too, where a "helpful" normalisation would be invisible.

2. It stores each evidence image ONCE. The uploaded file api/main.py writes
   to a temp path is copied here before that temp file is deleted; the
   Rule 7 overlay is decoded out of the response base64 and written as a
   real PNG rather than left as text in a database column. A pack that is
   scanned and then measured is ONE inspection updated in place, not two
   rows with two copies of the same evidence — see save_inspection()'s
   `existing` argument and POST /inspect's inspection_id parameter.

3. A failure to write an evidence file must not lose the inspection. The
   verdict cost a minute or more of VLM inference; an unwritable JPEG is
   reported to the server log, leaves that path null, and the record itself
   still commits.
"""
import base64
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
from sqlalchemy import String, case, cast, func, or_, select
from sqlalchemy.orm import Session

from storage.database import DATA_DIR, EVIDENCE_DIR
from storage.models import Inspection

# The one piece of engine code this layer reuses. _num() is the parser that
# already turns a printed "Rs. 1,299.00" into 1299.0 everywhere else in
# PRAMAAN; re-implementing that here would create a second, subtly different
# idea of what a price is, and the two would drift.
from engine.vlm_extract import _num as parse_number


# ---------------------------------------------------------------------------
# JSON safety
# ---------------------------------------------------------------------------

def _jsonable(value: Any) -> Any:
    """
    Convert an engine result into something json.dumps() accepts.

    Rule 7's rows come out of OpenCV and numpy, so their x/y/w/h and
    height_mm are numpy float32/int64 scalars, not Python floats. Over HTTP
    that never shows: Pydantic coerces them while serialising the response.
    Writing the same dict straight into a JSON column does NOT go through
    Pydantic, and SQLAlchemy raises "Object of type float32 is not JSON
    serializable" — which, caught by the persistence guard in api/main.py,
    would have shown up as an inspection that returned fine and silently
    never saved. Normalising here keeps the stored record byte-identical in
    meaning to the one the API returned.

    Values are converted, never rounded or reinterpreted: .item() gives the
    exact Python equivalent of the numpy scalar.
    """
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return _jsonable(value.tolist())
    return value


# ---------------------------------------------------------------------------
# Evidence files
# ---------------------------------------------------------------------------

def _evidence_dir(inspection_id: int) -> Path:
    d = EVIDENCE_DIR / str(inspection_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _relative(path: Path) -> str:
    """Store paths relative to DATA_DIR so the data directory stays portable."""
    return path.relative_to(DATA_DIR).as_posix()


def resolve(relative_path: Optional[str]) -> Optional[Path]:
    """Absolute path for a stored relative one, or None if never stored."""
    return DATA_DIR / relative_path if relative_path else None


def _copy_evidence(inspection_id: int, src_path: str, stem: str) -> Optional[str]:
    """
    Copy an uploaded image out of its temp location into permanent evidence
    storage. Returns the stored relative path, or None if the copy failed
    (logged, never raised — see this module's docstring, point 3).
    """
    try:
        suffix = Path(src_path).suffix or ".jpg"
        dest = _evidence_dir(inspection_id) / f"{stem}{suffix}"
        shutil.copyfile(src_path, dest)
        return _relative(dest)
    except OSError as e:
        print(f"[storage] could not store {stem} evidence for inspection "
              f"{inspection_id}: {e}", file=sys.stderr)
        return None


def _write_overlay(inspection_id: int, overlay_b64: str) -> Optional[str]:
    """Decode the Rule 7 evidence overlay to a real PNG on disk."""
    try:
        dest = _evidence_dir(inspection_id) / "rule7_overlay.png"
        dest.write_bytes(base64.b64decode(overlay_b64))
        return _relative(dest)
    except (OSError, ValueError) as e:
        print(f"[storage] could not store Rule 7 overlay for inspection "
              f"{inspection_id}: {e}", file=sys.stderr)
        return None


def read_overlay_base64(inspection: Inspection) -> Optional[str]:
    """
    Re-attach the stored overlay to a Rule 7 result on the way out, so a
    replayed inspection carries the same evidence image a live one does.
    """
    path = resolve(inspection.rule7_overlay_path)
    if not path or not path.exists():
        return None
    try:
        return base64.b64encode(path.read_bytes()).decode()
    except OSError as e:
        print(f"[storage] could not read stored overlay {path}: {e}", file=sys.stderr)
        return None


#: The evidence files an inspection can hold, and the column each lives in.
EVIDENCE_KINDS = {
    "rule6": "rule6_image_path",
    "rule7": "rule7_image_path",
    "overlay": "rule7_overlay_path",
}


def evidence_path(inspection: Inspection, kind: str) -> Optional[Path]:
    """
    Absolute path of one stored evidence file, or None if this inspection
    has no such file (or the file has since vanished from disk).

    `kind` is looked up in EVIDENCE_KINDS rather than interpolated into a
    path. That is what makes the HTTP evidence route safe: a caller asking
    for "../../pramaan.db" matches no key and gets None, so no request can
    address a file the record does not name.
    """
    column = EVIDENCE_KINDS.get(kind)
    if column is None:
        return None
    path = resolve(getattr(inspection, column))
    return path if path and path.exists() else None


def available_evidence(inspection: Inspection) -> list[str]:
    """The kinds that actually exist on disk for this inspection."""
    return [kind for kind in EVIDENCE_KINDS if evidence_path(inspection, kind)]


def rule7_for_response(inspection: Inspection,
                       include_overlay: bool = False) -> Optional[dict[str, Any]]:
    """
    The stored Rule 7 result, optionally with its overlay restored inline.

    include_overlay defaults to False because that image is ~9 MB of base64:
    inlining it by default would make every history record slow to open, for
    a picture the page can stream separately from
    GET /inspections/{id}/evidence/overlay. Callers that genuinely want one
    self-contained JSON document — a future PDF renderer, an export — ask
    for it explicitly.
    """
    if inspection.rule7_result_json is None:
        return None
    rule7 = dict(inspection.rule7_result_json)
    rule7["overlay_png_base64"] = (read_overlay_base64(inspection)
                                   if include_overlay else None)
    return rule7


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _summary_columns(rule6: dict[str, Any]) -> dict[str, Any]:
    """
    Lift the searchable/aggregatable values out of the Rule 6 result.

    Only ever reads what extraction already produced. A field the pack does
    not carry stays null here; nothing is derived, defaulted or guessed, so
    a null column means "not on the label", never "we filled in a blank".
    """
    fields = rule6.get("fields") or {}

    def text(key: str) -> Optional[str]:
        v = fields.get(key)
        if v is None:
            return None
        s = str(v).strip()
        # "ILLEGIBLE" is a real extraction state, not a value. It is already
        # recorded in rule6_result_json and in the row's problems; putting it
        # in a search column would make it look like a manufacturer's name.
        return None if not s or s.upper() in ("NULL", "NONE", "ILLEGIBLE") else s

    quantity_value = text("net_quantity_value")
    quantity_unit = text("net_quantity_unit")
    net_quantity = " ".join(p for p in (quantity_value, quantity_unit) if p) or None

    return {
        "manufacturer": text("manufacturer"),
        "mrp": parse_number(text("mrp_value")),
        "net_quantity": net_quantity,
        "mfg_date": text("mfg_date"),
    }


def save_inspection(db: Session, *,
                    rule6: dict[str, Any],
                    rule7: Optional[dict[str, Any]],
                    overall_status: str,
                    findings: list[str],
                    product_name: Optional[str] = None,
                    rule6_src_path: Optional[str] = None,
                    rule7_src_path: Optional[str] = None,
                    existing: Optional[Inspection] = None,
                    inspector: Optional[Any] = None,
                    analysis: Optional[dict[str, Any]] = None) -> Inspection:
    """
    Persist one completed inspection, or update the one `existing` names.

    The update path is what keeps a single pack a single record. PRAMAAN's
    Rule 7 step is a SECOND call to POST /inspect for a pack whose Rule 6
    result the client already holds; without `existing` that call would
    write a fresh row carrying only a Rule 7 photo, and the history would
    show two half-inspections of one product. api/main.py passes the row
    the client's inspection_id names.

    The caller commits (the session_scope() around it). This function
    flushes to obtain the row id, because evidence files are stored in a
    directory named after it.
    """
    row = existing or Inspection()
    row.overall_status = overall_status
    row.findings_json = [str(f) for f in findings]
    row.rule6_result_json = _jsonable(rule6)
    row.mandatory_present = rule6.get("mandatory_present", 0)
    row.mandatory_total = rule6.get("mandatory_total", 0)
    for column, value in _summary_columns(rule6).items():
        setattr(row, column, value)

    # Only overwrite a stored product name when a new one was supplied — the
    # Rule 7 follow-up call need not resend it.
    if product_name is not None:
        row.product_name = product_name

    # Attribution is set once, by whoever created the inspection, and is not
    # rewritten when a second officer adds a Rule 7 measurement to it: the
    # record must keep saying who actually performed the original inspection.
    if inspector is not None and row.inspector_id is None:
        row.inspector_id = inspector.id
        row.inspector_name = inspector.full_name or inspector.username

    # Written only when this call actually produced one. Passing None leaves
    # whatever is already stored alone, which is what the Rule 7 follow-up
    # call needs: that call carries no declaration photograph, so overwriting
    # here would replace a real readability measurement with "not assessed"
    # and the record would get worse because more work was done on it.
    if analysis is not None:
        row.analysis_json = _jsonable(analysis)

    overlay_b64 = None
    if rule7 is not None:
        overlay_b64 = rule7.get("overlay_png_base64")
        row.rule7_result_json = _jsonable({k: v for k, v in rule7.items()
                                           if k != "overlay_png_base64"})
        row.rule7_verdict = rule7.get("verdict")
    elif existing is None:
        row.rule7_result_json = None
        row.rule7_verdict = None

    if existing is None:
        db.add(row)
    db.flush()   # assigns row.id, which names the evidence directory

    if rule6_src_path:
        stored = _copy_evidence(row.id, rule6_src_path, "rule6")
        if stored:
            row.rule6_image_path = stored
    if rule7_src_path:
        stored = _copy_evidence(row.id, rule7_src_path, "rule7")
        if stored:
            row.rule7_image_path = stored
    if overlay_b64:
        stored = _write_overlay(row.id, overlay_b64)
        if stored:
            row.rule7_overlay_path = stored

    db.flush()
    return row


# ---------------------------------------------------------------------------
# Reading and deleting
# ---------------------------------------------------------------------------

def get_inspection(db: Session, inspection_id: int) -> Optional[Inspection]:
    return db.get(Inspection, inspection_id)


def list_inspections(db: Session, *, limit: int = 20, offset: int = 0,
                     status: Optional[str] = None,
                     q: Optional[str] = None,
                     date_from: Optional[datetime] = None,
                     date_to: Optional[datetime] = None,
                     ) -> tuple[list[Inspection], int]:
    """
    Most recent first, with the total matching count so a caller can page
    without guessing. `status` filters on the stored verdict; `q` is a
    case-insensitive substring of the product name or manufacturer — both
    hit the indexed denormalised columns, not the JSON.

    date_from/date_to are naive UTC, matching how created_at is stored (see
    storage/models.py). The caller is responsible for turning a calendar day
    into a half-open range; this function compares exactly what it is given.
    """
    filters = []
    if status:
        filters.append(Inspection.overall_status == status)
    if q:
        like = f"%{q}%"
        filters.append(or_(Inspection.product_name.ilike(like),
                           Inspection.manufacturer.ilike(like)))
    if date_from is not None:
        filters.append(Inspection.created_at >= date_from)
    if date_to is not None:
        filters.append(Inspection.created_at < date_to)

    total = db.scalar(select(func.count()).select_from(Inspection).where(*filters)) or 0
    rows = db.scalars(
        select(Inspection).where(*filters)
                          .order_by(Inspection.created_at.desc(), Inspection.id.desc())
                          .limit(limit).offset(offset)
    ).all()
    return list(rows), total


def delete_inspection(db: Session, inspection_id: int) -> bool:
    """
    Remove one inspection and its evidence directory. Returns False if no
    such row existed, so the caller can answer 404 rather than pretending a
    delete happened.
    """
    row = db.get(Inspection, inspection_id)
    if row is None:
        return False

    db.delete(row)
    evidence = EVIDENCE_DIR / str(inspection_id)
    if evidence.exists():
        shutil.rmtree(evidence, ignore_errors=True)
    return True


# ---------------------------------------------------------------------------
# Dashboard aggregation
# ---------------------------------------------------------------------------
#: How many recent non-passing inspections the findings tally reads.
#:
#: findings_json is a JSON column, so there is no way to GROUP BY a finding
#: in SQLite without reading the rows. This is the one aggregate here that
#: cannot be done entirely in SQL, so it is bounded: the query projects ONLY
#: the findings column (never whole rows, never evidence) of the most recent
#: non-passing inspections. Everything else on the dashboard is a real
#: COUNT/SUM over indexed columns and is exact at any table size.
FINDINGS_WINDOW = 200

#: Verdict string for a clean inspection. Imported as a literal rather than
#: from engine/verdict.py to keep the storage layer free of engine imports it
#: does not need — this file only ever compares stored strings, it never
#: produces one.
_COMPLIANT = "COMPLIANT"


def dashboard_stats(db: Session, *, recent_limit: int = 8,
                    findings_window: int = FINDINGS_WINDOW) -> dict[str, Any]:
    """
    Everything the enforcement dashboard shows, aggregated in the database.

    NOTHING HERE DECIDES ANYTHING. Every number is a count of verdicts
    engine/verdict.py already issued and repository.save_inspection()
    already stored. There is no branch below that could turn a stored
    REVIEW into a compliant tally, and no status is derived from anything
    other than the `overall_status` column itself — CLAUDE.md rule 1 again.

    Returned counts are keyed by the exact strings stored in the column, so
    a future fourth status shows up here without a change to this function
    (the API layer maps the three it knows and reports the rest as-is).
    """
    # --- verdict mix: one GROUP BY over an indexed column.
    by_status: dict[str, int] = {
        str(status): int(count)
        for status, count in db.execute(
            select(Inspection.overall_status, func.count())
            .group_by(Inspection.overall_status)
        ).all()
    }
    total = sum(by_status.values())

    # --- Rule 7 mix. A null rule7_verdict means two different things and an
    #     officer needs them separated: "no measurement photo was taken" and
    #     "a photo was measured but no target row was ever selected". The
    #     first is a normal incomplete inspection, the second is an
    #     abandoned measurement. rule7_result_json IS NULL distinguishes them.
    #
    #     Note the cast. SQLAlchemy's JSON type stores a Python None as the
    #     JSON value `null` (the four characters), NOT as SQL NULL, so
    #     `.is_(None)` matches nothing and every unmeasured inspection would
    #     be reported as "measured, awaiting selection". On the way out the
    #     column deserialises back to Python None, which is why nothing else
    #     in PRAMAAN notices — it only bites a query that filters in SQL.
    #     Casting to text and comparing against 'null' catches both forms and
    #     works on SQLite and on Postgres.
    unmeasured = or_(Inspection.rule7_result_json.is_(None),
                     cast(Inspection.rule7_result_json, String) == "null")
    measured = case((unmeasured, 0), else_=1)
    rule7 = {"PASS": 0, "FAIL": 0, "REVIEW": 0,
             "pending_selection": 0, "not_measured": 0}
    for verdict, was_measured, count in db.execute(
        select(Inspection.rule7_verdict, measured, func.count())
        .group_by(Inspection.rule7_verdict, measured)
    ).all():
        count = int(count)
        if verdict in ("PASS", "FAIL", "REVIEW"):
            rule7[str(verdict)] += count
        elif was_measured:
            rule7["pending_selection"] += count
        else:
            rule7["not_measured"] += count

    # --- declaration shortfall, straight from the denormalised counters.
    incomplete = db.scalar(
        select(func.count()).select_from(Inspection)
        .where(Inspection.mandatory_present < Inspection.mandatory_total)
    ) or 0
    missing_declarations = db.scalar(
        select(func.coalesce(
            func.sum(Inspection.mandatory_total - Inspection.mandatory_present), 0))
        .where(Inspection.mandatory_present < Inspection.mandatory_total)
    ) or 0

    # --- most frequent findings among inspections that did not come out
    #     clean. Compliant rows are excluded on purpose: their findings are
    #     records of checks that PASSED ("Rule 7: ... - PASS"), and counting
    #     those next to violations would read as a violation league table
    #     with passes in it.
    finding_counts: dict[str, int] = {}
    for (findings,) in db.execute(
        select(Inspection.findings_json)
        .where(Inspection.overall_status != _COMPLIANT)
        .order_by(Inspection.created_at.desc(), Inspection.id.desc())
        .limit(findings_window)
    ).all():
        for finding in (findings or []):
            text = str(finding).strip()
            if text:
                finding_counts[text] = finding_counts.get(text, 0) + 1
    top_findings = sorted(finding_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:8]

    considered = min(total - by_status.get(_COMPLIANT, 0), findings_window)

    recent = list(db.scalars(
        select(Inspection)
        .order_by(Inspection.created_at.desc(), Inspection.id.desc())
        .limit(recent_limit)
    ).all())

    return {
        "total": total,
        "by_status": by_status,
        "rule7": rule7,
        "incomplete_declarations": int(incomplete),
        "missing_declarations": int(missing_declarations),
        "top_findings": [{"finding": text, "count": n} for text, n in top_findings],
        "findings_considered": max(0, considered),
        "recent": recent,
    }

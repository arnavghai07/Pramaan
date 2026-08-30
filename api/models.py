"""
api/models.py — PRAMAAN
========================
Pydantic request/response contracts for the FastAPI service. These mirror the
dicts that engine.vlm_extract.extract() and engine.measure_chart.measure()
already return; FastAPI validates the return value against response_model and
serialises it, so the shape here must match those functions exactly.
"""
from typing import Optional

from pydantic import BaseModel


class FieldRow(BaseModel):
    field: str
    state: str          # PRESENT | MISSING | REVIEW
    value: Optional[str] = None
    mandatory: bool


class ScanResponse(BaseModel):
    fields: dict
    rows: list[FieldRow]
    problems: list[str]
    mandatory_present: int
    mandatory_total: int
    best_rotation: Optional[str] = None
    orientations_tried: list[str]


class MeasureRow(BaseModel):
    x: float
    y: float
    w: float
    h: float
    height_mm: float


class MeasureResponse(BaseModel):
    width_px: int
    height_px: int
    tilt_spread_pct: float
    capture_scale_ppm: float
    rows: list[MeasureRow]


class ErrorResponse(BaseModel):
    detail: str


# ---------------------------------------------------------------------------
# Phase D — unified inspection (Rule 6 + Rule 7 combined verdict)
# ---------------------------------------------------------------------------
class Rule7Row(BaseModel):
    x: float
    y: float
    w: float
    h: float
    height_mm: float


class CandidatesResponse(BaseModel):
    """
    Response for POST /measure/candidates — every detected row on a Rule 7
    photo, with no target chosen yet. Used to let an inspector pick which
    row is the MRP numeral by pointing at it on the overlay image, rather
    than being asked for a row number.
    """
    tilt_spread_pct: float
    capture_scale_ppm: float
    rows: list[Rule7Row]
    overlay_png_base64: str


class Rule7Result(BaseModel):
    """
    Rule 7's contribution to a combined inspection. "target_index" is
    deliberately NOT part of this response shape — the row it refers to is
    already reflected in measured_height_mm/overlay_png_base64, and
    "selection_method" is where the API records HOW the target was picked
    ("manual" today; reserved "auto" once automatic MRP-numeral
    identification exists) without callers needing to know an index was
    ever involved.
    """
    attempted: bool
    problem: Optional[str] = None
    tilt_spread_pct: Optional[float] = None
    capture_scale_ppm: Optional[float] = None
    rows: list[Rule7Row] = []
    measured_height_mm: Optional[float] = None
    threshold_mm: Optional[float] = None
    verdict: Optional[str] = None          # PASS | FAIL | REVIEW | None (pending selection)
    selection_method: Optional[str] = None  # "manual" | "auto" (reserved)
    overlay_png_base64: Optional[str] = None


class InspectionResponse(BaseModel):
    rule6: ScanResponse
    rule7: Optional[Rule7Result] = None
    overall_status: str   # COMPLIANT | NON_COMPLIANT | NEEDS_MANUAL_REVIEW
    findings: list[str] = []

"""
api/models.py — PRAMAAN
========================
Pydantic request/response contracts for the FastAPI service. These mirror the
dicts that engine.vlm_extract.extract() and engine.measure_chart.measure()
already return; FastAPI validates the return value against response_model and
serialises it, so the shape here must match those functions exactly.
"""
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_serializer


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
    # Set once the inspection has been persisted (storage/repository.py).
    # Optional, and null when persistence failed, so an existing client that
    # ignores this field behaves exactly as it did before history existed —
    # and so a storage fault can never destroy a verdict that has already
    # cost a minute of VLM inference.
    inspection_id: Optional[int] = None


# ---------------------------------------------------------------------------
# Inspection history (SQLite via storage/)
# ---------------------------------------------------------------------------
class InspectionSummary(BaseModel):
    """
    One row of GET /inspections. Carries only the denormalised columns — a
    list view never drags a full Rule 6 result or a base64 overlay off disk
    to render a line of history.
    """
    id: int
    created_at: datetime
    product_name: Optional[str] = None
    overall_status: str
    mandatory_present: int
    mandatory_total: int
    rule7_verdict: Optional[str] = None
    manufacturer: Optional[str] = None
    mrp: Optional[float] = None
    net_quantity: Optional[str] = None
    mfg_date: Optional[str] = None
    has_rule7: bool = False
    inspector_name: Optional[str] = None

    model_config = {"from_attributes": True}

    @field_serializer("created_at")
    def _utc(self, value: datetime) -> str:
        """
        SQLite stores naive datetimes; storage/models.py writes UTC by
        convention. Marking it explicitly on the way out stops a browser
        from reading a UTC timestamp as local time.
        """
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class InspectionDetail(InspectionSummary):
    """
    GET /inspections/{id} — the full stored result, in the same shapes a
    live POST /inspect returns, so a replayed inspection can be rendered by
    exactly the same client code. The Rule 7 evidence overlay is read back
    from disk and re-attached as base64 here.
    """
    rule6: ScanResponse
    rule7: Optional[Rule7Result] = None
    findings: list[str] = []
    # Which evidence files exist for this inspection. The images themselves
    # are served by GET /inspections/{id}/evidence/{kind} rather than inlined
    # here: the Rule 7 overlay alone runs to ~9 MB of base64, which would
    # make opening a history record slower than running the original scan.
    evidence: list[str] = []
    rule6_image_stored: bool = False
    rule7_image_stored: bool = False
    rule7_overlay_stored: bool = False


class InspectionListResponse(BaseModel):
    items: list[InspectionSummary]
    total: int      # matching rows, ignoring limit/offset
    limit: int
    offset: int


class DeleteResponse(BaseModel):
    id: int
    deleted: bool


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    # 72 bytes is bcrypt's hard limit; anything longer is silently truncated
    # by the algorithm, so it is refused here rather than half-checked later.
    password: str = Field(min_length=1, max_length=72)


class UserOut(BaseModel):
    """
    A user as the client may see them. Deliberately has no password_hash
    field: a response model FastAPI filters through cannot leak a column
    that was never declared on it, even if a handler returns the whole ORM
    row by accident.
    """
    id: int
    username: str
    role: Literal["ADMIN", "INSPECTOR"]
    full_name: Optional[str] = None

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int          # seconds
    user: UserOut


# ---------------------------------------------------------------------------
# Enforcement dashboard
# ---------------------------------------------------------------------------
class StatusCounts(BaseModel):
    """
    How many stored inspections carry each verdict. `other` exists so a
    verdict string this build does not know about is still counted in the
    total instead of vanishing from the dashboard — a silently dropped
    inspection would read as "fewer violations", which is exactly the kind
    of quiet omission CLAUDE.md rule 2 forbids.
    """
    compliant: int = 0
    non_compliant: int = 0
    needs_manual_review: int = 0
    other: int = 0


class Rule7Counts(BaseModel):
    """
    The Rule 7 measurement mix. `pending_selection` is an inspection whose
    measurement photo was processed but where no target numeral was ever
    picked, and `not_measured` is one where no Rule 7 photo was taken at
    all. Both are "no Rule 7 verdict", and an officer chasing incomplete
    work needs to know which.
    """
    passed: int = 0
    failed: int = 0
    review: int = 0
    pending_selection: int = 0
    not_measured: int = 0


class FindingCount(BaseModel):
    finding: str
    count: int


class DashboardResponse(BaseModel):
    """
    GET /dashboard. Every number here is an aggregate of verdicts already
    stored by storage/repository.py — nothing on this endpoint re-runs the
    engine or re-decides a status. `compliance_rate` is plain arithmetic
    over the counts (compliant / total x 100), null when there are no
    inspections yet rather than 0, because "0% compliant" and "nothing
    inspected" are different facts.
    """
    total: int
    status: StatusCounts
    compliance_rate: Optional[float] = None
    rule7: Rule7Counts
    #: Inspections with at least one mandatory Rule 6 declaration missing.
    incomplete_declarations: int
    #: Total count of missing mandatory declarations across those inspections.
    missing_declarations: int
    top_findings: list[FindingCount] = []
    #: How many non-compliant/review inspections the findings tally read.
    #: Bounded — see repository.FINDINGS_WINDOW.
    findings_considered: int
    recent: list[InspectionSummary] = []

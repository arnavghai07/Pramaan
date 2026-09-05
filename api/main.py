"""
api/main.py — PRAMAAN
=======================
FastAPI service wrapping the vision engine (engine/vlm_extract.py,
engine/measure_chart.py) behind two endpoints:

    POST /scan     image in  -> structured Rule 6 fields, verdict data out
    POST /measure  image in  -> Rule 7 millimetre measurements out

The engine functions raise typed exceptions instead of calling sys.exit() or
printing to stdout (see the refactor notes in vlm_extract.py and
measure_chart.py). The exception handlers below are the "error middleware":
every failure mode the engine defines is mapped to a JSON body with a
readable message, never a raw 500 traceback.

Completed inspections are persisted to SQLite through storage/ — see
POST /inspect and the GET/DELETE /inspections routes at the bottom of this
file. Persistence is strictly downstream of the verdict: nothing in
storage/ can change what engine/verdict.py decided.

Run with:
    uvicorn api.main:app --reload --port 8000
"""
import base64
import json
import os
import sys
import tempfile
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from engine.vlm_extract import BackendError, ExtractionFailed, extract
from engine.measure_chart import (MarkerNotFound, MarkerTilted, measure, rule7_result,
                                  rule7_measure_selected_region)
from engine.verdict import combine_status

from api.models import (CandidatesResponse, DashboardResponse, DeleteResponse,
                        InspectionDetail, InspectionListResponse, InspectionResponse,
                        InspectionSummary, MeasureResponse, Rule7Result, ScanResponse)
from api.auth import current_user, require_admin
from api.auth import router as auth_router

from storage import repository
from storage import users as users_repo
from storage.database import get_db, init_db, session_scope
from storage.models import User


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Create data/, data/evidence/ and any missing tables before the first
    request, then seed the demo accounts if no user exists yet. A fresh
    clone needs no migration step and no user-creation step: start uvicorn
    and you can sign in.
    """
    init_db()
    with session_scope() as db:
        users_repo.seed_demo_users(db)
    yield


app = FastAPI(title="PRAMAAN", version="0.3.0", lifespan=lifespan)

# The Next.js dev server (web/) calls this API from a different origin, and a
# browser fetch is blocked client-side without this even though the server
# still returns 200 — curl doesn't enforce CORS, so this gap is invisible
# from the CLI/curl gates Phase A used.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)

DEFAULT_MODEL = os.environ.get("VLM_MODEL", "qwen2.5vl:7b")


@app.exception_handler(BackendError)
async def backend_error_handler(request, exc):
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(ExtractionFailed)
async def extraction_failed_handler(request, exc):
    return JSONResponse(status_code=422, content={
        "detail": "no image orientation produced a usable extraction",
        "orientations_tried": exc.tried,
    })


@app.exception_handler(MarkerNotFound)
async def marker_not_found_handler(request, exc):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(MarkerTilted)
async def marker_tilted_handler(request, exc):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


async def _save_upload(image: UploadFile) -> str:
    """
    Uploads arrive as an in-memory/spooled file object; the engine functions
    take a path on disk (they call cv2.imread), so every request needs one
    temp file. Always cleaned up by the caller's finally block.
    """
    suffix = Path(image.filename or "upload.jpg").suffix or ".jpg"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    with open(tmp_path, "wb") as fh:
        fh.write(await image.read())
    return tmp_path


@app.post("/scan", response_model=ScanResponse)
async def scan(image: UploadFile = File(...),
               backend: str = Form("ollama"),
               model: Optional[str] = Form(None),
               user: User = Depends(current_user)):
    tmp_path = await _save_upload(image)
    try:
        return extract(tmp_path, backend=backend, model=model or DEFAULT_MODEL)
    finally:
        os.remove(tmp_path)


@app.post("/measure", response_model=MeasureResponse)
async def measure_endpoint(image: UploadFile = File(...),
                            marker_mm: float = Form(40.0),
                            user: User = Depends(current_user)):
    tmp_path = await _save_upload(image)
    try:
        return measure(tmp_path, marker_mm=marker_mm)
    finally:
        os.remove(tmp_path)


@app.post("/measure/candidates", response_model=CandidatesResponse)
async def measure_candidates(image: UploadFile = File(...),
                              marker_mm: float = Form(40.0),
                              user: User = Depends(current_user)):
    """
    Every Rule 7 candidate row on a photo, with no target chosen — the
    picking step of the inspector workflow. The UI shows the returned
    overlay (every row boxed, none highlighted) and lets the inspector tap
    the printed price; the row under that tap becomes the target_index the
    client later sends to POST /inspect. This endpoint never sees or needs
    a target — see rule7_result()'s docstring in engine/measure_chart.py.
    """
    tmp_path = await _save_upload(image)
    try:
        r7 = rule7_result(tmp_path, marker_mm=marker_mm)
        return {
            "tilt_spread_pct": r7["tilt_spread_pct"],
            "capture_scale_ppm": r7["capture_scale_ppm"],
            "rows": r7["rows"],
            "overlay_png_base64": base64.b64encode(r7["overlay_png"]).decode(),
        }
    finally:
        os.remove(tmp_path)


@app.post("/measure/region", response_model=Rule7Result)
async def measure_region(image: UploadFile = File(...),
                          marker_mm: float = Form(40.0),
                          rotation_deg: int = Form(0),
                          region_x: int = Form(...),
                          region_y: int = Form(...),
                          region_w: int = Form(...),
                          region_h: int = Form(...),
                          pdp_area_cm2: Optional[float] = Form(None),
                          container: str = Form("normal"),
                          user: User = Depends(current_user)):
    """
    FALLBACK Rule 7 measurement for when automatic candidate discovery
    could not isolate a clean, complete numeral: the inspector draws a
    rectangle around the complete MRP numeral instead of tapping a
    detected candidate. Preview endpoint, mirroring POST
    /measure/candidates - lets the UI show the trimmed-glyph evidence and
    the measurement (or an "ambiguous"/"nothing found" outcome) before the
    inspector commits to the full POST /inspect call.

    region_x/y/w/h are in the rectified-frame coordinates of the given
    rotation_deg orientation - see rule7_measure_selected_region()'s
    docstring in engine/measure_chart.py for exactly what that means and
    why the rectangle's own dimensions are never used as the measurement.
    """
    tmp_path = await _save_upload(image)
    try:
        r = rule7_measure_selected_region(
            tmp_path, marker_mm=marker_mm, rotation_deg=rotation_deg,
            region=(region_x, region_y, region_w, region_h),
            pdp_area_cm2=pdp_area_cm2, container=container)
        return {
            "attempted": True,
            "problem": r["problem"],
            "measured_height_mm": r["measured_height_mm"],
            "threshold_mm": r["threshold_mm"],
            "verdict": r["verdict"],
            "selection_method": "manual_region",
            "overlay_png_base64": (base64.b64encode(r["overlay_png"]).decode()
                                   if r["overlay_png"] else None),
        }
    finally:
        os.remove(tmp_path)


@app.post("/inspect", response_model=InspectionResponse)
async def inspect(rule6_image: Optional[UploadFile] = File(None),
                   rule6_result: Optional[str] = Form(None),
                   rule7_image: Optional[UploadFile] = File(None),
                   backend: str = Form("ollama"),
                   model: Optional[str] = Form(None),
                   marker_mm: float = Form(40.0),
                   pdp_area_cm2: Optional[float] = Form(None),
                   container: str = Form("normal"),
                   target_index: Optional[int] = Form(None),
                   rotation_deg: int = Form(0),
                   region_x: Optional[int] = Form(None),
                   region_y: Optional[int] = Form(None),
                   region_w: Optional[int] = Form(None),
                   region_h: Optional[int] = Form(None),
                   product_name: Optional[str] = Form(None),
                   inspection_id: Optional[int] = Form(None),
                   user: User = Depends(current_user)):
    """
    The unified workflow: Rule 6 declarations + (optional) Rule 7
    measurement, combined into one COMPLIANT / NON_COMPLIANT /
    NEEDS_MANUAL_REVIEW status by engine.verdict.combine_status() — never
    computed in the frontend. rule7_image is optional because a pack may be
    scanned for declarations alone; when it's omitted, combine_status()
    reports Rule 7 as not attempted rather than guessing a result, per
    CLAUDE.md rule 2, "silence is never a pass".

    rule6_image vs. rule6_result — exactly one is expected:
      - rule6_image: run Rule 6 VLM extraction now (the initial-scan path,
        unchanged from before this parameter existed).
      - rule6_result: a Rule 6 result this same client already received
        from an earlier call to this endpoint (or /scan), sent back as
        JSON matching ScanResponse's shape. Used when an inspector adds a
        Rule 7 measurement to a pack that was already scanned: Rule 6 has
        already run once and its output hasn't changed, so re-uploading
        the original photo here would only pay for a second, identical VLM
        inference. Validated against ScanResponse before use — a client
        must supply a shape the engine actually produced, not arbitrary
        fields — so a malformed value fails loudly (422) rather than
        silently producing a wrong verdict from bad data.

    Rule 7 target selection is one of two internal mechanisms, neither a
    product concept the response exposes:
      - target_index: an automatically-detected candidate row was tapped
        (see POST /measure/candidates). Always the 0-degree orientation
        today.
      - region_x/y/w/h (+ rotation_deg): the FALLBACK path — the inspector
        drew a rectangle because no automatic candidate cleanly bounded the
        complete numeral (see POST /measure/region and
        rule7_measure_selected_region()'s docstring in
        engine/measure_chart.py for why the rectangle itself is never
        trusted as the measurement). Takes priority over target_index if
        both are somehow given, since drawing a region is the explicit
        "automatic discovery didn't work" signal.
    Neither is ever returned in the response — Rule7Result carries the
    resolved measurement instead.

    PERSISTENCE. Every completed inspection is written to SQLite and its id
    returned as "inspection_id". Two optional inputs govern that:
      - product_name: a label for the history list. Never inferred — the
        VLM has no product-name field and the pack may not print one.
      - inspection_id: UPDATE the inspection this names instead of creating
        a new one. This is how the Rule 7 follow-up call (which sends
        rule6_result plus a rule7_image, for a pack already scanned once)
        adds its measurement to the existing record rather than leaving two
        half-inspections of one pack in the history. Validated BEFORE any
        extraction or measurement runs, so an unknown id costs nothing and
        can never discard a verdict that has already been computed.

    Storage failure never fails the request: the inspection is returned with
    inspection_id null and the reason logged server-side. A verdict that
    cost a minute of inference is not thrown away because a disk was full.
    """
    if inspection_id is not None:
        with session_scope() as db:
            if repository.get_inspection(db, inspection_id) is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"no stored inspection with id {inspection_id}")

    rule6_path = await _save_upload(rule6_image) if rule6_image else None
    rule7_path = await _save_upload(rule7_image) if rule7_image else None
    try:
        if rule6_result is not None:
            try:
                rule6 = ScanResponse(**json.loads(rule6_result)).model_dump()
            except (json.JSONDecodeError, ValidationError, TypeError):
                return JSONResponse(status_code=422, content={
                    "detail": "rule6_result could not be read as a prior Rule 6 result"})
        elif rule6_path:
            rule6 = extract(rule6_path, backend=backend, model=model or DEFAULT_MODEL)
        else:
            return JSONResponse(status_code=422, content={
                "detail": "either rule6_image or rule6_result is required"})

        rule7 = None
        has_region = None not in (region_x, region_y, region_w, region_h)
        if rule7_path and has_region:
            try:
                r = rule7_measure_selected_region(
                    rule7_path, marker_mm=marker_mm, rotation_deg=rotation_deg,
                    region=(region_x, region_y, region_w, region_h),
                    pdp_area_cm2=pdp_area_cm2, container=container)
                rule7 = {
                    "attempted": True,
                    "problem": r["problem"],
                    "measured_height_mm": r["measured_height_mm"],
                    "threshold_mm": r["threshold_mm"],
                    "verdict": r["verdict"],
                    "selection_method": "manual_region",
                    "overlay_png_base64": (base64.b64encode(r["overlay_png"]).decode()
                                           if r["overlay_png"] else None),
                }
            except (MarkerNotFound, MarkerTilted) as e:
                rule7 = {"attempted": True, "problem": str(e)}
        elif rule7_path:
            try:
                r7 = rule7_result(rule7_path, marker_mm=marker_mm,
                                  pdp_area_cm2=pdp_area_cm2, container=container,
                                  target_index=target_index)
                rule7 = {
                    "attempted": True,
                    "tilt_spread_pct": r7["tilt_spread_pct"],
                    "capture_scale_ppm": r7["capture_scale_ppm"],
                    "rows": r7["rows"],
                    "measured_height_mm": (r7["rows"][target_index]["height_mm"]
                                           if target_index is not None else None),
                    "threshold_mm": r7["threshold_mm"],
                    "verdict": r7["verdict"],
                    "selection_method": "manual" if target_index is not None else None,
                    "overlay_png_base64": base64.b64encode(r7["overlay_png"]).decode(),
                }
            except (MarkerNotFound, MarkerTilted, ValueError) as e:
                rule7 = {"attempted": True, "problem": str(e)}

        overall_status, findings = combine_status(rule6, rule7)

        # Persist AFTER the verdict exists and BEFORE the finally block
        # deletes the uploads — repository.save_inspection() copies those
        # temp files into permanent evidence storage. A file copy and one
        # INSERT, on a local SQLite file: milliseconds against a VLM
        # inference measured in minutes, so this adds nothing a user sees.
        saved_id = None
        try:
            with session_scope() as db:
                existing = (repository.get_inspection(db, inspection_id)
                            if inspection_id is not None else None)
                row = repository.save_inspection(
                    db, rule6=rule6, rule7=rule7, overall_status=overall_status,
                    findings=findings, product_name=product_name,
                    rule6_src_path=rule6_path, rule7_src_path=rule7_path,
                    existing=existing, inspector=user)
                saved_id = row.id
        except Exception as e:                      # noqa: BLE001 — see docstring
            print(f"[storage] inspection could not be saved: {e!r}", file=sys.stderr)

        return {"rule6": rule6, "rule7": rule7,
                "overall_status": overall_status, "findings": findings,
                "inspection_id": saved_id}
    finally:
        if rule6_path:
            os.remove(rule6_path)
        if rule7_path:
            os.remove(rule7_path)


# ---------------------------------------------------------------------------
# Inspection history
#
# Read-only replay of what POST /inspect already decided. None of these
# routes recomputes a verdict, re-runs the model, or re-measures anything:
# they return the stored decision as it was made. A stored REVIEW stays a
# REVIEW forever.
# ---------------------------------------------------------------------------

def _summary(row) -> dict:
    """Shared projection for both the list and detail responses."""
    return {
        "id": row.id,
        "created_at": row.created_at,
        "product_name": row.product_name,
        "overall_status": row.overall_status,
        "mandatory_present": row.mandatory_present,
        "mandatory_total": row.mandatory_total,
        "rule7_verdict": row.rule7_verdict,
        "manufacturer": row.manufacturer,
        "mrp": row.mrp,
        "net_quantity": row.net_quantity,
        "mfg_date": row.mfg_date,
        "has_rule7": row.rule7_result_json is not None,
        "inspector_name": row.inspector_name,
    }


def _day_bounds(date_from: Optional[date],
                date_to: Optional[date]) -> tuple[Optional[datetime], Optional[datetime]]:
    """
    Turn two calendar days into the half-open UTC range the repository
    compares against. date_to is inclusive as a DAY — a filter "to 5 Sep"
    must include an inspection recorded at 17:40 on 5 September, so the
    bound sent down is midnight at the START of 6 September.
    """
    start = datetime.combine(date_from, time.min) if date_from else None
    end = datetime.combine(date_to + timedelta(days=1), time.min) if date_to else None
    return start, end


@app.get("/inspections", response_model=InspectionListResponse)
def list_inspections(limit: int = Query(20, ge=1, le=100),
                     offset: int = Query(0, ge=0),
                     status: Optional[str] = Query(None,
                         description="COMPLIANT | NON_COMPLIANT | NEEDS_MANUAL_REVIEW"),
                     q: Optional[str] = Query(None,
                         description="substring of product name or manufacturer"),
                     date_from: Optional[date] = Query(None,
                         description="inclusive, YYYY-MM-DD, UTC"),
                     date_to: Optional[date] = Query(None,
                         description="inclusive, YYYY-MM-DD, UTC"),
                     db: Session = Depends(get_db),
                     user: User = Depends(current_user)):
    """
    Recent inspections, newest first. Summary columns only — no Rule 6
    field list and no base64 overlay, so this stays fast as history grows.
    `total` is the count matching the filters, ignoring limit/offset.
    """
    start, end = _day_bounds(date_from, date_to)
    rows, total = repository.list_inspections(
        db, limit=limit, offset=offset, status=status, q=q,
        date_from=start, date_to=end)
    return {"items": [InspectionSummary(**_summary(r)) for r in rows],
            "total": total, "limit": limit, "offset": offset}


@app.get("/inspections/{inspection_id}", response_model=InspectionDetail)
def get_inspection(inspection_id: int,
                   include_overlay: bool = Query(False,
                       description="inline the Rule 7 overlay as base64 (~9 MB); "
                                   "prefer the evidence endpoint"),
                   db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    """
    One complete stored inspection, in the same shapes POST /inspect
    returns, so the same client code can render a replayed inspection and a
    live one.

    The Rule 7 overlay is NOT inlined by default — see
    repository.rule7_for_response(). `evidence` lists which images exist;
    fetch each from GET /inspections/{id}/evidence/{kind}.
    """
    row = repository.get_inspection(db, inspection_id)
    if row is None:
        raise HTTPException(status_code=404,
                            detail=f"no stored inspection with id {inspection_id}")

    available = repository.available_evidence(row)
    return {**_summary(row),
            "rule6": row.rule6_result_json,
            "rule7": repository.rule7_for_response(row, include_overlay=include_overlay),
            "findings": row.findings_json or [],
            "evidence": available,
            "rule6_image_stored": "rule6" in available,
            "rule7_image_stored": "rule7" in available,
            "rule7_overlay_stored": "overlay" in available}


@app.get("/inspections/{inspection_id}/evidence/{kind}")
def get_evidence(inspection_id: int, kind: str,
                 db: Session = Depends(get_db),
                 user: User = Depends(current_user)):
    """
    Stream one stored evidence image: kind is "rule6", "rule7" or "overlay".

    Two distinct 404s on purpose. An unknown inspection is one thing; an
    inspection that exists but never had that image (Rule 6 answered from a
    cached result, no Rule 7 photo taken, a marker photo that failed before
    an overlay could be drawn) is another, and an inspector chasing missing
    evidence needs to be told which. Neither is an error state of the
    system — "no Rule 7 photo" is a normal, complete inspection.

    `kind` is resolved through repository.EVIDENCE_KINDS, never interpolated
    into a filesystem path, so no request can address a file the inspection
    record does not name.
    """
    row = repository.get_inspection(db, inspection_id)
    if row is None:
        raise HTTPException(status_code=404,
                            detail=f"no stored inspection with id {inspection_id}")
    if kind not in repository.EVIDENCE_KINDS:
        raise HTTPException(
            status_code=404,
            detail=f"unknown evidence kind {kind!r}; expected one of "
                   f"{', '.join(repository.EVIDENCE_KINDS)}")

    path = repository.evidence_path(row, kind)
    if path is None:
        raise HTTPException(
            status_code=404,
            detail=f"inspection {inspection_id} has no stored {kind} image")

    media_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return FileResponse(path, media_type=media_type,
                        filename=f"inspection-{inspection_id}-{kind}{path.suffix}")


@app.delete("/inspections/{inspection_id}", response_model=DeleteResponse)
def delete_inspection(inspection_id: int, db: Session = Depends(get_db),
                      admin: User = Depends(require_admin)):
    """
    Remove one inspection and its stored evidence images.

    ADMIN only. Deleting an inspection destroys the evidence behind an
    enforcement decision, which is not something a field officer should be
    able to do to their own record on a whim.
    """
    if not repository.delete_inspection(db, inspection_id):
        raise HTTPException(status_code=404,
                            detail=f"no stored inspection with id {inspection_id}")
    db.commit()
    return {"id": inspection_id, "deleted": True}


# ---------------------------------------------------------------------------
# Enforcement dashboard
# ---------------------------------------------------------------------------
#: Verdict strings this build knows, mapped to their DashboardResponse field.
#: Anything else a row carries is counted under "other" rather than dropped.
_STATUS_FIELDS = {
    "COMPLIANT": "compliant",
    "NON_COMPLIANT": "non_compliant",
    "NEEDS_MANUAL_REVIEW": "needs_manual_review",
}


@app.get("/dashboard", response_model=DashboardResponse)
def dashboard(recent: int = Query(8, ge=1, le=25,
                                  description="how many recent inspections to return"),
              db: Session = Depends(get_db),
              user: User = Depends(current_user)):
    """
    Summary view for enforcement officials: verdict mix, Rule 7 mix,
    declaration shortfall, the findings seen most often, and the latest
    inspections.

    Available to any signed-in officer — an inspector needs to see the
    district's compliance picture as much as an administrator does, and
    nothing here exposes a field the history list does not already show.

    THIS ENDPOINT DECIDES NOTHING. It counts verdicts that
    engine/verdict.py issued and storage/repository.py stored, and it does
    the counting in SQL (repository.dashboard_stats) rather than pulling
    history into the browser. compliance_rate below is arithmetic over
    those counts, not a judgement: null when nothing has been inspected
    yet, because "no data" must not render as 0% compliant.
    """
    stats = repository.dashboard_stats(db, recent_limit=recent)

    counts = {"compliant": 0, "non_compliant": 0, "needs_manual_review": 0, "other": 0}
    for status_value, count in stats["by_status"].items():
        counts[_STATUS_FIELDS.get(status_value, "other")] += count

    total = stats["total"]
    rate = round(counts["compliant"] / total * 100, 1) if total else None

    r7 = stats["rule7"]
    return {
        "total": total,
        "status": counts,
        "compliance_rate": rate,
        "rule7": {"passed": r7["PASS"], "failed": r7["FAIL"], "review": r7["REVIEW"],
                  "pending_selection": r7["pending_selection"],
                  "not_measured": r7["not_measured"]},
        "incomplete_declarations": stats["incomplete_declarations"],
        "missing_declarations": stats["missing_declarations"],
        "top_findings": stats["top_findings"],
        "findings_considered": stats["findings_considered"],
        "recent": [InspectionSummary(**_summary(r)) for r in stats["recent"]],
    }

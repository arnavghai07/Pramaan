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

Run with:
    uvicorn api.main:app --reload --port 8000
"""
import base64
import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from engine.vlm_extract import BackendError, ExtractionFailed, extract
from engine.measure_chart import (MarkerNotFound, MarkerTilted, measure, rule7_result,
                                  rule7_measure_selected_region)
from engine.verdict import combine_status

from api.models import (CandidatesResponse, InspectionResponse, MeasureResponse,
                        Rule7Result, ScanResponse)

app = FastAPI(title="PRAMAAN", version="0.1.0")

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
               model: Optional[str] = Form(None)):
    tmp_path = await _save_upload(image)
    try:
        return extract(tmp_path, backend=backend, model=model or DEFAULT_MODEL)
    finally:
        os.remove(tmp_path)


@app.post("/measure", response_model=MeasureResponse)
async def measure_endpoint(image: UploadFile = File(...),
                            marker_mm: float = Form(40.0)):
    tmp_path = await _save_upload(image)
    try:
        return measure(tmp_path, marker_mm=marker_mm)
    finally:
        os.remove(tmp_path)


@app.post("/measure/candidates", response_model=CandidatesResponse)
async def measure_candidates(image: UploadFile = File(...),
                              marker_mm: float = Form(40.0)):
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
                          container: str = Form("normal")):
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
                   region_h: Optional[int] = Form(None)):
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
    """
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
        return {"rule6": rule6, "rule7": rule7,
                "overall_status": overall_status, "findings": findings}
    finally:
        if rule6_path:
            os.remove(rule6_path)
        if rule7_path:
            os.remove(rule7_path)

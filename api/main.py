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
import os
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from engine.vlm_extract import BackendError, ExtractionFailed, extract
from engine.measure_chart import MarkerNotFound, measure

from api.models import MeasureResponse, ScanResponse

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

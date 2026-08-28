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

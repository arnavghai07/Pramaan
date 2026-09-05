"""
analysis.py  —  PRAMAAN
========================
The three deterministic checks that sit BESIDE Rule 6 (completeness) and
Rule 7 (character height), never inside them:

    placement                — legal placement conformity. Always
                               NOT_ASSESSED; this system cannot verify it.
    capture_observation      — ADVISORY. Does the photograph itself look
                               cropped or empty? A fact about the capture,
                               never about the pack, and never able to move
                               the compliance verdict.
    readability              — can this inspection image be read at all?
    declaration_validation   — are the declarations that ARE present
                               structurally valid and self-consistent?

WHY A SEPARATE MODULE
----------------------
engine/vlm_extract.py answers "what is printed"; engine/measure_chart.py
answers "how tall is it". Neither owns "is this evidence good enough" or "is
this value even shaped like the declaration it claims to be". Putting those
into either file would make the two things that already work harder to reason
about, and would mean a change here could alter a Rule 6 or Rule 7 result.
Nothing in this file touches either one - it reads their output and the
photograph, and returns a parallel result.

FOUR STATES, AND WHY THE FOURTH EXISTS
---------------------------------------
PASS          the check ran on adequate evidence and found nothing wrong.
FAIL          a specific, deterministic non-conformity was established.
REVIEW        evidence exists but is ambiguous; an officer must decide.
NOT_ASSESSED  the input this check needs was absent, or the check cannot
              technically be performed at all.

REVIEW and NOT_ASSESSED are different facts and are kept apart on purpose.
"The photo was too marginal to judge" is a statement about this pack; "no
photograph was supplied" is a statement about the inspection. Collapsing
either into PASS is the failure CLAUDE.md rule 2 exists to prevent, and
collapsing them into each other hides which one an officer has to fix.

WHAT THIS MODULE DELIBERATELY DOES NOT CLAIM
---------------------------------------------
It does not decide legal PLACEMENT conformity. Rules 6 and 9 of the Legal
Metrology (Packaged Commodities) Rules speak about which panel a declaration
sits on, whether the declarations are grouped together, and whether they are
on the principal display panel. Answering that needs a bounding box PER
DECLARATION on a known panel geometry. The VLM in this project returns field
VALUES and no coordinates, and this repository carries no panel-geometry rule
source, so a placement PASS here would be a legal claim with nothing behind
it. The placement check therefore returns NOT_ASSESSED for every pack, and
says so in as many words.

Whether the PHOTOGRAPH is adequate is a separate question with a separate
answer, and it lives in capture_observation(). Keeping them apart matters:
merged, a cropped-looking photo made the placement row read NEEDS REVIEW above
an explanation stating placement had not been assessed, which is two different
claims under one heading. Split, each row says one thing.
"""
from __future__ import annotations

import re
from typing import Any, Optional

import cv2
import numpy as np

PASS = "PASS"
FAIL = "FAIL"
REVIEW = "REVIEW"
NOT_ASSESSED = "NOT_ASSESSED"

#: Worst-first. Used to combine sub-signals and to roll the three checks up
#: into one overall state without anywhere writing that ordering twice.
_SEVERITY = {FAIL: 3, REVIEW: 2, NOT_ASSESSED: 1, PASS: 0}

PLACEMENT = "placement"
CAPTURE_OBSERVATION = "capture_observation"
READABILITY = "readability"
DECLARATION_VALIDATION = "declaration_validation"

CHECK_TITLES = {
    PLACEMENT: "Declaration placement",
    CAPTURE_OBSERVATION: "Capture observation",
    READABILITY: "Readability",
    DECLARATION_VALIDATION: "Declaration validation",
}

#: Checks whose state is reported to the officer but is NOT allowed to move
#: the overall compliance status. See _check()'s `advisory` argument and
#: engine/verdict.py._apply_analysis().
ADVISORY_CHECKS = {CAPTURE_OBSERVATION}

#: Bumped if the shape below ever changes, so a stored result from an older
#: build can be recognised rather than misread as the current shape.
SCHEMA_VERSION = 1

#: Every image measurement is taken on the frame scaled to this longest side.
#: Laplacian variance and pixel-count ratios are both resolution-dependent, so
#: a 48 MP phone photo and a 2 MP one would otherwise be scored on different
#: yardsticks and the thresholds below would mean nothing. Resolution adequacy
#: is judged separately, on the ORIGINAL pixel dimensions.
_WORK_SIDE = 1000


def _worst(states) -> str:
    states = [s for s in states if s]
    if not states:
        return NOT_ASSESSED
    return max(states, key=lambda s: _SEVERITY.get(s, 0))


def _finding(severity: str, message: str) -> dict[str, str]:
    return {"severity": severity, "message": message}


def _check(name: str, state: str, explanation: str,
           findings: Optional[list] = None,
           metrics: Optional[dict] = None) -> dict[str, Any]:
    """
    `advisory` is derived from the check name, not passed in, so a check
    cannot be advisory in one code path and verdict-bearing in another. It
    travels with the stored result: a record written today keeps its own
    answer to "was this allowed to change the verdict?" even if that set
    changes later.
    """
    return {
        "check": name,
        "title": CHECK_TITLES[name],
        "state": state,
        "explanation": explanation,
        "advisory": name in ADVISORY_CHECKS,
        "findings": findings or [],
        "metrics": metrics or {},
    }


# ---------------------------------------------------------------------------
# Shared image loading
# ---------------------------------------------------------------------------

def _load(image_path: Optional[str]):
    """
    Returns (gray_at_work_scale, original_h, original_w) or None.

    cv2.imread returns None for a missing or undecodable file rather than
    raising, so the None case is a normal branch here, not an error path -
    it becomes NOT_ASSESSED, never an exception that would cost a caller a
    completed inspection.
    """
    if not image_path:
        return None
    bgr = cv2.imread(image_path)
    if bgr is None or bgr.size == 0:
        return None
    h, w = bgr.shape[:2]
    scale = _WORK_SIDE / float(max(h, w))
    if scale < 1.0:
        bgr = cv2.resize(bgr, (int(round(w * scale)), int(round(h * scale))),
                         interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), h, w


# ---------------------------------------------------------------------------
# 1. READABILITY
#
# "Can the declaration reasonably be read from THIS inspection image?" - a
# question about the photograph. Rule 7 asks a different question about the
# pack ("is the printed character tall enough?"), and a pack can fail one
# while passing the other in either direction: a razor-sharp macro shot of
# 0.9 mm type is perfectly readable and still illegal, and a blurred photo of
# 4 mm type is legal print nobody can inspect. They are never combined.
#
# Every number below is measured, not inferred, and each is reported even when
# it passes, so an officer can see what the verdict rested on. One measured
# number - saturation - is deliberately reported WITHOUT deciding anything;
# see the note above _DARK_REVIEW.
# ---------------------------------------------------------------------------

#: Below this shortest side, printed declarations are not resolvable at any
#: sharpness - a 320 px photo of a label cannot be read however crisp it is.
_MIN_SIDE_FAIL = 400
_MIN_SIDE_REVIEW = 800

#: Variance of the Laplacian: the classic focus measure. Low variance means
#: no high-frequency edge energy, i.e. no crisp character strokes.
_BLUR_FAIL = 20.0
_BLUR_REVIEW = 60.0

#: RMS contrast (standard deviation of intensity). A washed-out or badly
#: underexposed frame has strokes that no threshold can separate from paper.
_CONTRAST_FAIL = 12.0
_CONTRAST_REVIEW = 25.0

# GLARE IS MEASURED BUT DOES NOT DECIDE.
# The saturated-pixel fraction was measured across the photos/ corpus and does
# not separate specular glare from light packaging: real, perfectly readable
# packs score 0.07 to 0.80 saturated, while a deliberately blown-out test
# frame scores 0.68 - lower than an undamaged photo of a chilli packet on a
# white background. Any threshold over that number would fail readable packs
# and pass unreadable ones. The fraction is still reported in `metrics`, as
# evidence an officer can weigh, but it contributes nothing to the state:
# an unreliable signal that changes a verdict is worse than no signal.

#: Mean intensity floor. Under-exposure so severe the frame is mostly black.
_DARK_REVIEW = 40.0


def readability_analysis(image_path: Optional[str],
                         rule6: Optional[dict] = None) -> dict[str, Any]:
    loaded = _load(image_path)
    if loaded is None:
        return _check(READABILITY, NOT_ASSESSED,
                      "No declaration-panel image was available to this "
                      "inspection, so image readability could not be measured.")

    gray, orig_h, orig_w = loaded
    min_side = min(orig_h, orig_w)
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    contrast = float(gray.std())
    glare = float(np.count_nonzero(gray >= 250) / gray.size)
    mean = float(gray.mean())

    findings: list[dict[str, str]] = []
    states: list[str] = []

    if min_side < _MIN_SIDE_FAIL:
        states.append(FAIL)
        findings.append(_finding(FAIL,
            f"Image resolution is {orig_w}x{orig_h} px; its shortest side of "
            f"{min_side} px is below the {_MIN_SIDE_FAIL} px needed to resolve "
            "printed declarations at all."))
    elif min_side < _MIN_SIDE_REVIEW:
        states.append(REVIEW)
        findings.append(_finding(REVIEW,
            f"Image resolution is {orig_w}x{orig_h} px - adequate for large "
            "print but marginal for the smallest declarations."))
    else:
        states.append(PASS)

    if blur < _BLUR_FAIL:
        states.append(FAIL)
        findings.append(_finding(FAIL,
            f"Focus measure {blur:.1f} is below {_BLUR_FAIL:.0f}: the frame "
            "carries no sharp character edges and cannot be read."))
    elif blur < _BLUR_REVIEW:
        states.append(REVIEW)
        findings.append(_finding(REVIEW,
            f"Focus measure {blur:.1f} is soft (clear captures score above "
            f"{_BLUR_REVIEW:.0f}); small print may not be reliably readable."))
    else:
        states.append(PASS)

    if contrast < _CONTRAST_FAIL:
        states.append(FAIL)
        findings.append(_finding(FAIL,
            f"Contrast (intensity spread) is {contrast:.1f}, below "
            f"{_CONTRAST_FAIL:.0f} - text cannot be separated from background."))
    elif contrast < _CONTRAST_REVIEW:
        states.append(REVIEW)
        findings.append(_finding(REVIEW,
            f"Contrast (intensity spread) is {contrast:.1f}, which is low; "
            "faint or low-contrast declarations may be unreadable."))
    else:
        states.append(PASS)

    if mean < _DARK_REVIEW:
        states.append(REVIEW)
        findings.append(_finding(REVIEW,
            f"Mean brightness {mean:.0f}/255 - the frame is severely "
            "under-exposed."))

    # Corroboration from the extraction that already ran. This is not an OCR
    # confidence score and is not treated as one: it is the fact that a
    # readable-looking frame yielded nothing, which is itself a reason to
    # doubt the frame. It can only ever RAISE doubt, never clear it.
    if rule6 is not None:
        present = rule6.get("mandatory_present")
        total = rule6.get("mandatory_total")
        if present == 0 and total:
            states.append(REVIEW)
            findings.append(_finding(REVIEW,
                "No mandatory declaration was extracted from this image at all, "
                "which is consistent with the panel not being readable."))

    state = _worst(states)
    if state == PASS:
        explanation = ("Resolution, focus, contrast and exposure were measured "
                       "on this image and all are within readable limits. "
                       "Saturation is reported below but is not used to decide: "
                       "on real packs it cannot be told apart from light "
                       "packaging.")
    elif state == FAIL:
        explanation = ("This image is measurably unreadable for inspection "
                       "purposes; the declarations on it cannot be verified "
                       "from this capture.")
    else:
        explanation = ("Image quality is marginal. The declarations may be "
                       "readable, but not provably so - an officer must confirm "
                       "against the pack or a better photograph.")

    return _check(READABILITY, state, explanation, findings, {
        "width_px": orig_w,
        "height_px": orig_h,
        "min_side_px": min_side,
        "focus_variance": round(blur, 2),
        "rms_contrast": round(contrast, 2),
        "glare_fraction": round(glare, 4),
        "mean_intensity": round(mean, 1),
    })


# ---------------------------------------------------------------------------
# 2. PLACEMENT
#
# See the module docstring: this check never returns PASS, because nothing in
# this repository can establish legal placement conformity. What it CAN do
# deterministically is tell an officer that the evidence in front of them is
# not adequate to assess placement - a printed band running off the edge of
# the frame, or a frame with almost no printed text on it at all.
# ---------------------------------------------------------------------------

#: A text band must be at least this fraction of the frame width before its
#: touching an edge is reported. Narrow fragments touch a border in almost
#: every real photograph; a band a quarter of the frame wide running off the
#: edge is a cropped panel.
_CLIPPED_BAND_WIDTH = 0.25

#: How close to the border counts as touching it, as a fraction of the
#: shorter side. Phone cameras and JPEG edges are not pixel-exact.
_BORDER_TOLERANCE = 0.004

#: Under this share of the frame covered by text-shaped components, the photo
#: is not a declaration panel in any useful sense.
_MIN_TEXT_COVERAGE = 0.005


def _text_bands(gray) -> list[tuple[int, int, int, int]]:
    """
    Text-shaped horizontal bands, in work-scale pixels.

    Same idea as measure_chart.text_rows() - adaptive threshold, dilate
    horizontally to join characters into lines, keep wide-and-short
    components - but deliberately NOT the same function: text_rows() works in
    a marker-calibrated, rectified frame and its filters are expressed in
    millimetres. There is no marker and no millimetre here, so the filters
    are expressed as fractions of the frame instead. Reusing text_rows()
    would mean passing it a fabricated pixels-per-mm, which is exactly the
    kind of invented number this module exists to avoid.
    """
    h, w = gray.shape[:2]
    th = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY_INV, 31, 12)
    kernel_w = max(3, int(round(w * 0.02)))
    ker = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_w, 1))
    joined = cv2.dilate(th, ker, iterations=1)
    cnts, _ = cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    bands = []
    for c in cnts:
        x, y, bw, bh = cv2.boundingRect(c)
        if bh < 2 or bw < 0.02 * w:            # noise / single specks
            continue
        if bw / float(bh) < 2.0:               # not line-shaped
            continue
        if bh > 0.25 * h:                      # a whole dark region, not a line
            continue
        bands.append((x, y, bw, bh))
    return bands


def placement_analysis(image_path: Optional[str],
                       rule6: Optional[dict] = None) -> dict[str, Any]:
    """
    ALWAYS NOT_ASSESSED. No input to this function can produce any other
    state, and that is the point.

    Legal placement conformity needs a bounding box per declaration on a known
    panel geometry. The VLM returns values with no coordinates and this
    repository carries no panel-geometry rule source, so the honest answer is
    the same for every pack: this system cannot verify placement.

    It previously returned REVIEW whenever the capture heuristic fired, which
    put two different statements under one heading - "placement is
    questionable on this pack" and "this photograph may be cropped" - and left
    a row reading NEEDS REVIEW above an explanation saying the check was not
    assessed. The second statement is now capture_observation() below, where
    it cannot be mistaken for a finding about the pack.
    """
    return _check(PLACEMENT, NOT_ASSESSED,
                  "Automatic legal placement verification could not be "
                  "performed from the available image evidence. PRAMAAN's "
                  "extraction returns declaration values without "
                  "per-declaration coordinates, and no panel-geometry rule "
                  "source is available, so which panel a declaration sits on "
                  "and whether the declarations are grouped as Rules 6 and 9 "
                  "require cannot be established here - by this system, for "
                  "any pack. An officer must assess placement directly.",
                  metrics={"legal_placement_conformity": NOT_ASSESSED})


def capture_observation(image_path: Optional[str],
                        rule6: Optional[dict] = None) -> dict[str, Any]:
    """
    An ADVISORY observation about the photograph, never about the pack.

    WHY THIS DOES NOT AFFECT THE VERDICT
    -------------------------------------
    The wide-band-at-the-frame-edge test is a heuristic with no calibration
    behind it: measured across photos/, it fires on 4 of the 10 photographs,
    all of which are perfectly usable evidence. A signal with that
    false-positive rate is worth showing an officer - "check the whole panel
    is in shot" is useful advice - and is not worth letting turn a measured,
    fully-declared, Rule 7-passing pack into NEEDS_MANUAL_REVIEW. It is
    therefore in ADVISORY_CHECKS: its state is printed in full, and
    engine/verdict.py._apply_analysis() excludes it when combining.

    Advisory is not hidden. It keeps its own row, its own state and its own
    findings; what it loses is the power to change a verdict on evidence that
    is wrong four times in ten.
    """
    loaded = _load(image_path)
    if loaded is None:
        return _check(CAPTURE_OBSERVATION, NOT_ASSESSED,
                      "No declaration-panel image was available to this "
                      "inspection, so the capture itself could not be checked.")

    gray, orig_h, orig_w = loaded
    h, w = gray.shape[:2]
    bands = _text_bands(gray)

    tol = max(1, int(round(min(h, w) * _BORDER_TOLERANCE)))
    coverage = sum(bw * bh for _, _, bw, bh in bands) / float(h * w) if bands else 0.0

    clipped = []
    for x, y, bw, bh in bands:
        if bw < _CLIPPED_BAND_WIDTH * w:
            continue
        edges = []
        if x <= tol:
            edges.append("left")
        if x + bw >= w - tol:
            edges.append("right")
        if y <= tol:
            edges.append("top")
        if y + bh >= h - tol:
            edges.append("bottom")
        if edges:
            clipped.append(edges)

    findings: list[dict[str, str]] = []
    if clipped:
        sides = sorted({e for group in clipped for e in group})
        plural = "s" if len(clipped) != 1 else ""
        findings.append(_finding(REVIEW,
            f"{len(clipped)} wide printed band{plural} reach the "
            f"{', '.join(sides)} edge of the frame. The declaration panel may "
            "extend beyond the photograph. This is a capture heuristic, not a "
            "measurement of the pack. Re-capture with the whole package panel "
            "inside the frame before relying on placement verification."))
    if coverage < _MIN_TEXT_COVERAGE:
        findings.append(_finding(REVIEW,
            f"Only {coverage * 100:.2f}% of the frame carries text-shaped "
            "content, so this photograph may not show a declaration panel at "
            "all."))

    # Rule 6 has already recorded which mandatory fields are absent and that
    # record is untouched. This adds only the thing Rule 6 cannot say: the
    # panel may simply not have been in shot.
    if rule6 is not None and clipped:
        missing = [r.get("field") for r in (rule6.get("rows") or [])
                   if r.get("mandatory") and r.get("state") == "MISSING"]
        if missing:
            findings.append(_finding(REVIEW,
                f"{len(missing)} mandatory declaration(s) were not found and the "
                "panel may be cropped by the frame - the omission may be a "
                "capture fault rather than a labelling one. Rule 6's result is "
                "unchanged by this observation."))

    state = REVIEW if findings else PASS
    explanation = (
        "An observation about the photograph, not about the pack. It is "
        "advisory and does not change the compliance verdict."
        if findings else
        "The photograph shows a text-bearing panel that does not appear "
        "cropped by the frame. This is a fact about the capture only - not a "
        "placement verdict, and it does not change the compliance verdict.")

    return _check(CAPTURE_OBSERVATION, state, explanation, findings, {
        "text_bands_detected": len(bands),
        "text_coverage_fraction": round(coverage, 4),
        "bands_touching_frame_edge": len(clipped),
    })


# ---------------------------------------------------------------------------
# 3. DECLARATION VALIDATION
#
# Missing declarations are Rule 6's job and are not repeated here. This check
# reads the declarations that ARE present and asks whether each is structurally
# what it claims to be. Every rule below can be shown to a magistrate as
# arithmetic or as a format, never as a model's opinion - which is why there is
# no "misleading product claim" detection in this file. A claim like "natural"
# or "premium" is a judgement about marketing that no deterministic rule in
# this repository can prove, and inventing one would be a legal assertion with
# nothing behind it.
# ---------------------------------------------------------------------------

#: SI symbols and count words the Rules permit for a net-quantity declaration.
_STANDARD_UNITS = {
    "g", "kg", "mg", "ml", "l", "cl", "dl", "kl",
    "m", "cm", "mm", "km",
    "n", "u", "unit", "units", "number", "numbers", "piece", "pieces", "pcs",
}

#: Non-standard spellings seen on real packs, mapped to what the Rules require.
#: These are declarations that exist and are wrong in form, which is a
#: different finding from a declaration that is absent.
_NON_STANDARD_UNITS = {
    "gm": "g", "gms": "g", "gram": "g", "grams": "g", "grm": "g",
    "kgs": "kg", "kgm": "kg",
    "ltr": "l", "ltrs": "l", "litre": "l", "litres": "l", "liter": "l",
    "liters": "l", "lt": "l",
    "mls": "ml", "milliliter": "ml", "millilitre": "ml",
    "oz": "g or ml (imperial units are not permitted)",
    "lb": "kg (imperial units are not permitted)",
    "lbs": "kg (imperial units are not permitted)",
}

#: A per-unit price string wearing an MRP's clothes, or a date field holding
#: one. "115 Per g" in a use_by field is the seed.jpg bug in CLAUDE.md.
_PER_UNIT = re.compile(
    r"(per\s*\d*\s*(g|gm|gms|gram|grams|kg|ml|l|ltr|litre|piece|pc|unit)\b"
    r"|/\s*(g|gm|kg|ml|l|ltr|piece|pc|unit)\b"
    r"|\bper\s+(gram|kilo|litre|liter|unit|piece)\b)",
    re.I)

_CURRENCY = re.compile(r"(₹|\brs\.?\b|\binr\b)", re.I)

#: An FSSAI licence is a 14-digit number. Anything with letters or slashes in
#: it is a different licence entirely - CM/L is BIS, and CLAUDE.md records an
#: iron and a perfume both having a BIS number filed as FSSAI.
_FSSAI_DIGITS = 14
_OTHER_LICENCE = re.compile(r"\b(cm\s*/\s*l|bis|isi|is\s*\d{3,}|gc\s*/)", re.I)

_ABSENT = {None, "", "null", "none", "nil", "na", "n/a"}


def _present(value) -> Optional[str]:
    """The value as text if the label actually carries one, else None."""
    if value is None:
        return None
    s = str(value).strip()
    if s.lower() in _ABSENT:
        return None
    return s


def _number(value) -> Optional[float]:
    m = re.search(r"\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(m.group(0)) if m else None


def _looks_like_date(value: str) -> bool:
    """
    Deliberately permissive: any recognisable day/month/year, month name, or
    bare 4-digit year counts. The point is not to parse the date - Rule 6
    already tried that - but to establish whether the field holds something
    date-shaped at all, so that "115 Per g" in a use-by field can be called
    what it is without also condemning an unusual but genuine date format.
    """
    s = value.lower()
    if re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", s):
        return True
    if re.search(r"\b(19|20)\d{2}\b", s):
        return True
    if re.search(r"\b\d{1,2}\s*[/\-.]\s*\d{1,2}\s*[/\-.]\s*\d{2,4}\b", s):
        return True
    if re.search(r"\b\d{1,2}\s*[/\-]\s*\d{2,4}\b", s):
        return True
    return False


def declaration_validation(rule6: Optional[dict]) -> dict[str, Any]:
    if not rule6 or not isinstance(rule6.get("fields"), dict):
        return _check(DECLARATION_VALIDATION, NOT_ASSESSED,
                      "No extracted declaration set was available, so no "
                      "declaration could be validated.")

    fields = rule6["fields"]
    findings: list[dict[str, str]] = []
    checked = 0

    # --- MRP -------------------------------------------------------------
    mrp_raw = _present(fields.get("mrp_value"))
    if mrp_raw is not None and mrp_raw.upper() != "ILLEGIBLE":
        checked += 1
        mrp = _number(mrp_raw)
        if _PER_UNIT.search(mrp_raw):
            findings.append(_finding(FAIL,
                f"The maximum retail price is declared as {mrp_raw!r}, which is "
                "a per-unit rate, not a retail price for the pack. An MRP "
                "declaration must state the price of the package."))
        elif mrp is None:
            findings.append(_finding(FAIL,
                f"The maximum retail price is declared as {mrp_raw!r}, which "
                "carries no numeric price."))
        elif mrp <= 0:
            findings.append(_finding(FAIL,
                f"The maximum retail price is declared as {mrp:g}, which is not "
                "a valid price."))

    # --- net quantity ----------------------------------------------------
    qty_raw = _present(fields.get("net_quantity_value"))
    if qty_raw is not None and qty_raw.upper() != "ILLEGIBLE":
        checked += 1
        qty = _number(qty_raw)
        if qty is None:
            findings.append(_finding(FAIL,
                f"The net quantity is declared as {qty_raw!r}, which is not a "
                "number."))
        elif qty <= 0:
            findings.append(_finding(FAIL,
                f"The net quantity is declared as {qty:g}, which is not a valid "
                "quantity."))

    unit_raw = _present(fields.get("net_quantity_unit"))
    if unit_raw is not None and unit_raw.upper() != "ILLEGIBLE":
        checked += 1
        unit = re.sub(r"[^a-z]", "", unit_raw.lower())
        if unit in _NON_STANDARD_UNITS:
            findings.append(_finding(FAIL,
                f"The net quantity unit is printed as {unit_raw!r}. The Rules "
                f"require the standard symbol {_NON_STANDARD_UNITS[unit]}; "
                f"{unit_raw!r} is a non-standard abbreviation."))
        elif unit and unit not in _STANDARD_UNITS:
            findings.append(_finding(REVIEW,
                f"The net quantity unit {unit_raw!r} is not one this system "
                "recognises as a standard unit or count word. It may be valid "
                "for this commodity - an officer must confirm."))

    # --- dates -----------------------------------------------------------
    for key, label in (("mfg_date", "manufacture/packing date"),
                       ("use_by", "use-by / best-before date")):
        raw = _present(fields.get(key))
        if raw is None or raw.upper() == "ILLEGIBLE":
            continue
        checked += 1
        if _looks_like_date(raw):
            continue
        if _PER_UNIT.search(raw) or _CURRENCY.search(raw):
            findings.append(_finding(FAIL,
                f"The {label} field holds {raw!r}, which is a price, not a date. "
                "The declaration recorded for this field is invalid and the "
                "date cross-check could not be performed on it."))
        else:
            findings.append(_finding(REVIEW,
                f"The {label} is declared as {raw!r}, which could not be read as "
                "a date. The date cross-check did not run against it - this is "
                "not a pass."))

    # --- FSSAI licence ---------------------------------------------------
    #
    # NEVER FAIL, ON PURPOSE. What is deterministic here is only that the
    # value in this field is not an FSSAI licence number - a 14-digit format
    # check proves that much and no more. It does NOT prove the pack breaks
    # any rule, for two reasons this system cannot resolve:
    #
    #   1. There is no product-category gate (CLAUDE.md known bug 2). An
    #      iron, a perfume and a sunscreen have no FSSAI number to print, so
    #      the field being wrong is not an omission by the packer.
    #   2. The value almost always turns out to be a DIFFERENT authority's
    #      licence that the reader filed under this field - BIS "CM/L ...",
    #      a cosmetics licence "HIM/COS/L/16/243", a drug licence. That is an
    #      extraction fault, not a labelling offence.
    #
    # Calling either of those NON-COMPLIANT would be accusing a pack of a
    # violation on evidence that only says "this software put the wrong
    # string in this box". REVIEW is the honest ceiling.
    fssai_raw = _present(fields.get("fssai_licence"))
    if fssai_raw is not None and fssai_raw.upper() != "ILLEGIBLE":
        checked += 1
        compact = re.sub(r"\s", "", fssai_raw)
        digits = re.sub(r"\D", "", fssai_raw)
        if _OTHER_LICENCE.search(fssai_raw):
            findings.append(_finding(REVIEW,
                f"{fssai_raw!r} was captured as an FSSAI licence but is "
                "formatted as another authority's licence number (BIS/ISI "
                f"style). An FSSAI licence is exactly {_FSSAI_DIGITS} digits, "
                "so this field has been mis-identified. Confirm whether this "
                "commodity requires an FSSAI licence at all before treating "
                "it as a missing or invalid declaration."))
        elif not compact.isdigit() or len(compact) != _FSSAI_DIGITS:
            findings.append(_finding(REVIEW,
                f"{fssai_raw!r} was captured as an FSSAI licence, but an FSSAI "
                f"licence is exactly {_FSSAI_DIGITS} digits and this value has "
                f"{len(digits)} digit(s)"
                + (" and non-digit characters. " if not compact.isdigit() else ". ")
                + "It is most likely a different licence (cosmetics, drug or "
                  "BIS) read into this field. This is not evidence that the "
                  "pack is non-compliant - an officer must confirm which "
                  "licence, if any, this commodity must carry."))

    # --- consumer care ---------------------------------------------------
    care_raw = _present(fields.get("consumer_care"))
    if care_raw is not None and care_raw.upper() != "ILLEGIBLE":
        checked += 1
        if "@" not in care_raw and not re.search(r"\d", care_raw):
            findings.append(_finding(REVIEW,
                f"The consumer-care declaration {care_raw!r} carries neither a "
                "telephone number nor an email address, so it may not be a "
                "usable contact."))

    # --- contradiction: quantity absent but implied by the other two -----
    # Not a Rule 6 change: Rule 6 has already recorded net quantity as missing
    # and that record is untouched. This only says the pack's own arithmetic
    # contradicts the omission, which is a reason for an officer to look again.
    if qty_raw is None:
        mrp_n = _number(mrp_raw) if mrp_raw else None
        usp_raw = _present(fields.get("unit_sale_price"))
        usp_n = _number(usp_raw) if usp_raw else None
        if mrp_n and usp_n and usp_n > 0:
            derived = mrp_n / usp_n
            if 0 < derived < 1e6:
                findings.append(_finding(REVIEW,
                    f"Net quantity was not extracted, but the declared MRP "
                    f"{mrp_n:g} and unit price {usp_n:g} imply approximately "
                    f"{derived:.0f} {unit_raw or 'unit(s)'}. Either the quantity "
                    "is printed and was missed, or one of the two prices is "
                    "misread - this is a contradiction, not an omission."))

    state = _worst([f["severity"] for f in findings]) if findings else (
        PASS if checked else NOT_ASSESSED)

    if state == NOT_ASSESSED:
        explanation = ("No declaration carried a readable value, so there was "
                       "nothing whose structure could be validated.")
    elif state == PASS:
        explanation = (f"{checked} extracted declaration(s) were checked for "
                       "structural validity (price form, quantity and unit, "
                       "date shape, licence format, contact form) and all are "
                       "well formed.")
    elif state == FAIL:
        explanation = ("At least one declaration present on the label is "
                       "provably not in a valid or standard form. Each finding "
                       "below is a format or arithmetic fact, not an inference.")
    else:
        explanation = ("At least one declaration is unusual in a way this system "
                       "cannot prove is wrong. An officer must decide.")

    return _check(DECLARATION_VALIDATION, state, explanation, findings,
                  {"declarations_validated": checked})


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------

def run_analysis(rule6: Optional[dict],
                 image_path: Optional[str] = None,
                 prior: Optional[dict] = None) -> dict[str, Any]:
    """
    All three checks, as one stored/serialisable result.

    `prior` is the analysis already stored against this inspection, if any.
    PRAMAAN's Rule 7 step is a SECOND POST /inspect for a pack whose Rule 6
    result the client already holds, and that call carries no declaration
    photograph. Re-running the image checks then would replace a real
    readability measurement with NOT_ASSESSED - the record would get worse
    because more work was done on it. So when this call has no image and the
    prior result has a real image-based answer, the prior answer is carried
    forward unchanged rather than recomputed or discarded.

    Declaration validation is always recomputed: it needs no image, and the
    Rule 6 result passed in is the authoritative one for this call.
    """
    checks = {
        PLACEMENT: placement_analysis(image_path, rule6),
        CAPTURE_OBSERVATION: capture_observation(image_path, rule6),
        READABILITY: readability_analysis(image_path, rule6),
        DECLARATION_VALIDATION: declaration_validation(rule6),
    }

    if image_path is None and prior:
        for stored in prior.get("checks", []):
            name = stored.get("check")
            if name in (CAPTURE_OBSERVATION, READABILITY) and stored.get("metrics"):
                checks[name] = stored

    ordered = [checks[PLACEMENT], checks[CAPTURE_OBSERVATION],
               checks[READABILITY], checks[DECLARATION_VALIDATION]]

    # overall_state summarises only the checks that can move a verdict. The
    # advisory row is excluded for the same reason verdict.py ignores it: a
    # heuristic that fires on 4 of 10 good photographs must not be the thing
    # that makes a whole analysis read as "needs review".
    return {
        "version": SCHEMA_VERSION,
        "overall_state": _worst([c["state"] for c in ordered
                                 if not c.get("advisory")]),
        "checks": ordered,
    }


def analysis_findings(analysis: Optional[dict]) -> list[str]:
    """
    The analysis rendered as findings lines, in the same voice as the Rule 6
    and Rule 7 findings engine/verdict.py already produces, so one findings
    list reads as one document. Every check contributes a line INCLUDING the
    ones that could not run: a check that says nothing reads as a check that
    passed.
    """
    if not analysis:
        return []
    lines = []
    for check in analysis.get("checks", []):
        title = check.get("title") or check.get("check", "Check")
        state = check.get("state", NOT_ASSESSED)
        # An advisory line says so on its face. A findings list is read on its
        # own in a report, away from any UI that could carry the distinction.
        suffix = " (advisory - does not affect the verdict)" if check.get("advisory") else ""
        lines.append(f"{title}: {state}{suffix}")
        for f in check.get("findings", []):
            lines.append(f"{title} [{f.get('severity', REVIEW)}]: "
                         f"{f.get('message', '')}")
    return lines


# ---------------------------------------------------------------------------
# Self-test — deterministic, no photo and no model needed
# ---------------------------------------------------------------------------

def _r6(**fields) -> dict:
    return {"fields": fields, "rows": [], "problems": [],
            "mandatory_present": 6, "mandatory_total": 6}


_SELF_TEST = [
    ("valid declarations -> PASS",
     _r6(mrp_value="1299.00", net_quantity_value="320", net_quantity_unit="g",
         mfg_date="03/2026", use_by="03/2027",
         fssai_licence="10012345678901", consumer_care="1800-123-456"),
     PASS),
    ("seed.jpg bug: use_by holds a per-gram price -> FAIL",
     _r6(mrp_value="299", net_quantity_value="200", net_quantity_unit="g",
         mfg_date="01/2026", use_by="115 Per g"),
     FAIL),
    ("iron.jpg bug: BIS licence filed as FSSAI -> REVIEW, never FAIL",
     _r6(mrp_value="1500", net_quantity_value="1", net_quantity_unit="N",
         fssai_licence="CM/L 0009878017"),
     REVIEW),
    ("perfume.jpg bug: GC/2049 filed as FSSAI -> REVIEW, never FAIL",
     _r6(mrp_value="899", fssai_licence="GC/2049"),
     REVIEW),
    ("sunscreen: a cosmetics licence filed as FSSAI -> REVIEW, never FAIL",
     _r6(mrp_value="499", net_quantity_value="50", net_quantity_unit="ml",
         fssai_licence="HIM/COS/L/16/243"),
     REVIEW),
    ("a genuine 14-digit FSSAI licence -> PASS",
     _r6(mrp_value="499", fssai_licence="10012345678901"),
     PASS),
    ("creatin.jpg bug: quantity absent but implied by MRP/unit price -> REVIEW",
     _r6(mrp_value="1299.00", unit_sale_price="4.06"),
     REVIEW),
    ("non-standard unit 'gms' -> FAIL",
     _r6(mrp_value="99", net_quantity_value="500", net_quantity_unit="gms"),
     FAIL),
    ("MRP declared as a per-unit rate -> FAIL",
     _r6(mrp_value="4.06 per g", net_quantity_value="320",
         net_quantity_unit="g"),
     FAIL),
    ("unrecognised unit -> REVIEW, not FAIL",
     _r6(mrp_value="99", net_quantity_value="12", net_quantity_unit="dozen"),
     REVIEW),
    ("date in an unusual but real format -> not condemned",
     _r6(mrp_value="99", mfg_date="MAR 2026", use_by="MAR 2027"),
     PASS),
    ("nothing readable -> NOT_ASSESSED, never PASS",
     _r6(), NOT_ASSESSED),
    ("no extraction at all -> NOT_ASSESSED",
     None, NOT_ASSESSED),
]


def self_test() -> bool:
    print("ANALYSIS SELF-TEST (no photo, no model needed)\n")
    ok = 0
    for name, rule6, expected in _SELF_TEST:
        result = declaration_validation(rule6)
        good = result["state"] == expected
        ok += good
        print(f"  {'ok  ' if good else 'FAIL'} {name}")
        print(f"        -> {result['state']} (want {expected})")
        if not good:
            for f in result["findings"]:
                print(f"           [{f['severity']}] {f['message']}")

    # Image checks must degrade to NOT_ASSESSED, never to PASS, with no image.
    for fn, label in ((placement_analysis, "placement"),
                      (capture_observation, "capture observation"),
                      (readability_analysis, "readability")):
        r = fn(None, None)
        good = r["state"] == NOT_ASSESSED
        ok += good
        print(f"  {'ok  ' if good else 'FAIL'} {label} with no image -> "
              f"{r['state']} (want {NOT_ASSESSED})")

    # Placement produces exactly one state whatever it is handed.
    fixed = placement_analysis("photos/oats.jpg")["state"] == NOT_ASSESSED
    ok += fixed
    print(f"  {'ok  ' if fixed else 'FAIL'} placement on a real photo is still "
          f"{NOT_ASSESSED} (it can never be anything else)")

    # The advisory row must not be able to move the analysis-level state.
    # oats.jpg is a photo whose capture heuristic DOES fire, so this is the
    # exact case that used to read NEEDS REVIEW.
    result = run_analysis(_r6(mrp_value="99"), "photos/oats.jpg")
    cap = next(c for c in result["checks"] if c["check"] == CAPTURE_OBSERVATION)
    isolated = (cap.get("advisory") is True
                and cap["state"] == REVIEW
                and result["overall_state"] != REVIEW)
    ok += isolated
    print(f"  {'ok  ' if isolated else 'FAIL'} a firing capture observation is "
          f"advisory and stays out of overall_state (capture={cap['state']}, "
          f"overall={result['overall_state']})")

    total = len(_SELF_TEST) + 5
    print(f"\n{ok}/{total} correct")
    return ok == total


if __name__ == "__main__":
    import sys
    sys.exit(0 if self_test() else 1)

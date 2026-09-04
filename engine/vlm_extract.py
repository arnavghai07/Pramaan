"""
vlm_extract.py — PRAMAAN

Vision-language extraction for Indian packaged commodity declaration panels.
The VLM extracts fields; deterministic validation produces the review verdict.

Usage:
    python vlm_extract.py label.jpg --backend ollama
    python vlm_extract.py label.jpg --backend gemini
    python vlm_extract.py --selftest
"""

import argparse
import base64
import json
import os
import re
import sys


# ---------------------------------------------------------------------------
# 1. SCHEMA + PROMPT
# ---------------------------------------------------------------------------

SCHEMA = {
    "manufacturer": "name of manufacturer, packer or importer, or null",
    "address": "full address including PIN code, or null",
    "net_quantity_value": "number only, e.g. 200, or null",
    "net_quantity_unit": "unit only, e.g. g, kg, ml, Units, Numbers, or null",
    "mrp_value": "the MAXIMUM RETAIL PRICE as a number only, or null",
    "unit_sale_price": "the per-unit / per-gram price if printed, or null",
    "mfg_date": "month and year of manufacture or packing, as printed, or null",
    "use_by": "use-by, best-before or expiry, as printed, or null",
    "consumer_care": "phone, email or care address, or null",
    "fssai_licence": "FSSAI licence number, or null",
    "country_origin": "country of origin, or null",
}

PROMPT = """You are reading the declaration panel of an Indian packaged commodity,
for compliance checking under the Legal Metrology (Packaged Commodities) Rules, 2011.

Extract ONLY what is printed on this label.
Return a single JSON object, no markdown, no commentary.

Rules you must follow:
- If a field is not printed on the label, return null. Do NOT guess.
- If a label is printed but its value is blank or unreadable, return the string
  "ILLEGIBLE" for that field. This is different from null.
- MRP and unit sale price are DIFFERENT fields. The MRP is the retail price of
  the whole pack. The unit sale price is per gram / per ml / per piece and is
  usually the smaller number. Never put the unit sale price in mrp_value.
- Manufacture date and use-by date are DIFFERENT fields. Do not swap them.
- Copy values exactly as printed. Do not reformat dates or add currency symbols.

Fields:
%s
""" % json.dumps(SCHEMA, indent=2)


# ---------------------------------------------------------------------------
# 2. BACKENDS
# ---------------------------------------------------------------------------

def encode_image(path, max_side=1000):
    """Encode image as base64 after optionally downscaling it."""
    try:
        import cv2

        im = cv2.imread(path)
        if im is None:
            raise ValueError("Cannot read image")

        h, w = im.shape[:2]

        if max(h, w) > max_side:
            scale = max_side / max(h, w)
            im = cv2.resize(
                im,
                (int(w * scale), int(h * scale)),
                interpolation=cv2.INTER_AREA,
            )

        ok, buf = cv2.imencode(
            ".jpg",
            im,
            [cv2.IMWRITE_JPEG_QUALITY, 92],
        )

        if ok:
            return base64.b64encode(buf.tobytes()).decode()

    except Exception:
        pass

    with open(path, "rb") as fh:
        return base64.b64encode(fh.read()).decode()


class BackendError(Exception):
    """Vision backend could not be reached or refused the request."""


class ExtractionFailed(Exception):
    """No image orientation/resolution produced usable JSON."""

    def __init__(self, tried):
        self.tried = tried
        super().__init__("no orientation produced usable JSON")


def ollama_models(host=None):
    """Return installed Ollama models."""
    import urllib.request

    host = host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")

    try:
        with urllib.request.urlopen(host + "/api/tags", timeout=10) as r:
            return [
                m["name"]
                for m in json.loads(r.read()).get("models", [])
            ]
    except Exception:
        return None


def call_ollama(
    image_path,
    model=None,
    host=None,
    force_json=True,
    debug=False,
    max_side=1000,
):
    """Ask Ollama for one extraction."""
    import urllib.request
    import urllib.error

    model = model or os.environ.get("VLM_MODEL", "qwen2.5vl:7b")
    host = host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")

    b64 = encode_image(image_path, max_side=max_side)

    payload = {
        "model": model,
        "prompt": PROMPT,
        "images": [b64],
        "stream": False,
        "keep_alive": "30m",
        "options": {
            "temperature": 0,
            "num_predict": 300,
            "num_ctx": 8192,
        },
    }

    if force_json:
        payload["format"] = "json"

    req = urllib.request.Request(
        host + "/api/generate",
        json.dumps(payload).encode(),
        {"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            out = json.loads(r.read())

        raw = out.get("response", "")

        if debug:
            print(
                f"    [debug] {len(raw)} chars, "
                f"done_reason={out.get('done_reason')}"
            )
            if raw:
                print(f"    [debug] {raw[:300]}")

        return raw

    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]

        try:
            detail = json.loads(detail).get("error", detail)
        except Exception:
            pass

        msg = (
            f"\nOllama refused the request (HTTP {e.code}):\n"
            f"  {detail}\n"
        )

        have = ollama_models(host)

        if have is not None:
            msg += "\nInstalled models:" if have else "\nNo models installed."
            for m in have:
                msg += f"\n    {m}"
            msg += (
                "\n\nRecommended:"
                "\n    ollama pull qwen2.5vl:7b"
            )

        raise BackendError(msg) from e

    except urllib.error.URLError as e:
        raise BackendError(
            f"\nCannot reach Ollama at {host}: {e.reason}\n"
            "Open the Ollama app and retry."
        ) from e


def call_gemini(image_path, model="gemini-2.0-flash"):
    """Cloud fallback backend."""
    import urllib.request

    key = os.environ.get("GEMINI_API_KEY")

    if not key:
        raise BackendError(
            "Set GEMINI_API_KEY, or use --backend ollama"
        )

    b64 = encode_image(image_path)
    mime = "image/jpeg"

    body = json.dumps({
        "contents": [{
            "parts": [
                {"text": PROMPT},
                {
                    "inline_data": {
                        "mime_type": mime,
                        "data": b64,
                    }
                },
            ]
        }],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0,
        },
    }).encode()

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={key}"
    )

    req = urllib.request.Request(
        url,
        body,
        {"Content-Type": "application/json"},
    )

    with urllib.request.urlopen(req, timeout=180) as r:
        return (
            json.loads(r.read())["candidates"][0]
            ["content"]["parts"][0]["text"]
        )


# ---------------------------------------------------------------------------
# 3. JSON PARSING + ORIENTATIONS
# ---------------------------------------------------------------------------

def parse_json(raw):
    """Recover JSON even when the model wraps it in prose/fences."""
    if raw is None or not str(raw).strip():
        raise ValueError("the model returned an EMPTY response")

    raw = str(raw).strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I).strip()

    try:
        return json.loads(raw)

    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.S)

        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        raise ValueError(
            "the model did not return JSON. It said:\n"
            f"  {raw[:300]}"
        )


def rotations(path):
    """
    Yield (label, path) for 0/90/180/270 degree orientations.
    """
    import cv2
    import tempfile
    import os as _os

    im = cv2.imread(path)

    if im is None:
        yield ("as-is", path)
        return

    codes = [
        ("0", None),
        ("90", cv2.ROTATE_90_CLOCKWISE),
        ("180", cv2.ROTATE_180),
        ("270", cv2.ROTATE_90_COUNTERCLOCKWISE),
    ]

    for name, code in codes:
        out = im if code is None else cv2.rotate(im, code)

        if code is None:
            yield (name + " deg", path)
            continue

        fd, tmp = tempfile.mkstemp(suffix=".jpg")
        _os.close(fd)
        cv2.imwrite(tmp, out)

        try:
            yield (name + " deg", tmp)
        finally:
            try:
                _os.remove(tmp)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# 4. EXTRACTION STRATEGY
# ---------------------------------------------------------------------------

MANDATORY = [
    "manufacturer",
    "address",
    "net_quantity_value",
    "mrp_value",
    "mfg_date",
    "consumer_care",
]


def score_result(d):
    """Count mandatory fields successfully extracted."""
    return sum(
        1
        for k in MANDATORY
        if str(d.get(k)).strip().upper()
        not in ("NONE", "NULL", "", "ILLEGIBLE")
    )


def extract_fields(
    image_path,
    backend="ollama",
    model=None,
    debug=False,
    all_rotations=False,
    max_side=1000,
):
    """
    Adaptive extraction strategy:

    1. Try the requested/default resolution (1000px).
    2. If the result is weak (<5 mandatory fields), retry at 1200px.
    3. Try rotations only when necessary.
    4. Keep the richest result.
    """

    def call_backend(path, size):
        if backend != "ollama":
            return call_gemini(
                path,
                model or "gemini-2.0-flash",
            )

        raw = call_ollama(
            path,
            model=model,
            force_json=True,
            debug=debug,
            max_side=size,
        )

        if not str(raw).strip():
            if debug:
                print(
                    "    [debug] empty with format=json, "
                    "retrying unconstrained"
                )

            raw = call_ollama(
                path,
                model=model,
                force_json=False,
                debug=debug,
                max_side=size,
            )

        return raw

    best = None
    best_score = -1
    best_rot = None
    tried = []

    sizes = [max_side]

    # Adaptive fallback:
    # 1200px improves small declaration text.
    if backend == "ollama" and max_side < 1200:
        sizes.append(1200)

    for label, path in rotations(image_path):

        for size in sizes:
            try:
                d = parse_json(call_backend(path, size))

            except ValueError as e:
                tried.append(f"{label} @ {size}px: {e}")
                continue

            sc = score_result(d)

            tried.append(
                f"{label} @ {size}px: "
                f"{sc}/{len(MANDATORY)} mandatory fields"
            )

            if debug:
                print(
                    f"    [debug] {label} @ {size}px "
                    f"score={sc}/{len(MANDATORY)}"
                )

            if sc > best_score:
                best = d
                best_score = sc
                best_rot = label

            # Perfect extraction: stop.
            if sc == len(MANDATORY):
                return best, best_rot, tried

            # Strong result: stop current resolution attempts.
            if (
                size == max_side
                and sc >= 5
                and not all_rotations
            ):
                break

        # Don't rotate if current result is already strong.
        if not all_rotations and best_score >= 5:
            break

    if best is None:
        raise ExtractionFailed(tried)

    return best, best_rot, tried


# ---------------------------------------------------------------------------
# 5. VALIDATION
# ---------------------------------------------------------------------------

MONTHS = (
    "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
)


def _num(v):
    if v is None:
        return None

    m = re.search(
        r"\d+(?:\.\d+)?",
        str(v).replace(",", ""),
    )

    return float(m.group(0)) if m else None


def _date(v):
    """
    Parse common label date formats.

    Returns (year, month) or None.
    """
    if not v:
        return None

    s = str(v).strip()

    # DD/MM/YYYY or DD-MM-YY
    m = re.search(
        r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\b",
        s,
    )

    if m:
        _, mo, y = (int(g) for g in m.groups())
        return (
            y if y > 99 else 2000 + y,
            mo,
        )

    # JUL.2026 / 26JUL2026 / 11/FEB/2026
    m = re.search(
        rf"\b(?:\d{{1,2}}[\s/\-.]*)?"
        rf"({MONTHS})[a-z]*"
        rf"[\s/\-.]*(\d{{2,4}})\b",
        s,
        re.I,
    )

    if m:
        month_name = m.group(1).lower()[:3]
        month_map = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4,
            "may": 5, "jun": 6, "jul": 7, "aug": 8,
            "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        }

        y = int(m.group(2))

        return (
            y if y > 99 else 2000 + y,
            month_map[month_name],
        )

    # MM/YYYY or MM/YY
    m = re.search(
        r"\b(0?[1-9]|1[0-2])[/\-](\d{2}|\d{4})\b",
        s,
    )

    if m:
        mo = int(m.group(1))
        y = int(m.group(2))

        return (
            y if y > 99 else 2000 + y,
            mo,
        )

    return None


def validate(d):
    """
    Cross-check extracted values.

    The model is never the legal decision-maker.
    """

    problems = []

    mrp = _num(d.get("mrp_value"))
    usp = _num(d.get("unit_sale_price"))
    qty = _num(d.get("net_quantity_value"))

    # CHECK 1 — MRP/USP swap.
    if (
        mrp is not None
        and usp is not None
        and qty
        and qty > 1
        and mrp <= usp
    ):
        problems.append(
            f"MRP {mrp} is not greater than unit price {usp} "
            f"for a {qty}-unit pack - the two are probably swapped"
        )

    # CHECK 2 — arithmetic consistency.
    if mrp and usp and qty:
        expected = mrp / qty

        if abs(expected - usp) > max(0.05, 0.1 * usp):
            problems.append(
                f"MRP/quantity = {expected:.2f} but the label prints "
                f"unit price {usp:.2f} - one of the three is misread"
            )

    # CHECK 3 — manufacture cannot be after expiry/use-by.
    a = _date(d.get("mfg_date"))
    b = _date(d.get("use_by"))

    if a and b and a > b:
        problems.append(
            f"manufacture {d['mfg_date']} is after use-by "
            f"{d['use_by']} - dates are probably swapped"
        )

    # CHECK 4 — preserve unreadable values.
    for k, v in d.items():
        if str(v).strip().upper() == "ILLEGIBLE":
            problems.append(
                f"{k} printed but unreadable - officer must confirm"
            )

    rows = []
    for k in SCHEMA:
        v = d.get(k)
        state = ("MISSING" if v in (None, "", "null")
                 else "REVIEW" if str(v).upper() == "ILLEGIBLE" else "PRESENT")
        rows.append((k, state, v, k in MANDATORY))

# CHECK 5 — mandatory declarations missing
    for k in MANDATORY:
        v = d.get(k)
        if v in (None, "", "null"):
            problems.append(f"{k} mandatory declaration is missing")

    return rows, problems


# ---------------------------------------------------------------------------
# 6. IMPORTABLE END-TO-END API
# ---------------------------------------------------------------------------

def extract(
    image_path,
    backend="ollama",
    model=None,
    debug=False,
    all_rotations=False,
    max_side=1000,
):
    """Run extraction and deterministic validation end to end."""

    fields, best_rot, tried = extract_fields(
        image_path,
        backend=backend,
        model=model,
        debug=debug,
        all_rotations=all_rotations,
        max_side=max_side,
    )

    rows, problems = validate(fields)

    row_dicts = [
        {
            "field": k,
            "state": state,
            "value": v,
            "mandatory": mandatory,
        }
        for k, state, v, mandatory in rows
    ]

    mandatory_present = sum(
        1
        for r in row_dicts
        if r["mandatory"] and r["state"] == "PRESENT"
    )

    return {
        "fields": fields,
        "rows": row_dicts,
        "problems": problems,
        "mandatory_present": mandatory_present,
        "mandatory_total": len(MANDATORY),
        "best_rotation": best_rot,
        "orientations_tried": tried,
    }


# ---------------------------------------------------------------------------
# 7. SELF TEST
# ---------------------------------------------------------------------------

SELFTEST = [
    (
        "walnut, MRP/USP swapped",
        {
            "manufacturer": "VMG FOODS PVT. LTD.",
            "address": "Khari Baoli Delhi-110006",
            "net_quantity_value": "250",
            "net_quantity_unit": "g",
            "mrp_value": "1.60",
            "unit_sale_price": "400.00",
            "mfg_date": "JUL.2026",
            "use_by": "JAN.2027",
            "consumer_care": "info@vinoddryfruits.com",
        },
        1,
    ),
    (
        "iron, counted in Units, clean",
        {
            "manufacturer": "VERSUNI INDIA HOME SOLUTIONS LTD.",
            "address": "Kolkata, West Bengal - 700016",
            "net_quantity_value": "1",
            "net_quantity_unit": "Unit",
            "mrp_value": "825.00",
            "unit_sale_price": "825.00",
            "mfg_date": "05/2025",
            "use_by": None,
            "consumer_care": "1800 572 1800",
        },
        0,
    ),
    (
        "perfume, dates swapped",
        {
            "manufacturer": "OG Beauty Private Limited",
            "address": "Ahmedabad - 380054",
            "net_quantity_value": "100",
            "net_quantity_unit": "ml",
            "mrp_value": "750.00",
            "unit_sale_price": "7.50",
            "mfg_date": "12/2028",
            "use_by": "12/2025",
            "consumer_care": "hi@ogbeauty.in",
        },
        1,
    ),
    (
        "chanachur, MRP unreadable",
        {
            "manufacturer": "GIRISH CHANACHUR & SNACKS PVT. LTD.",
            "address": "Jharkhand-831001",
            "net_quantity_value": "200",
            "net_quantity_unit": "g",
            "mrp_value": "ILLEGIBLE",
            "unit_sale_price": "0.22",
            "mfg_date": "06/07/2026",
            "use_by": "05/11/2026",
            "consumer_care": "0657 2426571",
        },
        1,
    ),
]


def selftest():
    ok = 0

    for name, data, want_problems in SELFTEST:
        _, problems = validate(data)

        good = (
            (len(problems) > 0)
            == (want_problems > 0)
        )

        ok += good

        print(
            f"  {'ok  ' if good else 'FAIL'} {name}"
        )

        for p in problems:
            print(f"         -> {p}")

    print(
        f"\n{ok}/{len(SELFTEST)} validation cases correct"
    )

    return ok == len(SELFTEST)


# ---------------------------------------------------------------------------
# 8. CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()

    ap.add_argument("image", nargs="?")

    ap.add_argument(
        "--backend",
        choices=["ollama", "gemini"],
        default="ollama",
    )

    ap.add_argument(
        "--model",
        default=None,
    )

    ap.add_argument(
        "--selftest",
        action="store_true",
    )

    ap.add_argument(
        "--debug",
        action="store_true",
        help="show raw model reply and diagnostic information",
    )

    ap.add_argument(
        "--all-rotations",
        action="store_true",
        help="try all four orientations even after a good result",
    )

    ap.add_argument(
        "--max-side",
        type=int,
        default=1000,
        help="maximum image dimension in pixels (default: 1000)",
    )

    ap.add_argument(
        "--list",
        action="store_true",
        help="show installed Ollama models",
    )

    a = ap.parse_args()

    if a.list:
        have = ollama_models()

        if have is None:
            sys.exit(
                "Cannot reach Ollama. Open the Ollama app and retry."
            )

        print(
            "Installed models:" if have else "No models installed."
        )

        for m in have:
            print("   ", m)

        sys.exit(0)

    if a.selftest or not a.image:
        sys.exit(0 if selftest() else 1)

    try:
        result = extract(
            a.image,
            backend=a.backend,
            model=a.model,
            debug=a.debug,
            all_rotations=a.all_rotations,
            max_side=a.max_side,
        )

    except BackendError as e:
        print(str(e))
        sys.exit(1)

    except ExtractionFailed as e:
        print("orientations/resolutions tried:")

        for t in e.tried:
            print("   " + t)

        print("\nNo orientation produced usable JSON.")
        print("\nSuggestions:")
        print("  1. Crop to JUST the declaration block and rerun.")
        print("  2. Use qwen2.5vl:7b locally:")
        print("       ollama pull qwen2.5vl:7b")
        print("  3. Try --max-side 1200 for difficult small text.")
        print("  4. Use Gemini cloud backend if permitted.")

        sys.exit(1)

    print("orientations/resolutions tried:")

    for t in result["orientations_tried"]:
        print("   " + t)

    print()

    if result["best_rotation"] != "0 deg":
        print(
            "NOTE: best result came from rotating the image "
            f"{result['best_rotation']}.\n"
        )

    print(f"{'':<2}{'declaration':<22} {'state':<9} value")
    print("-" * 75)

    for r in result["rows"]:
        mark = "* " if r["mandatory"] else "  "

        print(
            f"{mark}{r['field']:<22} "
            f"{r['state']:<9} "
            f"{str(r['value'])[:40]}"
        )

    print("  (* = mandatory under Rule 6)")
    print()

    if result["problems"]:
        print(
            "VALIDATION PROBLEMS - "
            "do not trust these fields without review"
        )
        print("-" * 75)

        for p in result["problems"]:
            print("  " + p)

    else:
        print(
            "Validation clean: all cross-checks passed."
        )

    print()

    print(
        f"VERDICT: {result['mandatory_present']} of "
        f"{result['mandatory_total']} mandatory declarations present"
        + (
            f", {len(result['problems'])} problem(s) flagged"
            if result["problems"]
            else ""
        )
    )


if __name__ == "__main__":
    main()
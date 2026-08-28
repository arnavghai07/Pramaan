"""
vlm_extract.py  —  PRAMAAN
===========================
Replaces the regex classifier with a vision-language model for EXTRACTION,
while keeping deterministic rules for the VERDICT.

WHY THE REGEX APPROACH HIT A WALL
---------------------------------
Ten real packs used ten different layouts: vertical label columns, horizontal
header rows, sideways print, values as batch overprints drifting out of
alignment, "Net Vol." / "Net Wt." / "Net Quantity", counts in Units and Numbers,
dates as 11/FEB/2026 and 26JUL2026 and 04/26. Each fix taught the rules exactly
one more layout. There are millions of layouts. That does not converge.

A vision-language model reads the label the way a person does — it sees that
"400 .00" sits under the "MRP (Rs)" heading regardless of whether that heading
is above, beside, or rotated. Layout generalisation is the thing VLMs are
actually good at.

WHY THE RULES DO NOT GO AWAY
----------------------------
A VLM will confidently invent a plausible MRP if the print is smudged. That is
unacceptable for an enforcement tool. So the split is:

    VLM          reads the label      -> structured fields      (generalises)
    rule engine  applies LM(PC) Rules -> compliance verdict     (auditable)
    OpenCV       measures characters  -> Rule 7 mm test         (VLM cannot)

The legal decision is never made by the model. The model only reports what it
sees, and every field it returns is cross-checked before it is trusted.

BACKENDS
--------
    --backend ollama   local, offline, no API key. Needs Ollama + a vision model:
                           ollama pull qwen2.5vl:7b
                       Keeps the "no gated API" claim in the deck true.
    --backend gemini   cloud, more accurate, needs GEMINI_API_KEY.
                       NOTE: using this breaks the offline claim on slide 4.

USAGE
    python vlm_extract.py label.jpg --backend ollama
    python vlm_extract.py label.jpg --backend gemini
    python vlm_extract.py --selftest          # validation layer, no model needed
"""

import argparse
import base64
import json
import os
import re
import sys

# ---------------------------------------------------------------------------
# 1. THE SCHEMA — the contract the model must fill
# ---------------------------------------------------------------------------
# Asking for free text invites paraphrase and invention. Asking for a strict
# schema with an explicit null option gives the model a way to say "not
# present", which is the answer we most need it to be able to give.

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

Extract ONLY what is printed on this label. Return a single JSON object, no
markdown, no commentary.

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
def encode_image(path, max_side=1600):
    """
    Base64 the image, downscaling first if it is large.

    A 12 MP photo becomes an 8 MB base64 string. That is slow to upload, slow
    for the model, and large enough that some servers reject the request body
    outright. 1600 px on the long side keeps label text readable while cutting
    the payload by an order of magnitude.
    """
    try:
        import cv2
        im = cv2.imread(path)
        if im is None:
            raise ValueError
        h, w = im.shape[:2]
        if max(h, w) > max_side:
            f = max_side / max(h, w)
            im = cv2.resize(im, (int(w * f), int(h * f)),
                            interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", im, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if ok:
            return base64.b64encode(buf.tobytes()).decode()
    except Exception:
        pass
    with open(path, "rb") as fh:            # fall back to the raw bytes
        return base64.b64encode(fh.read()).decode()


class BackendError(Exception):
    """A vision backend (Ollama/Gemini) could not be reached or refused the request.

    Raised instead of calling sys.exit() so this module stays importable: a
    server process embedding these functions (the FastAPI app) must be able to
    catch a bad request from one caller without the whole process dying.
    """


class ExtractionFailed(Exception):
    """No image orientation produced a usable extraction.

    Carries .tried, the same per-orientation diagnostic list the CLI prints,
    so a caller (CLI or API) can report exactly what was attempted.
    """
    def __init__(self, tried):
        self.tried = tried
        super().__init__("no orientation produced usable JSON")


def ollama_models(host=None):
    """What is actually installed? Ollama answers 400 for an unknown model."""
    import urllib.request
    host = host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    try:
        with urllib.request.urlopen(host + "/api/tags", timeout=10) as r:
            return [m["name"] for m in json.loads(r.read()).get("models", [])]
    except Exception:
        return None


def call_ollama(image_path, model=None, host=None,
                force_json=True, debug=False):
    """
    Ask Ollama for one extraction.

    Two hard-won details:

    1. OPTIONS. Ollama's defaults give a short prediction budget and a small
       context. A busy label produces a long JSON object, and the generation
       simply stops — which surfaces as an EMPTY response rather than an error.
       num_predict and num_ctx are raised, and temperature pinned to 0 so the
       same label gives the same answer twice.

    2. force_json FALLBACK. With format="json" the runtime constrains decoding
       to valid JSON. When a small model cannot satisfy that constraint on a
       difficult image it emits nothing at all. Retrying WITHOUT the constraint
       usually yields prose with a JSON object inside it, which parse_json can
       still recover. A messy answer beats no answer.
    """
    import urllib.request, urllib.error

    model = model or os.environ.get("VLM_MODEL", "qwen2.5vl:7b")
    host = host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")

    b64 = encode_image(image_path)
    payload = {"model": model, "prompt": PROMPT, "images": [b64],
               "stream": False,
               "options": {"temperature": 0, "num_predict": 1200,
                           "num_ctx": 8192}}
    if force_json:
        payload["format"] = "json"

    req = urllib.request.Request(host + "/api/generate",
                                 json.dumps(payload).encode(),
                                 {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            out = json.loads(r.read())
        raw = out.get("response", "")
        if debug:
            print(f"    [debug] {len(raw)} chars, "
                  f"done_reason={out.get('done_reason')}")
            if raw:
                print(f"    [debug] {raw[:200]}")
        return raw

    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        try:
            detail = json.loads(detail).get("error", detail)
        except Exception:
            pass
        msg = f"\nOllama refused the request (HTTP {e.code}):\n  {detail}\n"
        have = ollama_models(host)
        if have is not None:
            msg += "\nInstalled models:" if have else "\nNo models installed."
            for m in have:
                msg += f"\n    {m}"
            msg += "\n\n   ollama pull qwen2.5vl:7b    (bigger, much better on busy labels)"
        raise BackendError(msg) from e

    except urllib.error.URLError as e:
        raise BackendError(f"\nCannot reach Ollama at {host}: {e.reason}\n"
                           "Open the Ollama app from the Start menu, then retry.") from e


def call_gemini(image_path, model="gemini-2.0-flash"):
    import urllib.request
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise BackendError("set GEMINI_API_KEY, or use --backend ollama")
    b64 = encode_image(image_path)
    mime = "image/jpeg"
    body = json.dumps({"contents": [{"parts": [
        {"text": PROMPT},
        {"inline_data": {"mime_type": mime, "data": b64}}]}],
        "generationConfig": {"response_mime_type": "application/json",
                             "temperature": 0}}).encode()
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent?key={key}")
    req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())["candidates"][0]["content"]["parts"][0]["text"]


def parse_json(raw):
    """
    Models wrap JSON in prose or fences more often than they should, and
    sometimes return nothing at all. An empty reply used to surface as
    'JSONDecodeError: Expecting value: line 1 column 1 (char 0)', which says
    nothing useful. Raise something that names the actual problem instead.
    """
    if raw is None or not str(raw).strip():
        raise ValueError("the model returned an EMPTY response")
    raw = str(raw).strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        raise ValueError(f"the model did not return JSON. It said:\n  {raw[:300]}")


def rotations(path):
    """
    Yield (label, temp_path) for 0/90/180/270 degree rotations.

    Several packs print the declaration block sideways — the oats pouch is
    rotated 90 degrees. A vision model handed sideways text frequently returns
    an empty reply rather than an error. Trying each orientation and keeping the
    richest result is far more reliable than asking the officer to rotate the
    photo correctly, and it costs only compute.
    """
    import cv2, tempfile, os as _os
    im = cv2.imread(path)
    if im is None:
        yield ("as-is", path)
        return
    codes = [("0", None), ("90", cv2.ROTATE_90_CLOCKWISE),
             ("180", cv2.ROTATE_180), ("270", cv2.ROTATE_90_COUNTERCLOCKWISE)]
    for name, code in codes:
        out = im if code is None else cv2.rotate(im, code)
        fd, tmp = tempfile.mkstemp(suffix=".jpg"); _os.close(fd)
        cv2.imwrite(tmp, out)
        yield (name + " deg", tmp)


def score_result(d):
    """How much did this orientation actually recover? Non-null mandatory fields."""
    return sum(1 for k in MANDATORY
               if d.get(k) not in (None, "", "null", "ILLEGIBLE"))


def extract_fields(image_path, backend="ollama", model=None, debug=False,
                   all_rotations=False):
    """
    Try each orientation against the vision backend, keep the richest result.

    This is the rotation-trying loop that used to live inline in main(). It is
    pulled out so it can be called from an importable function (extract, below)
    as well as the CLI, with no dependency on argparse.

    Returns (fields, best_rotation_label, tried) on success.
    Raises BackendError if the backend itself could not be reached (this
    propagates immediately, same as the old sys.exit did — no point trying
    the other three rotations if Ollama is down). Raises ExtractionFailed if
    every orientation was reachable but none produced usable JSON.
    """
    def call_backend(path):
        if backend != "ollama":
            return call_gemini(path, model or "gemini-2.0-flash")
        raw = call_ollama(path, model, force_json=True, debug=debug)
        if not str(raw).strip():
            if debug:
                print("    [debug] empty with format=json, retrying unconstrained")
            raw = call_ollama(path, model, force_json=False, debug=debug)
        return raw

    best, best_score, best_rot, tried = None, -1, None, []
    for label, path in rotations(image_path):
        try:
            d = parse_json(call_backend(path))
        except ValueError as e:
            tried.append(f"{label}: {e}")
            continue
        sc = score_result(d)
        tried.append(f"{label}: {sc}/{len(MANDATORY)} mandatory fields")
        if sc > best_score:
            best, best_score, best_rot = d, sc, label
        if sc == len(MANDATORY):
            break                       # nothing left to gain
        if not all_rotations:
            if sc >= 4:
                break                   # good enough, stop burning compute

    if best is None:
        raise ExtractionFailed(tried)
    return best, best_rot, tried


# ---------------------------------------------------------------------------
# 3. VALIDATION — the part that stops a confident model being believed
# ---------------------------------------------------------------------------
MANDATORY = ["manufacturer", "address", "net_quantity_value", "mrp_value",
             "mfg_date", "consumer_care"]

MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"


def _num(v):
    if v is None:
        return None
    m = re.search(r"\d+(?:\.\d+)?", str(v).replace(",", ""))
    return float(m.group(0)) if m else None


def _date(v):
    """Parse the formats real packs actually print. Returns (y, m) or None."""
    if not v:
        return None
    s = str(v).strip()
    m = re.search(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\b", s)
    if m:
        d, mo, y = (int(g) for g in m.groups())
        return (y if y > 99 else 2000 + y, mo)
    m = re.search(r"\b(\d{1,2})?\s*[/\-.]?\s*(%s)[a-z]*\s*[/\-.]?\s*(\d{2,4})\b"
                  % MONTHS, s, re.I)
    if m:
        mo = "janfebmaraprmayjunjulaugsepoctnovdec".index(m.group(2).lower()[:3]) // 3 + 1
        y = int(m.group(3))
        return (y if y > 99 else 2000 + y, mo)
    m = re.search(r"\b(0?[1-9]|1[0-2])[/\-](\d{2}|\d{4})\b", s)
    if m:
        y = int(m.group(2))
        return (y if y > 99 else 2000 + y, int(m.group(1)))
    return None


def validate(d):
    """
    Cross-check the model's answer against itself and against physical reality.
    Returns (verdict_rows, problems).

    Every check here exists because a real pack in our test set broke it.
    """
    problems = []

    mrp, usp = _num(d.get("mrp_value")), _num(d.get("unit_sale_price"))
    qty = _num(d.get("net_quantity_value"))

    # CHECK 1 — the MRP/USP swap. Walnut reported 1.60 (per gram) as the retail
    # price of a 250 g pack that actually costs 400.00. The unit price is by
    # definition smaller than the pack price whenever there is more than one unit.
    if mrp is not None and usp is not None and qty and qty > 1 and mrp <= usp:
        problems.append(f"MRP {mrp} is not greater than unit price {usp} for a "
                        f"{qty}-unit pack - the two are probably swapped")

    # CHECK 2 — arithmetic. mrp / qty should equal the printed unit price.
    if mrp and usp and qty:
        expected = mrp / qty
        if abs(expected - usp) > max(0.05, 0.1 * usp):
            problems.append(f"MRP/quantity = {expected:.2f} but the label prints "
                            f"unit price {usp:.2f} - one of the three is misread")

    # CHECK 3 — a pack cannot expire before it is made.
    a, b = _date(d.get("mfg_date")), _date(d.get("use_by"))
    if a and b and a > b:
        problems.append(f"manufacture {d['mfg_date']} is after use-by "
                        f"{d['use_by']} - dates are probably swapped")

    # CHECK 4 — ILLEGIBLE is a real answer and must reach the officer, not be
    # silently treated as absent.
    for k, v in d.items():
        if str(v).upper() == "ILLEGIBLE":
            problems.append(f"{k} printed but unreadable - officer must confirm")

    # Report EVERY field in the schema, not only the mandatory six. use_by is
    # what the officer needs to see next to the manufacture date, and the unit
    # sale price is what proves the MRP was read correctly. Dropping them made
    # the output look complete while hiding the evidence.
    rows = []
    for k in SCHEMA:
        v = d.get(k)
        state = ("MISSING" if v in (None, "", "null")
                 else "REVIEW" if str(v).upper() == "ILLEGIBLE" else "PRESENT")
        rows.append((k, state, v, k in MANDATORY))
    return rows, problems


def extract(image_path, backend="ollama", model=None, debug=False,
           all_rotations=False):
    """
    Run extraction + validation end to end and return a plain dict.

    This is the importable core Phase A calls for: everything main() used to
    do between "ask the model" and "print the table", with no printing and no
    sys.exit, so both the CLI and POST /scan can call it and are guaranteed to
    report the same thing for the same image. validate() itself is untouched;
    its (field, state, value, mandatory) tuples are converted to dicts here,
    at this API boundary, only.

    Returns:
        {
          "fields":              raw schema dict as returned by the model,
          "rows":                [{"field", "state", "value", "mandatory"}, ...],
          "problems":            [str, ...] cross-check failures,
          "mandatory_present":   int,
          "mandatory_total":     int,
          "best_rotation":       "0 deg" / "90 deg" / ...,
          "orientations_tried":  [str, ...] one diagnostic line per rotation,
        }

    Raises BackendError or ExtractionFailed — see extract_fields().
    """
    fields, best_rot, tried = extract_fields(image_path, backend=backend, model=model,
                                             debug=debug, all_rotations=all_rotations)
    rows, problems = validate(fields)
    row_dicts = [{"field": k, "state": state, "value": v, "mandatory": mand}
                for k, state, v, mand in rows]
    mandatory_present = sum(1 for r in row_dicts
                            if r["mandatory"] and r["state"] == "PRESENT")
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
SELFTEST = [
    ("walnut, MRP/USP swapped", {
        "manufacturer": "VMG FOODS PVT. LTD.", "address": "Khari Baoli Delhi-110006",
        "net_quantity_value": "250", "net_quantity_unit": "g",
        "mrp_value": "1.60", "unit_sale_price": "400.00",
        "mfg_date": "JUL.2026", "use_by": "JAN.2027",
        "consumer_care": "info@vinoddryfruits.com"}, 1),
    ("iron, counted in Units, clean", {
        "manufacturer": "VERSUNI INDIA HOME SOLUTIONS LTD.",
        "address": "Kolkata, West Bengal - 700016",
        "net_quantity_value": "1", "net_quantity_unit": "Unit",
        "mrp_value": "825.00", "unit_sale_price": "825.00",
        "mfg_date": "05/2025", "use_by": None,
        "consumer_care": "1800 572 1800"}, 0),
    ("perfume, dates swapped", {
        "manufacturer": "OG Beauty Private Limited", "address": "Ahmedabad - 380054",
        "net_quantity_value": "100", "net_quantity_unit": "ml",
        "mrp_value": "750.00", "unit_sale_price": "7.50",
        "mfg_date": "12/2028", "use_by": "12/2025",
        "consumer_care": "hi@ogbeauty.in"}, 1),
    ("chanachur, MRP blank on pack", {
        "manufacturer": "GIRISH CHANACHUR & SNACKS PVT. LTD.",
        "address": "Jharkhand-831001",
        "net_quantity_value": "200", "net_quantity_unit": "g",
        "mrp_value": "ILLEGIBLE", "unit_sale_price": "0.22",
        "mfg_date": "06/07/2026", "use_by": "05/11/2026",
        "consumer_care": "0657 2426571"}, 1),
]


def selftest():
    ok = 0
    for name, data, want_problems in SELFTEST:
        rows, problems = validate(data)
        good = (len(problems) > 0) == (want_problems > 0)
        ok += good
        print(f"  {'ok  ' if good else 'FAIL'} {name}")
        for p in problems:
            print(f"         -> {p}")
    print(f"\n{ok}/{len(SELFTEST)} validation cases correct")
    return ok == len(SELFTEST)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image", nargs="?")
    ap.add_argument("--backend", choices=["ollama", "gemini"], default="ollama")
    ap.add_argument("--model", default=None)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--debug", action="store_true",
                    help="show the raw model reply and stop reasons")
    ap.add_argument("--all-rotations", action="store_true",
                    help="try all four orientations even after a good one")
    ap.add_argument("--list", action="store_true",
                    help="show the vision models Ollama has installed")
    a = ap.parse_args()

    if a.list:
        have = ollama_models()
        if have is None:
            sys.exit("Cannot reach Ollama. Open the Ollama app and retry.")
        print("Installed models:" if have else "No models installed.")
        for m in have:
            print("   ", m)
        sys.exit(0)

    if a.selftest or not a.image:
        sys.exit(0 if selftest() else 1)

    try:
        result = extract(a.image, backend=a.backend, model=a.model,
                         debug=a.debug, all_rotations=a.all_rotations)
    except BackendError as e:
        print(str(e))
        sys.exit(1)
    except ExtractionFailed as e:
        print("orientations tried:")
        for t in e.tried:
            print("   " + t)
        print()
        print("No orientation produced usable JSON.\n")
        print("This image is too busy for a 3B model. In order of effort:")
        print("  1. Crop to JUST the declaration block and rerun.")
        print("     The oats pouch has two panels; the decorative one is noise.")
        print("  2. Use a larger local model:")
        print("       ollama pull qwen2.5vl:7b")
        print("       python vlm_extract.py img.jpg --model qwen2.5vl:7b")
        print("  3. Use the cloud model, which handles busy labels easily:")
        print("       set GEMINI_API_KEY=...")
        print("       python vlm_extract.py img.jpg --backend gemini")
        print("\nRun with --debug to see exactly what the model returned.")
        sys.exit(1)

    print("orientations tried:")
    for t in result["orientations_tried"]:
        print("   " + t)
    print()
    if result["best_rotation"] != "0 deg":
        print(f"NOTE: best result came from rotating the image {result['best_rotation']}. "
              "The label is printed sideways.\n")

    print(f"{'':<2}{'declaration':<22} {'state':<9} value")
    print("-" * 70)
    for r in result["rows"]:
        mark = "* " if r["mandatory"] else "  "
        print(f"{mark}{r['field']:<22} {r['state']:<9} {str(r['value'])[:34]}")
    print("  (* = mandatory under Rule 6)")

    print()
    if result["problems"]:
        print("VALIDATION PROBLEMS - do not trust these fields without review")
        print("-" * 70)
        for p in result["problems"]:
            print("  " + p)
    else:
        print("Validation clean: all cross-checks passed.")

    print()
    print(f"VERDICT: {result['mandatory_present']} of {result['mandatory_total']} "
          "mandatory declarations present"
          + (f", {len(result['problems'])} problem(s) flagged" if result["problems"] else ""))


if __name__ == "__main__":
    main()

"""
extract.py  —  PRAMAAN step 2: read the label, work out what each line IS
==========================================================================
OCR gives you text and boxes. It does not tell you which line is the MRP and
which is the manufacturer's address. That mapping is this file's job, and it is
where most of the Rule 6 completeness check actually lives.

WHY RULES AND NOT A TRAINED MODEL
---------------------------------
Indian packaging declarations are highly formulaic. "Net Qty.", "MRP Rs.",
"Mfd. by", "Best Before" appear on essentially every pack, in a handful of
spellings. A keyword + regex classifier handles this with zero training data and
is fully inspectable — when it gets something wrong you can see exactly why and
fix that rule. A trained classifier would need thousands of annotated packs and
would be a black box in front of a jury.

RUN THE LOGIC WITHOUT ANY OCR INSTALLED
---------------------------------------
    python extract.py --self-test

That runs the classifier against real label strings so you can check the brain
before downloading an OCR engine.

RUN ON A PHOTO
--------------
    python extract.py maggi.jpg

OCR ENGINE — pick ONE
    pip install rapidocr-onnxruntime     <- recommended: ~50 MB, no PyTorch
    pip install easyocr                  <- better accuracy, pulls ~2.5 GB torch
"""

import argparse
import re
import sys

# ---------------------------------------------------------------------------
# 1. THE FIELD DEFINITIONS  (Rule 6 mandatory declarations)
# ---------------------------------------------------------------------------
# Each field has: keyword patterns (strong evidence) and value patterns
# (supporting evidence). Scoring both means "MRP" alone still matches, and
# "Rs. 14.00" alone still matches, but "MRP Rs. 14.00" matches best.

FIELDS = {
    "manufacturer": {
        "label": "Manufacturer / packer / importer",
        "keywords": [r"\bmanufactured\s*by\b", r"\bmfd\.?\s*by\b", r"\bmfg\.?\s*by\b",
                     r"\bpacked\s*by\b", r"\bmarketed\s*by\b", r"\bimported\s*by\b",
                     r"\bmanufacturer\b"],
        # "inc" alone is a trap: "(INC. OF ALL TAXES)" on every MRP line matched it.
        # Company suffixes must appear in company form, e.g. "Pvt. Ltd.".
        "values": [r"\b(pvt\.?\s*ltd|private\s*limited|limited|llp|"
                   r"industries|foods|snacks|company|corp(oration)?)\b"],
    },
    "address": {
        "label": "Address of manufacturer",
        "keywords": [r"\baddress\b", r"\bregd\.?\s*office\b"],
        # A 6-digit PIN is the single most reliable signal an Indian address
        # line carries, so it stands alone. The first version only listed eight
        # states and missed Jharkhand entirely, which is why the real pack's
        # address went undetected.
        "strong": [r"[-\s]\d{6}\b"],
        "values": [r"\b(colony|nagar|road|marg|street|lane|sector|plot|"
                   r"p\.?\s?o\.?|dist(rict)?|near|opp(osite)?)\b",
                   r"\b(india|andhra|assam|bihar|chhattisgarh|goa|gujarat|haryana|"
                   r"himachal|jharkhand|karnataka|kerala|madhya|maharashtra|manipur|"
                   r"meghalaya|mizoram|nagaland|odisha|orissa|punjab|rajasthan|sikkim|"
                   r"tamil\s*nadu|telangana|tripura|uttarakhand|uttar\s*pradesh|"
                   r"west\s*bengal|delhi)\b"],
    },
    "net_quantity": {
        "label": "Net quantity",
        "keywords": [r"\bnet\s*(qty|quantity|wt|weight)\b", r"\bnet\s*content"],
        "values": [r"\b\d+(\.\d+)?\s*(g|gm|gms|gram|grams|kg|ml|l|ltr|litre|liters?)\b"],
    },
    "mrp": {
        "label": "Retail sale price (MRP)",
        # No trailing \b: OCR reads the rupee glyph as a letter and welds it on
        # ("MRP \u20b9" -> "MRPA"), which blocked the word boundary and made the
        # single most important declaration undetectable.
        "keywords": [r"\bm\.?\s?r\.?\s?p", r"\bmaximum\s*retail\s*price\b",
                     r"\bretail\s*sale\s*price\b"],
        # "44/-" is the standard Indian price notation and carries no currency
        # word or glyph at all, so a currency-prefixed pattern alone misses it.
        # A bare number scores only 1, so it cannot trigger MRP on its own —
        # it only confirms an MRP label that is already present.
        "values": [r"(rs\.?|inr|\u20b9)\s*\d+(\.\d{1,2})?",
                   r"\d+(\.\d{1,2})?\s*/\s*-",
                   r"\bincl(usive)?\.?\s*of\s*all\s*taxes\b",
                   r"\b\d{2,5}(\.\d{1,2})?\b", r"\b\d\.\d{1,2}\b"],
    },
    "mfg_date": {
        "label": "Month & year of manufacture / packing",
        # A line that says "USE BY" is an expiry, never a manufacture date, no
        # matter how good its date pattern looks. Without this, "USE BY
        # 06/07/2026" tied with best_before on score and won on dict order.
        "not": [r"\buse\s*by\b", r"\bbest\s*before\b", r"\bexpiry\b",
                r"\bexp\.?\s*date\b"],
        "keywords": [r"\b(mfd|mfg|manufactured|packed|pkd)\b.*\b(on|date)?\b",
                     r"\bdate\s*of\s*(manufacture|packing)\b"],
        # "." is NOT accepted as a date separator. Nutrition panels are full of
        # decimals like 12.97 and 3.01, and treating those as mm.yy dates
        # scattered false date matches across the whole nutrition table.
        "values": [r"\b\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b",
                   r"\b(0?[1-9]|1[0-2])[/\-]\d{4}\b",
                   r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*\d{2,4}\b"],
    },
    "best_before": {
        "label": "Best before / use by",
        "keywords": [r"\bbest\s*before\b", r"\buse\s*by\b", r"\bexpiry\b", r"\bexp\.?\s*date\b"],
        "values": [r"\b\d+\s*(months?|days?)\b"],
    },
    "consumer_care": {
        "label": "Consumer care details",
        "keywords": [r"\b(consumer|customer)\s*care\b", r"\btoll\s*free\b",
                     r"\bhelpline\b", r"\bcontact\s*us\b"],
        # an email, a 1800 number or a www address cannot plausibly be any other
        # declaration, so each stands alone as sufficient evidence
        "strong": [r"\b1800[\s\-]?\d{3}[\s\-]?\d{3,4}\b",
                   r"[\w.\-]+@[\w\-]+\.[a-z]{2,}", r"\bwww\.[\w\-.]+\b"],
        "values": [r"\b0\d{2,4}[\s\-]\d{6,8}\b"],
    },
    "fssai": {
        "label": "FSSAI licence number",
        "keywords": [r"\bfssai\b", r"\blic(ence|ense)?\.?\s*no\b"],
        "values": [r"\b\d{14}\b"],
    },
    "country_origin": {
        "label": "Country of origin",
        "keywords": [r"\bcountry\s*of\s*origin\b"],
        # "PRODUCT OF INDIA" tied with the address field (which also matches
        # "india") and lost on dict order. It is unambiguous, so make it strong.
        "strong": [r"\b(made|product)\s*(in|of)\s+\w+"],
        "values": [],
    },
}

# Rule 6 requires these. best_before / fssai / country_origin are food- or
# import-specific and are reported but not counted as missing by default.
MANDATORY = ["manufacturer", "address", "net_quantity", "mrp",
             "mfg_date", "consumer_care"]

# Fields where the LABEL alone proves nothing. "MRP :" printed with no price
# beside it is not a satisfied declaration — it is the exact violation an
# inspector is looking for. These require value evidence to count as present.
NEEDS_VALUE = {"net_quantity", "mrp", "mfg_date"}


# ---------------------------------------------------------------------------
# 1b. OCR REPAIR
# ---------------------------------------------------------------------------
# Preprocessing was benchmarked on the reference pack and moved OCR from 11/17
# to 12/17 known tokens — real but small. Every remaining failure was a
# CHARACTER confusion inside a value, not a missed detection:
#
#     200GM     -> 2G0GM / 2S0GM     zero read as G or S
#     44/-      -> 44l-              slash read as lowercase L
#     0.22      -> %22               "0." read as %
#     05/11/2026-> 05;11/2026        slash read as semicolon
#
# These are cheap to undo BECAUSE we know the context. A letter surrounded by
# digits is a misread digit. A separator inside a date is a slash. Applying the
# repair only inside numeric contexts keeps it from corrupting real words.

DIGIT_LOOKALIKE = str.maketrans({"O": "0", "o": "0", "G": "0", "S": "5",
                                 "I": "1", "l": "1", "|": "1", "B": "8"})


def repair_ocr(text):
    """Undo known OCR confusions inside numeric contexts only."""
    t = text

    # a letter wedged between two digits is a misread digit: 2G0GM -> 200GM
    t = re.sub(r"(?<=\d)([OoGSIlB])(?=\d)", lambda m: m.group(1).translate(DIGIT_LOOKALIKE), t)
    # ...and a letter between a digit and a unit: 20OGM -> 200GM
    t = re.sub(r"(?<=\d)([OoGSIlB])(?=\s?(?:gm?s?|kg|ml|l)\b)",
               lambda m: m.group(1).translate(DIGIT_LOOKALIKE), t, flags=re.I)

    # date separators: 05;11/2026 -> 05/11/2026
    t = re.sub(r"(?<=\d)[;:.](?=\d{1,2}[/\-;:.]\d{2,4})", "/", t)
    t = re.sub(r"(?<=\d{2})[;:](?=\d{4})", "/", t)

    # the Indian /- price notation, where the slash is read as l or 1
    t = re.sub(r"(?<=\d)[l1I](?=-)", "/", t)

    # "%22" is "0.22" with the leading zero-dot collapsed
    t = re.sub(r"(?<![\w.])%(\d{2})\b", r"0.\1", t)
    return t


# ---------------------------------------------------------------------------
# 2. THE CLASSIFIER
# ---------------------------------------------------------------------------
def classify(line):
    """
    Score a text line against every field. Returns (field_key, score, why).

    Three tiers:
      keywords (2) — an explicit label such as "Net Qty." or "MRP"
      strong   (2) — a value so distinctive it needs no label (an email address,
                     a 1800 helpline). Rule 6 is satisfied by consumer care
                     details however they are printed, and packs often give a
                     bare email with no "Consumer Care:" prefix in front of it.
      values   (1) — supporting evidence that is real but ambiguous. "14.00"
                     alone could be a price, a weight or a batch number.

    A line needs 2 to be accepted, so a label alone or a strong value alone is
    enough, while an ambiguous number alone is not.
    """
    t = repair_ocr(line).lower().strip()
    if not t:
        return None, 0, []

    # OCR frequently welds adjacent words together ("MFG DT" -> "MFGDT",
    # "USE BY" -> "USEBY"), which destroys the \b word boundaries the patterns
    # rely on. Testing a space-injected variant as well recovers those matches.
    spaced = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", t)
    variants = {t, spaced,
                re.sub(r"\b(mfg|mfd|use|net|best|lic)([a-z])", r"\1 \2", t)}

    best, best_score, best_why = None, 0, []
    for key, spec in FIELDS.items():
        if any(re.search(pat, v) for pat in spec.get("not", []) for v in variants):
            continue                       # disqualified for this field outright
        score, why = 0, []
        for pat in spec["keywords"]:
            if any(re.search(pat, v) for v in variants):
                score += 2
                why.append(f"keyword:{pat}")
        for pat in spec.get("strong", []):
            if any(re.search(pat, v) for v in variants):
                score += 2
                why.append(f"strong:{pat}")
        for pat in spec["values"]:
            if any(re.search(pat, v) for v in variants):
                score += 1
                why.append(f"value:{pat}")
        if score > best_score:
            best, best_score, best_why = key, score, why
    return best, best_score, best_why


def extract_mrp_numeral(line):
    """
    Isolate JUST the numeric value of the MRP.

    This matters for Rule 7: the numeral-size requirement applies to the price
    VALUE only. The prefix 'MRP Rs.' and the suffix 'inclusive of all taxes' are
    exempt. Measuring the whole line would flag compliant packs as violations.
    Returns (value_text, start_index, end_index) so the caller can crop the
    corresponding pixels for measurement.
    """
    # Skip the rupee-glyph artefact: OCR turns "MRP \u20b9" into "MRP 2", so the
    # FIRST number on the line is often not the price. Prefer a number that
    # looks like money - the /- notation, a decimal, or 2+ digits.
    for pat in (r"(\d+(?:\.\d{1,2})?)\s*[/1l]\s*-",
                r"(?:rs\.?|inr|\u20b9)\s*(\d+(?:\.\d{1,2})?)",
                r"\b(\d+\.\d{1,2})\b",
                r"\b(\d{2,5})\b"):
        m = re.search(pat, line, re.I)
        if m:
            return m.group(1), m.start(1), m.end(1)
    return None, None, None


# ---------------------------------------------------------------------------
# 2b. SPATIAL LABEL-VALUE PAIRING
# ---------------------------------------------------------------------------
def has_keyword(text):
    """Does this line carry an explicit field label (not just a value)?"""
    key, score, why = classify(text)
    return key if any(w.startswith("keyword:") for w in why) else None


def has_value_only(text):
    key, score, why = classify(text)
    if any(w.startswith("keyword:") for w in why):
        return False
    return bool(re.search(r"\d", text)) or bool(re.search(r"[A-Za-z]{3,}", text))


def pair_label_values(items, y_tol=1.8, x_gap=6.0):
    """
    Join each label box to its value box.

    On a real pack the printed layout is two columns — "NET WEIGHT :" in one and
    "200GM" in the other — and OCR returns them as separate boxes. Scoring lines
    independently means the label scores the keyword, the value scores the value,
    and neither reaches the threshold with full information.

    Worse, Indian packs often carry the values as a batch OVERPRINT applied after
    printing, so the value column can sit a whole row out of alignment with the
    labels. Matching only on exact row would miss every pair.

    So we search a window: to the right on roughly the same row, or below-right
    within y_tol label-heights. Nearest candidate wins. A label that finds no
    value is reported as unpaired rather than silently dropped — an MRP label
    with no value beside it is exactly the kind of thing an inspector must see.
    """
    used, out, unpaired = set(), [], []

    for i, (text, (x, y, w, h)) in enumerate(items):
        field = has_keyword(text)
        if not field:
            continue
        cy = y + h / 2
        best, best_d = None, 1e18
        for j, (t2, (x2, y2, w2, h2)) in enumerate(items):
            if j == i or j in used or not has_value_only(t2):
                continue
            cy2 = y2 + h2 / 2
            dy = cy2 - cy

            right_of = (x2 + w2 >= x + w * 0.4 and x2 - (x + w) <= x_gap * h
                        and -0.9 * h <= dy <= y_tol * h)
            # "Manufactured & Marketed by:" sits ABOVE the company name, not
            # left of it. Without a below-match that whole pattern is missed.
            x_overlap = min(x + w, x2 + w2) - max(x, x2)
            below = (0.3 * h < dy <= 2.2 * h and x_overlap > 0.25 * min(w, w2))

            if not (right_of or below):
                continue
            d = abs(dy) * 2 + max(0, x2 - (x + w)) / max(h, 1)
            if d < best_d:
                best, best_d = j, d
        own_key, _, own_why = classify(text)
        carries_value = any(w.startswith(("value:", "strong:")) for w in own_why)

        if best is not None:
            used.add(best)
            t2, b2 = items[best]
            merged = f"{text} {t2}".strip()
            out.append((merged, (x, y, max(x + w, b2[0] + b2[2]) - x,
                                 max(y + h, b2[1] + b2[3]) - y), i, best))
        elif not carries_value:
            unpaired.append((text, field))
    return out, unpaired


def column_pair_block(items, min_rows=3):
    """
    Pair a LABEL COLUMN with a VALUE COLUMN by rank order, not by proximity.

    Indian packs print the declaration labels at press time and stamp the values
    on later as a batch overprint. When that overprint drifts vertically — which
    it very often does — the value physically nearest a label belongs to the NEXT
    label down. Nearest-neighbour matching then gets EVERY row wrong by one, and
    silently: each pair looks locally plausible.

    Rank ordering is immune to a constant offset. Sort the labels top to bottom,
    sort the values top to bottom, and match 1st to 1st, 2nd to 2nd. The physical
    gap between a label and its value is irrelevant; only their order matters.

    Returns (pairs, drift_rows) where drift_rows reports how far the overprint
    had slipped, so the report can flag the pack for the officer.
    """
    # The label column is a PHYSICAL structure, not a semantic one. "BATCH NO."
    # is not a Rule 6 declaration and the classifier ignores it — but it occupies
    # a row in the printed table, so leaving it out of the column shifts the rank
    # zip by one and mis-assigns every field below it. Membership is therefore
    # decided by layout: a trailing colon, or a recognised keyword.
    labels, values = [], []
    for i, (text, box) in enumerate(items):
        _, _, why = classify(text)
        has_kw = any(w.startswith("keyword:") for w in why)
        has_val = any(w.startswith(("value:", "strong:")) for w in why)
        looks_like_label = bool(re.search(r"[:;]\s*$", text.strip()))
        if (looks_like_label or has_kw) and not has_val:
            labels.append((i, text, box))
        elif has_value_only(text):
            values.append((i, text, box))

    if len(labels) < min_rows:
        return [], 0

    # keep only labels that form a left-aligned column (the declaration table)
    xs = sorted(b[0] for _, _, b in labels)
    med_x = xs[len(xs) // 2]
    med_h = sorted(b[3] for _, _, b in labels)[len(labels) // 2]
    labels = [l for l in labels if abs(l[2][0] - med_x) < 2.0 * med_h]
    if len(labels) < min_rows:
        return [], 0

    labels.sort(key=lambda l: l[2][1])
    y_top = labels[0][2][1] - 2 * med_h
    y_bot = labels[-1][2][1] + labels[-1][2][3] + 3 * med_h
    label_right = max(b[0] + b[2] for _, _, b in labels)

    # values must sit to the right of the label column and inside its y span
    values = [v for v in values
              if v[2][0] > med_x + 0.5 * med_h and y_top <= v[2][1] <= y_bot]
    values.sort(key=lambda v: v[2][1])
    if len(values) < min_rows:
        return [], 0

    n = min(len(labels), len(values))
    pairs, drifts = [], []
    for (li, ltext, lbox), (vi, vtext, vbox) in zip(labels[:n], values[:n]):
        drifts.append((vbox[1] + vbox[3] / 2) - (lbox[1] + lbox[3] / 2))
        pairs.append((f"{ltext} {vtext}".strip(),
                      (lbox[0], lbox[1],
                       vbox[0] + vbox[2] - lbox[0],
                       max(lbox[3], vbox[3])), li, vi))

    drift_rows = round((sum(drifts) / len(drifts)) / max(med_h, 1), 1)
    return pairs, drift_rows


def check_consistency(found):
    """
    Cross-field sanity checks. These catch pairing mistakes that look fine
    locally but are impossible taken together.
    """
    notes = []
    dpat = r"\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})\b"

    def date_of(key):
        if key not in found:
            return None
        m = re.search(dpat, found[key][0])
        if not m:
            return None
        d, mo, y = (int(g) for g in m.groups())
        return (y if y > 99 else 2000 + y, mo, d)

    mfg, use = date_of("mfg_date"), date_of("best_before")
    if ("mfg_date" in found) != ("best_before" in found) or (
            ("mfg_date" in found and "best_before" in found) and not (mfg and use)):
        # Silence reads as a pass. Say plainly that the check could not run.
        bad = []
        if "mfg_date" in found and not mfg:
            bad.append(f"manufacture date unreadable: {found['mfg_date'][0][:24]}")
        if "best_before" in found and not use:
            bad.append(f"use-by date unreadable: {found['best_before'][0][:24]}")
        for b in bad:
            notes.append(f"SKIP date-order check could not run - {b}")
    if mfg and use:
        if mfg < use:
            notes.append(f"OK   manufacture {mfg[2]:02d}/{mfg[1]:02d}/{mfg[0]} "
                         f"precedes use-by {use[2]:02d}/{use[1]:02d}/{use[0]}")
        else:
            notes.append("FAIL manufacture date is NOT before the use-by date - "
                         "the label/value pairing is almost certainly off by a row")

    qtext = found.get("net_quantity", ("",))[0]
    # Letters wedged inside the number ("2G0GM") mean the OCR corrupted it.
    # Computing arithmetic on a corrupted quantity produced nonsense that LOOKED
    # like a finding, which is worse than reporting nothing.
    if re.search(r"\d[a-fh-z]\d", qtext, re.I):
        notes.append(f"WARN net quantity looks OCR-corrupted: '{qtext.strip()}' - "
                     "re-shoot before trusting it")
        return notes

    q = re.search(r"(\d+(?:\.\d+)?)\s*(g|gm|gms|kg|ml|l)\b", qtext, re.I)
    p = re.search(r"(\d+(?:\.\d+)?)", found.get("mrp", ("",))[0])
    if q and p:
        qty, price = float(q.group(1)), float(p.group(1))
        if qty <= 0:
            notes.append("WARN net quantity read as zero - unit price not computed")
        else:
            unit = price / qty
            if unit > price or unit <= 0:
                notes.append(f"WARN implausible unit price ({unit:.2f} per unit) - "
                             "quantity or price misread, flag for officer review")
            else:
                notes.append(f"     unit price = MRP {p.group(1)} / "
                             f"{q.group(1)}{q.group(2)} = {unit:.2f} per unit")
    return notes



# ---------------------------------------------------------------------------
# 3. OCR BACKENDS
# ---------------------------------------------------------------------------
def ocr_lines(path):
    """Return [(text, (x, y, w, h)), ...] using whichever engine is installed."""
    import importlib.util as u

    if u.find_spec("rapidocr_onnxruntime"):
        from rapidocr_onnxruntime import RapidOCR
        res, _ = RapidOCR()(path)
        out = []
        for box, text, conf in (res or []):
            xs = [p[0] for p in box]; ys = [p[1] for p in box]
            out.append((text, (int(min(xs)), int(min(ys)),
                               int(max(xs) - min(xs)), int(max(ys) - min(ys)))))
        return out, "rapidocr"

    if u.find_spec("easyocr"):
        import easyocr
        out = []
        for box, text, conf in easyocr.Reader(["en"], gpu=False).readtext(path):
            xs = [p[0] for p in box]; ys = [p[1] for p in box]
            out.append((text, (int(min(xs)), int(min(ys)),
                               int(max(xs) - min(xs)), int(max(ys) - min(ys)))))
        return out, "easyocr"

    if u.find_spec("pytesseract"):
        import pytesseract, cv2
        from pytesseract import Output
        img = cv2.imread(path)
        if img is None:
            sys.exit(f"could not read {path}")
        d = pytesseract.image_to_data(img, output_type=Output.DICT)
        rows = {}
        for i, txt in enumerate(d["text"]):
            if not txt.strip() or int(d["conf"][i]) < 30:
                continue
            k = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
            x, y, w, h = d["left"][i], d["top"][i], d["width"][i], d["height"][i]
            if k in rows:
                px, py, pw, ph, pt = rows[k]
                rows[k] = (min(px, x), min(py, y),
                           max(px + pw, x + w) - min(px, x),
                           max(py + ph, y + h) - min(py, y), pt + " " + txt)
            else:
                rows[k] = (x, y, w, h, txt)
        return [(v[4], (v[0], v[1], v[2], v[3])) for v in rows.values()], "tesseract"

    sys.exit("No OCR engine found.\n"
             "  pip install rapidocr-onnxruntime      (recommended, ~50 MB)\n"
             "  pip install easyocr                   (more accurate, ~2.5 GB)")


# ---------------------------------------------------------------------------
# 4. REPORT
# ---------------------------------------------------------------------------
def capture_warning(lines):
    """
    Detect the failure mode where the panel is too small in frame and OCR merges
    the label column into the value column by itself. The tell is that almost no
    line is a bare label — every detected line already carries a value, so there
    is nothing left to pair and any drift is locked in before we see the data.
    """
    labels = sum(1 for t, _ in lines if re.search(r"[:;]\s*$", t.strip()))
    if len(lines) >= 8 and labels == 0:
        return ("CAPTURE WARNING: no bare label boxes found. The OCR has merged "
                "the label\n  and value columns itself, so column drift cannot be "
                "corrected. Re-shoot\n  with the declaration panel filling the "
                "frame before trusting these values.\n")
    return None


def report(lines):
    lines = [(repair_ocr(t), b) for t, b in lines]
    found = {}

    warn = capture_warning(lines)
    if warn:
        print(warn)

    col_pairs, drift = column_pair_block(lines)
    pairs, unpaired = pair_label_values(lines)
    if col_pairs:
        claimed = {j for _, _, _, j in col_pairs} | {i for _, _, i, _ in col_pairs}
        pairs = [p for p in pairs if p[2] not in claimed and p[3] not in claimed]
        pairs = col_pairs + pairs
        unpaired = [u for u in unpaired
                    if not any(items_text == u[0]
                               for items_text in (lines[i][0] for i in claimed))]
        if abs(drift) > 0.6:
            print(f"OVERPRINT DRIFT DETECTED: the value column sits {drift:+.1f} rows "
                  f"from its labels.")
            print("  Paired by rank order instead of proximity. Flag for officer review.")
            print()
    if pairs:
        print("LABEL -> VALUE PAIRS RECOVERED BY LAYOUT")
        print("-" * 78)
        for merged, box, i, j in pairs:
            print(f"  {merged[:70]}")
        print()
    # merged pairs are evaluated alongside the raw lines
    lines = list(lines) + [(m, b) for m, b, _, _ in pairs]
    print(f"{'field':<22} {'score':>5}  text")
    print("-" * 78)
    for text, box in lines:
        key, score, _ = classify(text)
        name = FIELDS[key]["label"] if key else "-"
        shown = text if len(text) <= 46 else text[:43] + "..."
        print(f"{(name[:21] if key else '-'):<22} {score:>5}  {shown}")
        if key and score >= 2:
            _, _, why = classify(text)
            has_val = any(w.startswith(("value:", "strong:")) for w in why)
            if key in NEEDS_VALUE and not has_val:
                continue                      # label without a value proves nothing
            if key == "net_quantity" and re.search(r"\d[a-fh-z]\d", text, re.I):
                continue                      # "2S0GM" - corrupted, not verified
            if key == "mrp" and re.search(
                    r"\d{1,2}\s*[/\-.;:]\s*\d{1,2}\s*[/\-.;:]\s*\d{2,4}", text):
                # "MRPA 05/11/2026" — OCR merged the MRP label with a DATE from the
                # adjacent column. A price is never a date, so this is a mis-merge.
                # Reporting it as a valid MRP would be a confident wrong answer,
                # which is worse for an inspector than reporting nothing.
                continue
            if key not in found or score > found[key][1]:
                found[key] = (text, score, box)

    print()
    print("RULE 6 COMPLETENESS")
    print("-" * 78)
    missing = []
    for key in MANDATORY:
        if key in found:
            print(f"  [PRESENT] {FIELDS[key]['label']:<36} {found[key][0][:30]}")
        else:
            print(f"  [MISSING] {FIELDS[key]['label']}")
            missing.append(key)

    print()
    if "mrp" in found:
        val, s, e = extract_mrp_numeral(found["mrp"][0])
        if val:
            print(f"MRP numeral isolated for the Rule 7 size test: '{val}'")
            print(f"  (chars {s}-{e} of the line — prefix and tax suffix excluded,")
            print("   because the numeral-size requirement applies to the value only)")
    if unpaired:
        print("BORDERLINE - label printed but no value found beside it")
        print("-" * 78)
        for text, field in unpaired:
            print(f"  [REVIEW] {FIELDS[field]['label']:<36} printed as: {text[:26]}")
        print("  These need officer confirmation. A declaration label with no")
        print("  value next to it is either a misaligned overprint or a genuine")
        print("  Rule 6 omission, and the software should not guess which.")
        print()

    notes = check_consistency(found)
    if notes:
        print("CROSS-FIELD CONSISTENCY")
        print("-" * 78)
        for n in notes:
            print("  " + n)
        print()

    print(f"VERDICT: {len(MANDATORY)-len(missing)}/{len(MANDATORY)} mandatory "
          f"declarations detected" +
          ("" if not missing else f"  -> MISSING: {', '.join(missing)}"))


# ---------------------------------------------------------------------------
SELF_TEST = [
    ("Mfd. by: Nestle India Ltd.",                      "manufacturer"),
    ("Plot No. 5, Sanand, Ahmedabad, Gujarat - 382110", "address"),
    ("Net Qty.: 70 g",                                  "net_quantity"),
    ("MRP Rs. 14.00 (incl. of all taxes)",              "mrp"),
    ("Mfd: 06/2026",                                    "mfg_date"),
    ("Best Before 9 months from packaging",             "best_before"),
    ("Consumer Care: 1800 103 1947",                    "consumer_care"),
    ("care@in.nestle.com",                              "consumer_care"),
    ("FSSAI Lic. No. 10012041000123",                   "fssai"),
    ("Country of Origin: India",                        "country_origin"),
    ("MAGGI 2-MINUTE NOODLES",                          None),
    ("Taste bhi, Health bhi",                           None),
]


def self_test():
    print("CLASSIFIER SELF-TEST (no OCR needed)\n")
    ok = 0
    for text, expected in SELF_TEST:
        key, score, _ = classify(text)
        got = key if score >= 2 else None
        good = got == expected
        ok += good
        print(f"  {'ok ' if good else 'FAIL'}  {text[:44]:<46} "
              f"-> {str(got):<16} (want {expected})")
    print(f"\n{ok}/{len(SELF_TEST)} correct")
    return ok == len(SELF_TEST)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image", nargs="?")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()

    if a.self_test or not a.image:
        sys.exit(0 if self_test() else 1)

    lines, engine = ocr_lines(a.image)
    print(f"OCR engine: {engine}   |   {len(lines)} text lines found\n")
    report(lines)


if __name__ == "__main__":
    main()

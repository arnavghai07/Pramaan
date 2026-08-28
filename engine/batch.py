"""
batch.py  —  PRAMAAN
=====================
Runs extract.py's pipeline over a whole folder of label photos and writes one
CSV row per pack.

WHY A BATCH RUNNER MATTERS MORE THAN IT LOOKS
---------------------------------------------
Testing one pack at a time tells you whether that pack worked. It does not tell
you which failures REPEAT. A failure that appears on six packs is a rule worth
writing; a failure that appears once is probably a bad photo. You cannot see
that distinction without processing the set together.

The CSV is also your validation dataset. "6/6 on one packet" is an anecdote.
"52/60 declarations across 10 packs, with these 8 named failures" is evidence,
and evidence is what a jury scores.

GROUND TRUTH (optional but worth doing)
---------------------------------------
Put a file called truth.csv beside your photos:

    filename,manufacturer,address,net_quantity,mrp,mfg_date,consumer_care
    maggi.jpg,1,1,1,1,1,1
    soap.jpg,1,1,1,1,0,1

1 = the declaration IS printed on the pack, 0 = it genuinely is not.
Then batch.py reports true accuracy: not just what it found, but what it MISSED
and what it hallucinated. Without truth.csv you only get detection counts.

USAGE
    python batch.py photos/
    python batch.py photos/ --truth photos/truth.csv --out results.csv
"""

import argparse
import csv
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract import (FIELDS, MANDATORY, NEEDS_VALUE, classify, repair_ocr,
                     ocr_lines, pair_label_values, column_pair_block,
                     check_consistency, capture_warning)

IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff")


def analyse(path):
    """Run the pipeline on one image. Returns (found, flags)."""
    lines, engine = ocr_lines(path)
    lines = [(repair_ocr(t), b) for t, b in lines]
    flags = []

    if capture_warning(lines):
        flags.append("capture_merged_columns")

    col_pairs, drift = column_pair_block(lines)
    pairs, unpaired = pair_label_values(lines)
    if col_pairs:
        claimed = {j for _, _, _, j in col_pairs} | {i for _, _, i, _ in col_pairs}
        pairs = [p for p in pairs if p[2] not in claimed and p[3] not in claimed]
        pairs = col_pairs + pairs
        if abs(drift) > 0.6:
            flags.append(f"overprint_drift_{drift:+.1f}rows")

    allrows = list(lines) + [(m, b) for m, b, _, _ in pairs]

    found = {}
    for text, box in allrows:
        key, score, why = classify(text)
        if not key or score < 2:
            continue
        has_val = any(w.startswith(("value:", "strong:")) for w in why)
        if key in NEEDS_VALUE and not has_val:
            continue
        if key not in found or score > found[key][1]:
            found[key] = (text, score, box)

    for note in check_consistency(found):
        if note.startswith(("WARN", "FAIL", "SKIP")):
            flags.append(note.split()[0].lower() + "_" + note.split()[1])

    return found, flags, engine, len(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--out", default="results.csv")
    ap.add_argument("--truth", default=None)
    a = ap.parse_args()

    files = sorted(f for f in os.listdir(a.folder) if f.lower().endswith(IMG_EXT))
    if not files:
        sys.exit(f"no images in {a.folder}")

    truth = {}
    if a.truth and os.path.exists(a.truth):
        with open(a.truth, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                truth[row["filename"]] = row

    cols = ["file", "lines"] + MANDATORY + ["detected", "flags"]
    rows, totals = [], {k: 0 for k in MANDATORY}
    tp = {k: 0 for k in MANDATORY}       # correctly found
    fn = {k: 0 for k in MANDATORY}       # printed but missed
    fp = {k: 0 for k in MANDATORY}       # not printed but claimed

    for name in files:
        path = os.path.join(a.folder, name)
        try:
            found, flags, engine, nlines = analyse(path)
        except Exception:
            rows.append({"file": name, "lines": 0, "detected": "ERROR",
                         "flags": traceback.format_exc(limit=1).splitlines()[-1][:60]})
            continue

        row = {"file": name, "lines": nlines, "flags": ";".join(flags)}
        n = 0
        for k in MANDATORY:
            got = k in found
            row[k] = (found[k][0][:28] if got else "")
            n += got
            totals[k] += got
            if name in truth:
                want = truth[name].get(k, "1") == "1"
                if want and got:
                    tp[k] += 1
                elif want and not got:
                    fn[k] += 1
                elif not want and got:
                    fp[k] += 1
        row["detected"] = f"{n}/{len(MANDATORY)}"
        rows.append(row)

    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})

    # ---- console summary -------------------------------------------------
    print(f"{len(files)} packs  ->  {a.out}\n")
    print(f"{'file':<26} {'det':>5}  flags")
    print("-" * 78)
    for r in rows:
        print(f"{r['file'][:25]:<26} {r.get('detected',''):>5}  {r.get('flags','')[:40]}")

    print()
    print("PER-DECLARATION DETECTION RATE")
    print("-" * 78)
    for k in MANDATORY:
        pct = 100 * totals[k] / len(files)
        bar = "#" * int(pct / 5)
        print(f"  {FIELDS[k]['label'][:34]:<36} {totals[k]:>2}/{len(files)}  "
              f"{pct:5.0f}%  {bar}")

    if truth:
        print()
        print("ACCURACY AGAINST truth.csv")
        print("-" * 78)
        print(f"  {'declaration':<36} {'recall':>7} {'missed':>7} {'false+':>7}")
        for k in MANDATORY:
            denom = tp[k] + fn[k]
            rec = 100 * tp[k] / denom if denom else float("nan")
            print(f"  {FIELDS[k]['label'][:34]:<36} {rec:6.0f}% {fn[k]:>7} {fp[k]:>7}")
        print()
        print("  missed = printed on the pack but not detected (the dangerous one)")
        print("  false+ = claimed present when it is not printed")
    else:
        print()
        print("No truth.csv supplied, so these are DETECTION counts, not accuracy.")
        print("A declaration can be absent because the pack lacks it (a real")
        print("violation) or because OCR failed. Only ground truth separates those.")


if __name__ == "__main__":
    main()

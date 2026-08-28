"""
measure_chart.py  —  PRAMAAN, THE GATE (no-printer edition)
============================================================
Takes your photograph of the test chart and answers one question:

    At what character height does your camera stop measuring accurately?

It finds the ArUco marker, derives pixels-per-millimetre, then measures every
text row and compares the result against what that row is known to be.

    python measure_chart.py photo.jpg
"""

import argparse
import os
import sys
import tempfile
import cv2
import numpy as np

MARKER_MM = 40.0
TEST_MM = [1.0, 1.5, 2.0, 3.0, 4.0, 6.0]
TOLERANCE_MM = 0.15      # what counts as a good enough measurement for Rule 7

TILT_REJECT_PCT = 8.0    # CLAUDE.md rule 5: reject, don't just warn - see measure()

# ---------------------------------------------------------------------------
# Rule 7 Table-I - minimum numeral height in mm, by principal display panel
# (PDP) area, from the Legal Metrology (Packaged Commodities) Rules, 2011,
# Rule 7(2)/(3) and Table-I, AS AMENDED by the Legal Metrology (Packaged
# Commodities) Amendment Rules, 2017 - Notification G.S.R. 629(E), dated
# 23 June 2017, effective 1 January 2018. That amendment replaced the
# pre-2018 net-quantity/weight-volume-based Table-I and the separate
# area-based Table-II with a single consolidated, PDP-area-based Table-I.
#
# Verified against the amended rule text itself, not a summary site: the
# figures below were cross-checked across multiple independent reproductions
# of Rule 7 as amended, each carrying the amendment citation "Substituted by
# Notification No. G.S.R. 629(E), dated 23.6.2017" attached to sub-rules (2),
# (3) and Table-I.
#
# Rule 7(3) separately sets a general floor of 2 mm for ANY blown, formed,
# molded, embossed or perforated LETTER, regardless of PDP area. Table-I's
# bracket 1 below gives 1.5 mm for a NUMERAL specifically at PDP area
# <= 50 cm^2 - lower than that general 2 mm floor. This is intentional, not
# an error: Table-I is the specific provision governing numeral height under
# 7(2), and the specific provision controls over the general one it sits
# below.
RULE7_TABLE_I = [
    # (area upper bound in cm^2, inclusive; None = no upper bound,
    #  normal-case minimum mm, blown/formed/molded minimum mm)
    (50.0,   1.0, 1.5),
    (100.0,  1.5, 3.0),
    (500.0,  2.5, 4.0),
    (2500.0, 4.0, 6.0),
    (None,   6.0, 6.0),
]

REVIEW_BAND_MM = 0.2    # CLAUDE.md rule 3: within this of the threshold -> REVIEW, never PASS/FAIL


def find_marker(bgr):
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    p = cv2.aruco.DetectorParameters()
    p.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    corners, ids, _ = cv2.aruco.ArucoDetector(d, p).detectMarkers(bgr)
    if ids is None or len(ids) == 0:
        return None
    return corners[0].reshape(4, 2)


def rectify(bgr, pts, marker_mm=MARKER_MM, out_ppm=20.0):
    """Flatten the chart plane so 1 mm == out_ppm px everywhere."""
    side = marker_mm * out_ppm
    dst = np.float32([[0, 0], [side, 0], [side, side], [0, side]])
    Hm, _ = cv2.findHomography(pts.astype(np.float32), dst)
    h, w = bgr.shape[:2]
    box = cv2.perspectiveTransform(
        np.float32([[[0, 0]], [[w, 0]], [[w, h]], [[0, h]]]), Hm).reshape(4, 2)
    tx, ty = -box[:, 0].min(), -box[:, 1].min()
    T = np.float32([[1, 0, tx], [0, 1, ty], [0, 0, 1]])
    ow = int(min(box[:, 0].max() + tx, 8000))
    oh = int(min(box[:, 1].max() + ty, 8000))
    return cv2.warpPerspective(bgr, T @ Hm, (ow, oh),
                               borderValue=(255, 255, 255)), out_ppm


def text_rows(rect, ppm, chart_columns=False):
    """
    Find text-like rows and measure each one's height. Rows are grouped by
    connected components merged horizontally.

    chart_columns=True restricts detection to the printed test chart's own
    known column (used only by the chart-calibration CLI path, which compares
    against TEST_MM). A real pack has neither a known row count nor a known
    column position, so real-pack measurement (measure()'s default, the API,
    and pack-mode CLI) always leaves this False: every text-shaped candidate
    on the whole rectified frame comes back, and the caller decides which one
    is the Rule 7 compliance target - see rule7_verdict().
    """
    g = cv2.cvtColor(rect, cv2.COLOR_BGR2GRAY)
    th = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY_INV, 31, 12)
    # join characters into words/lines but do not bridge separate rows
    ker = cv2.getStructuringElement(cv2.MORPH_RECT, (int(3 * ppm), 1))
    joined = cv2.dilate(th, ker, iterations=1)
    cnts, _ = cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    x_lo = x_hi = None
    if chart_columns:
        # rectify() translates the frame so nothing is clipped, so the marker
        # corner is NOT at the origin afterwards. Re-detect it to find the true
        # origin. On the chart the marker sits at 8 mm and the MRP rows start
        # at 62 mm, so rows begin (62 - 8) = 54 mm right of the marker's left
        # edge. Filtering on that band drops the grey "1.0 mm" labels and the
        # marker caption, which would otherwise be counted as rows and shift
        # every pairing against TEST_MM.
        mk = find_marker(rect)
        ox = mk[:, 0].min() if mk is not None else 0.0
        x_lo, x_hi = ox + 50 * ppm, ox + 60 * ppm

    rows = []
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        if w < 1.2 * ppm or h < 0.4 * ppm:      # noise
            continue
        if w / h < 2.5 or h > 12 * ppm:         # not a text line / is the marker
            continue
        if x_lo is not None and not (x_lo <= x <= x_hi):   # wrong column (chart mode only)
            continue
        rows.append((x, y, w, h))

    rows.sort(key=lambda r: r[1])

    # merge fragments that belong to the same row (same vertical band)
    merged = []
    for r in rows:
        if merged and abs(r[1] - merged[-1][1]) < 0.6 * ppm:
            px, py, pw, ph = merged[-1]
            x0, y0 = min(px, r[0]), min(py, r[1])
            x1, y1 = max(px + pw, r[0] + r[2]), max(py + ph, r[1] + r[3])
            merged[-1] = (x0, y0, x1 - x0, y1 - y0)
        else:
            merged.append(r)
    return merged, th


class MarkerNotFound(Exception):
    """No ArUco marker was detected in the image, or the image couldn't be read."""


class MarkerTilted(Exception):
    """
    The marker's four sides are not equal length after perspective, so
    rectify() would scale x and y by different amounts and every millimetre
    reading on this photo would be wrong (CLAUDE.md rule 5: this is the exact
    failure mode a document-scanner-style rectification produces - measured
    5.8% error on A4, 33% on square when this check is skipped).
    """


def measure(image_path, marker_mm=MARKER_MM, out_ppm=20.0, chart_columns=False):
    """
    Core measurement pipeline: find the marker, derive px-per-mm, rectify, and
    measure every text row's height in millimetres.

    Pulled out of main() so the CLI (which additionally compares against the
    test chart's known TEST_MM ground truth) and POST /measure (which has no
    ground truth — it is measuring a real, unknown pack) can share it.

    marker_mm is the printed marker's real-world size in millimetres — the
    "marker size in" input POST /measure takes. It must flow into rectify(),
    not just into the raw capture-scale estimate: rectify()'s homography
    target square is sized from marker_mm, so a wrong value here scales every
    measured height on the whole image, not just the marker.

    Returns a dict:
        width_px, height_px   - original photo dimensions
        tilt_spread_pct       - spread across the marker's 4 side lengths.
                                 This is the aspect-ratio check CLAUDE.md rule 5
                                 requires: an app that rectified the photo to a
                                 fixed page aspect scales x and y differently,
                                 which turns the marker's true square into a
                                 non-square quadrilateral here.
        capture_scale_ppm     - raw pixels-per-mm at the marker, pre-rectify
        rows                  - [{"x","y","w","h","height_mm"}, ...] top-to-bottom

    chart_columns is only True for the chart-calibration CLI path; every
    real-pack caller (the API, pack-mode CLI) leaves it False - see
    text_rows().

    Raises MarkerNotFound if the image can't be read or no marker is visible.
    Raises MarkerTilted if the marker's aspect ratio is skewed past
    TILT_REJECT_PCT - CLAUDE.md rule 5, enforced here rather than only
    reported, so a bad measurement can never reach a caller looking valid.
    """
    bgr = cv2.imread(image_path)
    if bgr is None:
        raise MarkerNotFound(f"could not read {image_path}")

    h, w = bgr.shape[:2]
    pts = find_marker(bgr)
    if pts is None:
        raise MarkerNotFound(
            "NO MARKER FOUND.\n"
            "  - is the whole black square visible in the photo?\n"
            "  - is the photo sharp? tap to focus before shooting\n"
            "  - reduce screen glare; tilt the screen slightly")

    sides = [np.linalg.norm(pts[i] - pts[(i + 1) % 4]) for i in range(4)]
    ppm_raw = (sum(sides) / 4) / marker_mm
    spread = (max(sides) - min(sides)) / max(sides) * 100

    if spread > TILT_REJECT_PCT:
        raise MarkerTilted(
            f"MARKER TOO TILTED: {spread:.1f}% spread across its four sides "
            f"(limit {TILT_REJECT_PCT:.0f}%).\n"
            "  - shoot square-on to the marker, not at an angle\n"
            "  - do not use a document-scanner app's rectified output - it\n"
            "    scales x and y differently and every mm reading would be wrong")

    rect, ppm = rectify(bgr, pts, marker_mm=marker_mm, out_ppm=out_ppm)
    rows, _ = text_rows(rect, ppm, chart_columns=chart_columns)

    return {
        "width_px": w,
        "height_px": h,
        "tilt_spread_pct": spread,
        "capture_scale_ppm": ppm_raw,
        "rows": [{"x": x, "y": y, "w": rw, "h": rh, "height_mm": rh / ppm}
                for x, y, rw, rh in rows],
    }


def rule7_lookup(pdp_area_cm2, container="normal"):
    """
    Minimum numeral height in mm for the given PDP area, per Table-I - see
    RULE7_TABLE_I above for the citation.
    """
    if pdp_area_cm2 <= 0:
        raise ValueError("pdp_area_cm2 must be a positive number")
    if container not in ("normal", "blown"):
        raise ValueError('container must be "normal" or "blown"')
    for upper, normal_mm, blown_mm in RULE7_TABLE_I:
        if upper is None or pdp_area_cm2 <= upper:
            return blown_mm if container == "blown" else normal_mm
    raise AssertionError("unreachable: RULE7_TABLE_I's last bracket has no upper bound")


def rule7_verdict(height_mm, pdp_area_cm2, container="normal"):
    """
    Classify ONE measured numeral height against Rule 7 Table-I.

    Deliberately takes a single height, not the whole rows list: Rule 7
    governs the printed numeral itself, not every text-shaped thing a camera
    frame happens to include (a "MRP Rs." prefix, an "incl. of all taxes"
    suffix, unrelated print nearby). The caller selects which detected row IS
    that numeral - the --target flag in the CLI, an explicit row index at any
    future API boundary - before this runs. This function never sees, and
    therefore can never FAIL on, a row that was not selected.

    Returns (threshold_mm, verdict). verdict is one of PASS / FAIL / REVIEW.
    REVIEW covers anything within +/- REVIEW_BAND_MM of the threshold and is
    terminal - CLAUDE.md rule 3: a measurement this close to the legal line is
    never confidently rounded into PASS or FAIL.
    """
    threshold_mm = rule7_lookup(pdp_area_cm2, container)
    if height_mm >= threshold_mm + REVIEW_BAND_MM:
        return threshold_mm, "PASS"
    if height_mm <= threshold_mm - REVIEW_BAND_MM:
        return threshold_mm, "FAIL"
    return threshold_mm, "REVIEW"


VERDICT_COLOR_BGR = {"PASS": (0, 160, 0), "FAIL": (0, 0, 220), "REVIEW": (0, 165, 255)}


def annotate(rect, rows, target_index=None, verdict=None, threshold_mm=None):
    """
    Draw every detected row as a thin grey diagnostic box, and - if a target
    was selected - the Rule 7 compliance target as a thick, coloured,
    labelled box (measured height + verdict). This is the evidence overlay:
    what makes a FAIL visible on the image rather than only in a printout.

    Returns a new BGR image array; the caller writes it to disk
    (cv2.imwrite) - this function never touches the filesystem.
    """
    out = rect.copy()
    for i, r in enumerate(rows):
        if i == target_index:
            continue
        x, y, w, h = int(r["x"]), int(r["y"]), int(r["w"]), int(r["h"])
        cv2.rectangle(out, (x, y), (x + w, y + h), (160, 160, 160), 1)

    if target_index is not None and 0 <= target_index < len(rows):
        r = rows[target_index]
        x, y, w, h = int(r["x"]), int(r["y"]), int(r["w"]), int(r["h"])
        color = VERDICT_COLOR_BGR.get(verdict, (255, 0, 0))
        cv2.rectangle(out, (x, y), (x + w, y + h), color, 3)
        label = f"{r['height_mm']:.2f} mm  {verdict}"
        if threshold_mm is not None:
            label += f"  (min {threshold_mm:.1f} mm)"
        ty = y - 10 if y - 10 > 12 else y + h + 24
        cv2.putText(out, label, (x, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2, cv2.LINE_AA)
    return out


def _default_overlay_path(image_path):
    root, ext = os.path.splitext(image_path)
    return f"{root}_overlay{ext or '.png'}"


def run_chart_mode(args):
    """Chart-calibration path: compares detected rows against TEST_MM."""
    try:
        result = measure(args.image, marker_mm=args.marker_mm, chart_columns=True)
    except (MarkerNotFound, MarkerTilted) as e:
        sys.exit(str(e))

    W, H = result["width_px"], result["height_px"]
    spread = result["tilt_spread_pct"]
    print(f"photo         : {W} x {H} px  ({W*H/1e6:.1f} MP)")
    print(f"marker found  : yes")
    print(f"tilt spread   : {spread:.1f} %  "
          f"({'ok' if spread < TILT_REJECT_PCT else 'RESHOOT - too angled'})")
    print(f"CAPTURE SCALE : {result['capture_scale_ppm']:.1f} px per mm")
    print()

    rows = result["rows"]
    print(f"found {len(rows)} text rows (expected {len(TEST_MM)})")
    print()
    print(f"{'true mm':>8} {'measured':>10} {'error':>9}   verdict")
    print("-" * 46)

    # rows come out top-to-bottom, same order the chart was drawn
    ok_floor = None
    for true_mm, row in zip(TEST_MM, rows):
        meas = row["height_mm"]
        err = abs(meas - true_mm)
        good = err <= TOLERANCE_MM
        if good and ok_floor is None:
            ok_floor = true_mm
        print(f"{true_mm:>7.1f}  {meas:>9.2f}  {err:>8.2f}   "
              f"{'PASS' if good else 'off'}")

    print()
    print("WHAT THIS MEANS")
    if ok_floor is not None and ok_floor <= 1.0:
        print("  Your camera measures 1 mm text within tolerance.")
        print("  -> Rule 7 checking works at this distance. Proceed.")
    elif ok_floor is not None:
        print(f"  Your smallest reliable measurement is about {ok_floor} mm.")
        print("  -> Move the camera closer, or add a macro capture step for the")
        print("     declaration panel. The architecture does not change.")
    else:
        print("  No row measured within tolerance. Most likely causes:")
        print("   - photo out of focus (tap the screen to focus, then shoot)")
        print("   - too far away; fill the frame with the chart")
        print("   - screen glare washing out the text")
    print()
    print(f"  capture scale was {result['capture_scale_ppm']:.1f} px/mm. Roughly 20 px/mm or better")
    print("  is where 1 mm text becomes comfortable to measure AND read.")


def run_pack_mode(args):
    """
    Rule 7 verdict on a real pack.

    Capture protocol (BUILD_PLAN.md Phase C): the marker and the MRP
    declaration are photographed together in one tight frame. This does not
    attempt to locate the declaration on a whole-pack photo - see
    text_rows()'s docstring.
    """
    try:
        result = measure(args.image, marker_mm=args.marker_mm, chart_columns=False)
    except (MarkerNotFound, MarkerTilted) as e:
        sys.exit(str(e))

    rows = result["rows"]
    print(f"marker found, tilt spread {result['tilt_spread_pct']:.1f}% (ok)")
    print(f"CAPTURE SCALE : {result['capture_scale_ppm']:.1f} px per mm")
    print()
    print(f"{len(rows)} text-shaped row(s) detected:")
    print(f"{'index':>5}  {'height mm':>9}   bbox (x,y,w,h)")
    print("-" * 46)
    for i, r in enumerate(rows):
        print(f"{i:>5}  {r['height_mm']:>9.2f}   "
              f"({r['x']:.0f}, {r['y']:.0f}, {r['w']:.0f}, {r['h']:.0f})")
    print()

    verdict = threshold_mm = None
    target_index = None
    if args.target is None:
        print("No --target given: no Rule 7 verdict was computed.")
        print("Pick the index above that is the MRP numeral VALUE (not the")
        print("'MRP Rs.' prefix or the 'incl. of all taxes' suffix) and rerun")
        print("with --target <index>. An evidence overlay showing every")
        print("detected row is still saved below for a sanity check.")
    elif not (0 <= args.target < len(rows)):
        sys.exit(f"--target {args.target} is out of range (0..{len(rows) - 1})")
    else:
        target_index = args.target
        height_mm = rows[target_index]["height_mm"]
        threshold_mm, verdict = rule7_verdict(height_mm, args.pdp_area, args.container)
        print(f"TARGET row {target_index}: measured {height_mm:.2f} mm")
        print(f"Rule 7 Table-I minimum for {args.pdp_area:g} cm^2 PDP, "
              f"{args.container} case: {threshold_mm:.1f} mm")
        print(f"VERDICT: {verdict}")

    # Re-detect the marker to get the rectified image for the overlay.
    # measure() deliberately does not return it - its dict must stay exactly
    # what MeasureResponse (api/models.py) declares.
    bgr = cv2.imread(args.image)
    pts = find_marker(bgr)
    rect, _ = rectify(bgr, pts, marker_mm=args.marker_mm)
    overlay = annotate(rect, rows, target_index, verdict, threshold_mm)
    out_path = args.overlay_out or _default_overlay_path(args.image)
    cv2.imwrite(out_path, overlay)
    print(f"\nevidence overlay saved: {out_path}")


def build_arg_parser():
    ap = argparse.ArgumentParser(
        description="PRAMAAN Rule 7 measurement: chart calibration, or a "
                     "real-pack verdict when --pdp-area is given.")
    ap.add_argument("image", nargs="?", help="photo to measure")
    ap.add_argument("--pdp-area", type=float, metavar="CM2",
                     help="principal display panel area in cm^2 - switches to pack mode")
    ap.add_argument("--container", choices=["normal", "blown"], default="normal",
                     help="normal print, or blown/formed/molded/embossed/perforated "
                          "(default: normal)")
    ap.add_argument("--target", type=int, metavar="INDEX",
                     help="index (from the printed row list) of the row that is the "
                          "Rule 7 compliance target")
    ap.add_argument("--overlay-out", metavar="PATH",
                     help="where to save the evidence overlay image (pack mode only; "
                          "default: <image>_overlay.<ext>)")
    ap.add_argument("--marker-mm", type=float, default=MARKER_MM,
                     help=f"printed marker size in mm (default: {MARKER_MM})")
    ap.add_argument("--self-test", action="store_true",
                     help="run synthetic self-checks - no photo needed")
    return ap


def main():
    args = build_arg_parser().parse_args()

    if args.self_test:
        sys.exit(0 if self_test() else 1)

    if not args.image:
        build_arg_parser().error("an image path is required unless --self-test is given")

    if args.pdp_area is not None:
        run_pack_mode(args)
    else:
        run_chart_mode(args)


def self_test():
    """
    Synthetic checks that need no photo - see BUILD_PLAN.md Phase C. A real
    photo (printed marker next to a real pack's MRP declaration) is still
    required to close the Phase C gate; this only proves the logic underneath
    the camera input is correct.
    """
    print("MEASURE_CHART SELF-TEST (no photo needed)\n")
    ok = 0
    total = 0

    def check(name, cond):
        nonlocal ok, total
        total += 1
        ok += bool(cond)
        print(f"  {'ok ' if cond else 'FAIL'}  {name}")

    # --- Table-I lookup ------------------------------------------------
    check("bracket 1 normal (10 cm^2)", rule7_lookup(10, "normal") == 1.0)
    check("bracket 1 blown  (10 cm^2)", rule7_lookup(10, "blown") == 1.5)
    check("bracket 1 upper bound (50 cm^2) stays in bracket 1",
          rule7_lookup(50, "normal") == 1.0)
    check("bracket 2 starts just above 50 cm^2",
          rule7_lookup(50.01, "normal") == 1.5)
    check("bracket 3 (100-500 cm^2) normal", rule7_lookup(300, "normal") == 2.5)
    check("bracket 4 (500-2500 cm^2) blown", rule7_lookup(1000, "blown") == 6.0)
    check("bracket 5 (>2500 cm^2) normal == blown",
          rule7_lookup(5000, "normal") == rule7_lookup(5000, "blown") == 6.0)

    # --- REVIEW band -----------------------------------------------------
    check("clearly above threshold -> PASS",
          rule7_verdict(1.5, 10, "normal")[1] == "PASS")
    check("clearly below threshold -> FAIL",
          rule7_verdict(0.5, 10, "normal")[1] == "FAIL")
    check("exactly at threshold -> REVIEW",
          rule7_verdict(1.0, 10, "normal")[1] == "REVIEW")
    check("+0.2mm boundary is PASS (inclusive)",
          rule7_verdict(1.2, 10, "normal")[1] == "PASS")
    check("-0.2mm boundary is FAIL (inclusive)",
          rule7_verdict(0.8, 10, "normal")[1] == "FAIL")
    check("just inside the band (+0.19mm) -> REVIEW, never PASS",
          rule7_verdict(1.19, 10, "normal")[1] == "REVIEW")

    # --- generic row detection on a synthetic already-rectified image ----
    ppm = 20.0
    canvas = np.full((200, 400, 3), 255, np.uint8)
    cv2.rectangle(canvas, (50, 80), (250, 120), (0, 0, 0), -1)   # 40px = 2.0mm tall
    rows, _ = text_rows(canvas, ppm, chart_columns=False)
    check("generic text_rows finds the synthetic row", len(rows) == 1)
    if rows:
        _, _, _, row_h_px = rows[0]
        check("its measured height is close to the drawn 2.0 mm",
              abs(row_h_px / ppm - 2.0) < 0.15)

    # --- marker tilt gate --------------------------------------------------
    def marker_canvas(stretch=1.0):
        d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        side = 300
        marker = cv2.aruco.generateImageMarker(d, 0, side)
        marker_bgr = cv2.cvtColor(marker, cv2.COLOR_GRAY2BGR)
        if stretch != 1.0:
            marker_bgr = cv2.resize(marker_bgr, (side, int(side * stretch)))
        c = np.full((900, 900, 3), 255, np.uint8)
        mh, mw = marker_bgr.shape[:2]
        c[100:100 + mh, 100:100 + mw] = marker_bgr
        return c

    fd1, square_path = tempfile.mkstemp(suffix=".png")
    fd2, tilted_path = tempfile.mkstemp(suffix=".png")
    os.close(fd1)
    os.close(fd2)
    try:
        cv2.imwrite(square_path, marker_canvas(1.0))
        cv2.imwrite(tilted_path, marker_canvas(1.25))   # 25% -> well over TILT_REJECT_PCT

        try:
            measure(square_path, marker_mm=40.0)
            check("square marker is accepted (no false reject)", True)
        except MarkerTilted:
            check("square marker is accepted (no false reject)", False)

        try:
            measure(tilted_path, marker_mm=40.0)
            check("stretched marker is REJECTED, not just warned", False)
        except MarkerTilted:
            check("stretched marker is REJECTED, not just warned", True)
    finally:
        for p in (square_path, tilted_path):
            if os.path.exists(p):
                os.remove(p)

    # --- overlay smoke test ------------------------------------------------
    fake_rows = [{"x": 50, "y": 80, "w": 200, "h": 40, "height_mm": 2.0}]
    overlay = annotate(canvas, fake_rows, 0, "REVIEW", 2.5)
    check("annotate() returns an image of the same shape", overlay.shape == canvas.shape)

    print(f"\n{ok}/{total} checks passed")
    return ok == total


if __name__ == "__main__":
    main()

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


def rule7_result(image_path, marker_mm=MARKER_MM, pdp_area_cm2=None,
                 container="normal", target_index=None):
    """
    HTTP-friendly wrapper around measure() + rule7_verdict() + annotate().

    This does exactly what run_pack_mode() already does for the CLI, minus
    the printing and file-writing: it calls measure(), rule7_verdict() and
    annotate() unmodified and returns their results as data (the overlay as
    PNG bytes) so api/main.py can serve them over POST /inspect and
    POST /measure/candidates without touching disk.

    target_index selects which detected row (from measure()'s "rows" list)
    is the Rule 7 measurement target - the same role --target plays on the
    CLI. Deliberately kept a plain optional index, not a semantic concept:
    today a caller (the UI) resolves it by asking an inspector to point at
    the printed price; a later automatic MRP-numeral identification pass
    only needs to change how target_index gets filled in before this
    function is called; it does not change this function's contract. When
    target_index is None (no target chosen yet, e.g. showing measurement
    candidates before a selection is made), the verdict/threshold both come
    back None and the overlay draws every candidate row unhighlighted - this
    is the same "no --target given" state run_pack_mode already prints.

    Returns:
        {
          "tilt_spread_pct", "capture_scale_ppm": as measure() returns,
          "rows":                                 as measure() returns,
          "target_index":                         echoed back, or None,
          "threshold_mm", "verdict":               None unless both
                                                    target_index and
                                                    pdp_area_cm2 are given,
          "overlay_png":                          PNG-encoded bytes, or
                                                    None if cv2.imencode
                                                    somehow fails,
        }

    Raises MarkerNotFound / MarkerTilted, exactly as measure() does.
    Raises ValueError if target_index is given but out of range for the
    rows measure() found - the same bounds check the CLI performs before
    calling rule7_verdict().
    """
    result = measure(image_path, marker_mm=marker_mm, chart_columns=False)
    rows = result["rows"]

    verdict = threshold_mm = None
    if target_index is not None:
        if not (0 <= target_index < len(rows)):
            raise ValueError(
                f"target_index {target_index} is out of range (0..{len(rows) - 1})")
        if pdp_area_cm2 is not None:
            height_mm = rows[target_index]["height_mm"]
            threshold_mm, verdict = rule7_verdict(height_mm, pdp_area_cm2, container)

    # Re-detect the marker to get the rectified image for the overlay -
    # measure() deliberately does not return it, same reasoning as
    # run_pack_mode()'s identical re-detection above.
    bgr = cv2.imread(image_path)
    pts = find_marker(bgr)
    rect, _ = rectify(bgr, pts, marker_mm=marker_mm)
    overlay = annotate(rect, rows, target_index, verdict, threshold_mm)
    ok, buf = cv2.imencode(".png", overlay)

    return {
        "tilt_spread_pct": result["tilt_spread_pct"],
        "capture_scale_ppm": result["capture_scale_ppm"],
        "rows": rows,
        "target_index": target_index,
        "threshold_mm": threshold_mm,
        "verdict": verdict,
        "overlay_png": buf.tobytes() if ok else None,
    }


_ROTATIONS = {0: None, 90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
             270: cv2.ROTATE_90_COUNTERCLOCKWISE}


def rule7_candidates_all_orientations(image_path, marker_mm=MARKER_MM):
    """
    Rule 7 candidate rows across every 90-degree orientation of the
    rectified frame - not just the single orientation measure() happens to
    produce.

    WHY THIS EXISTS
    ----------------
    text_rows()'s character-joining dilation only bridges gaps along the
    rectified frame's horizontal axis (see its own comment: "join
    characters into words/lines but do not bridge separate rows" - the
    dilation kernel is (3*ppm, 1), one pixel tall). rectify()'s output
    orientation is fixed by the ArUco marker's own encoded corner order,
    not by the pack's print direction. So when the physical marker happens
    to be taped rotated in-plane relative to the printed text - a real,
    observed case, not a hypothetical: a genuine walnut-pack photo with the
    marker rotated roughly 90 degrees relative to its MRP declaration - a
    price that should measure as one row instead fragments into separate
    per-digit boxes, because the gaps between digits now run along the axis
    the dilation cannot bridge. pt.jpg never had this problem because its
    marker happened to be oriented in-line with the print.

    This function does not decide which orientation is "correct" -
    CLAUDE.md rule 1, nothing here judges or selects. It calls the
    UNMODIFIED text_rows() at 0/90/180/270 degrees of the same rectified
    pixels measure() already produces, and returns every orientation's
    candidates side by side. find_marker(), rectify(), measure(),
    text_rows(), rule7_lookup(), rule7_verdict() and annotate() are called
    exactly as they already exist - none of them are touched, and the tilt
    gate is enforced exactly once, by measure() itself, which this
    function calls for that purpose (so a tilted marker still raises
    MarkerTilted here as it always has, before any orientation is tried).

    The existing target-selection workflow (a human picks the row that is
    the MRP numeral) is unchanged in kind: it now simply has more than one
    orientation's candidate list to choose from, the same way an inspector
    already chooses among several rows within one orientation. A candidate
    that happens to merge the MRP with an adjacent declaration (observed on
    the walnut photo at 90 degrees, where the dilation reached far enough
    to bridge MRP and USP together) is exactly the kind of thing this
    function must surface, not filter or silently prefer - a human decides
    it is not the right box, the same way they would reject any other wrong
    candidate.

    Returns:
        {
          "tilt_spread_pct":    from measure() - one calibration, not
                                repeated per orientation,
          "capture_scale_ppm":  from measure() - ditto,
          "orientations": [
              {"rotation_deg": 0,   "rows": [...]},   # identical to
                                                       # measure()'s own
                                                       # rows - not recomputed
              {"rotation_deg": 90,  "rows": [...]},
              {"rotation_deg": 180, "rows": [...]},
              {"rotation_deg": 270, "rows": [...]},
          ],
        }

    Each row dict has the same "x","y","w","h","height_mm" shape measure()
    already returns, expressed in THAT orientation's own rectified-pixel
    coordinates - a caller rendering an overlay for orientation N must
    annotate a frame rotated to orientation N, not the 0-degree frame (see
    rule7_overlay_for_orientation() below, which does exactly that).

    Raises MarkerNotFound / MarkerTilted exactly as measure() does.
    """
    base = measure(image_path, marker_mm=marker_mm, chart_columns=False)

    bgr = cv2.imread(image_path)
    pts = find_marker(bgr)
    rect, ppm = rectify(bgr, pts, marker_mm=marker_mm)

    orientations = []
    for deg, code in _ROTATIONS.items():
        if deg == 0:
            rows = base["rows"]                       # reuse - do not redo measure()'s work
        else:
            rotated = cv2.rotate(rect, code)
            raw_rows, _ = text_rows(rotated, ppm, chart_columns=False)
            rows = [{"x": x, "y": y, "w": w, "h": h, "height_mm": h / ppm}
                    for x, y, w, h in raw_rows]
        orientations.append({"rotation_deg": deg, "rows": rows})

    return {
        "tilt_spread_pct": base["tilt_spread_pct"],
        "capture_scale_ppm": base["capture_scale_ppm"],
        "orientations": orientations,
    }


def rule7_overlay_for_orientation(image_path, marker_mm, rotation_deg, rows,
                                  target_index=None, verdict=None, threshold_mm=None):
    """
    Evidence overlay for one orientation's candidate rows - a thin wrapper
    around the UNMODIFIED annotate(), rotating the rectified frame to match
    the orientation `rows` was computed in (see
    rule7_candidates_all_orientations() above). Exists so a caller can
    show, and let a human pick from, 0/90/180/270-degree candidate sets
    without reimplementing annotate()'s drawing logic.

    target_index/verdict/threshold_mm are passed straight through to
    annotate(); leave them None to render every row unhighlighted, exactly
    as annotate() already does when no target has been chosen yet.
    """
    bgr = cv2.imread(image_path)
    pts = find_marker(bgr)
    rect, _ = rectify(bgr, pts, marker_mm=marker_mm)
    code = _ROTATIONS[rotation_deg]
    frame = rect if code is None else cv2.rotate(rect, code)
    overlay = annotate(frame, rows, target_index, verdict, threshold_mm)
    ok, buf = cv2.imencode(".png", overlay)
    return buf.tobytes() if ok else None


def _locate_target(crop, box_w, ppm):
    """
    Cluster a (cropped) binary threshold image into candidate glyphs and
    pick the one closest to the crop's own horizontal center. Factored out
    of rule7_measure_selected_region() so the SAME rule can be applied
    twice - once to an inspector's raw selection, then again to that
    selection's own detected target for refinement - without risking two
    copies of the logic drifting apart.

    Reuses text_rows()'s own horizontal-joining kernel and noise floor -
    not a second, incompatible detector - so every character of one
    printed value (e.g. "400.00") joins into a single candidate the same
    way text_rows() already joins them on the whole frame. The kernel is
    one row tall, so it can only spread ink sideways - it can never create
    ink in a row that had none, which is what lets height be re-measured
    from the undilated pixels without the dilation itself inflating
    anything.

    Returns (status, payload, num_clusters):
      "target", (cx, cy, cw, ch, cy0, cy1), N
          Exactly one confident cluster. (cx, cy, cw, ch) is its DILATED
          bounding box, in `crop`'s own coordinates - used only to know
          which pixels belong together, never to measure height.
          (cy0, cy1) is its ORIGINAL, UNDILATED ink extent (row indices in
          `crop`), restricted to BOTH that cluster's column span AND its
          own row span - restricting by column alone previously let an
          already-separated, vertically stacked print line (a real case:
          a USP value printed directly beneath the MRP, almost touching
          it) bleed back into the measurement even though the clustering
          above had already told the two lines apart; confirmed on that
          real selection, column-only restriction measured 4.70mm by
          scanning nearly the whole selection instead of the ~2.8mm the
          MRP cluster's own bounding box actually covered. N is the total
          number of candidate clusters found (including ones not chosen).
      "ambiguous", ranked, N
          Two or more clusters at similarly central positions - genuinely
          unclear which is intended. `ranked` lists every surviving
          cluster, closest-to-center first.
      "none", None, 0
          No cluster survived the noise filter.
    """
    ker = cv2.getStructuringElement(cv2.MORPH_RECT, (int(3 * ppm), 1))
    joined = cv2.dilate(crop, ker, iterations=1)
    cnts, _ = cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    clusters = []
    for c in cnts:
        cx, cy, cw, ch = cv2.boundingRect(c)
        if cw < 1.2 * ppm or ch < 0.4 * ppm:    # same noise floor text_rows() uses
            continue
        clusters.append((cx, cy, cw, ch))

    if not clusters:
        return "none", None, 0

    crop_center_x = box_w / 2.0
    ranked = sorted(clusters, key=lambda c: abs((c[0] + c[2] / 2.0) - crop_center_x))
    best_dist = abs((ranked[0][0] + ranked[0][2] / 2.0) - crop_center_x)

    # A second candidate about equally close to the selection's center is
    # genuinely ambiguous, not a tie to break silently - CLAUDE.md rule 3,
    # this never guesses among options a human would find equally
    # plausible.
    ambiguous = False
    if len(ranked) > 1:
        second_dist = abs((ranked[1][0] + ranked[1][2] / 2.0) - crop_center_x)
        ambiguous = (second_dist - best_dist) < 1.2 * ppm

    if ambiguous:
        return "ambiguous", ranked, len(ranked)

    cx, cy, cw, ch = ranked[0]
    sub = crop[cy:cy + ch, cx:cx + cw]
    ink_rows = np.where((sub > 0).any(axis=1))[0]
    if ink_rows.size == 0:
        return "none", None, len(clusters)
    cy0 = cy + int(ink_rows.min())
    cy1 = cy + int(ink_rows.max())
    return "target", (cx, cy, cw, ch, cy0, cy1), len(clusters)


def rule7_measure_selected_region(image_path, marker_mm, rotation_deg, region,
                                  pdp_area_cm2=None, container="normal",
                                  gap_ppm_fraction=0.3):
    """
    FALLBACK Rule 7 measurement, used only when automatic candidate
    discovery (rule7_candidates_all_orientations()) cannot isolate a clean,
    complete numeral - observed on two real photographs of the same real
    pack: text_rows()'s fixed-width character-joining dilation either
    fragments a multi-digit price into separate digits, or (a different,
    also-observed failure) over-merges it with an adjacent declaration
    printed close by (USP, batch number). Automatic discovery remains the
    first path always - this function is never called until an inspector
    has already looked at the automatic candidates and found none clean.

    WHY THE RECTANGLE ITSELF IS NEVER TRUSTED
    -------------------------------------------
    CLAUDE.md rule 3 - confident wrong beats nothing is false - applies as
    much to a hand-drawn box as to a model's guess. An inspector's
    selection is realistically generous (padding above/below/around the
    print), so its raw height is not the character height. Confirmed on a
    real selection: a generous box around a real "400.00" measured 5.5mm
    as a rectangle but 2.45mm of actual ink - using the rectangle would
    have been more than double the true value.

    HOW THE SELECTION IS MEASURED
    -------------------------------
    This crops the SAME raw, un-dilated binary threshold text_rows()
    already computes and returns as its second value
    (`rows, th = text_rows(...)`) - no new thresholding pipeline, so this
    can never disagree with the automatic path about what counts as "ink".

    An earlier version of this function measured height with a plain
    row-wise ink projection across the FULL WIDTH of the selection: any
    threshold pixel anywhere in a row - a table border, a neighbouring
    column's character, a decimal point - made that row count as
    "occupied", silently pulling the measured extent toward whatever else
    happened to be inside the box's horizontal span. Confirmed on a real
    selection: the same printed "400.00" measured anywhere from 2.60mm to
    4.70mm purely from drawing a slightly wider or taller box around it -
    a deterministic consequence of that projection having no concept of
    which pixels belonged to which character, not camera noise.

    This version instead clusters the selection's ink into candidate
    GLYPHS before measuring anything (see _locate_target() above for the
    exact rule), and picks the candidate closest to the selection's own
    center. Its height is then re-measured from the ORIGINAL, UNDILATED
    threshold pixels within that candidate's own bounding box - both its
    column span AND its row span, never from the dilated image and never
    from anything outside the chosen cluster's own extent.

    Neighbouring print (an adjacent column, a table border, another line)
    is not silently absorbed the way the old row-projection could absorb
    it: it only joins the target's cluster if the two are close enough for
    the joining dilation to actually bridge them - the same, already-
    accepted risk text_rows() itself carries on the automatic path - and
    otherwise remains its own separate, off-center candidate that is
    correctly ignored, or triggers the ambiguous outcome if it lands close
    to center.

    ITERATIVE REFINEMENT (stability against how generously the box is drawn)
    ---------------------------------------------------------------------
    A single confident pass already never trusts the rectangle's own size,
    but it can still be sensitive to exactly how much blank margin, or how
    much of a barely-touching neighbour, the inspector's own box happens
    to include on its very first try - confirmed on a real pack: boxes
    that all looked like "a reasonable selection around the MRP" measured
    anywhere from 2.10mm to 4.70mm before this refinement, purely from
    small differences in the drawn rectangle.

    So once a CONFIDENT single target is found, this re-applies
    _locate_target() to that target's own bounding box - zero added
    margin (a small margin was tested and found to actively prevent
    convergence, letting the box slowly regrow and drift on each pass
    instead of stabilizing) - for at most two more passes, stopping the
    moment the box stops changing. Confirmed empirically: differently
    drawn "reasonable" boxes around the same real printed value converge
    to the IDENTICAL final box after exactly one refinement pass.

    This refinement only ever runs after the FIRST pass already returned a
    confident single target - an ambiguous or empty first pass is never
    iterated, so refinement can never manufacture a confident answer out
    of a genuinely ambiguous selection; it can only stabilize an already-
    confident one. Symmetrically, if a later refinement pass were ever to
    come back ambiguous or empty on an already-tight box (not observed in
    testing, but never trusted blindly), the last confident box is kept
    rather than discarding a working answer.

    Exactly one confidently-selected candidate (after refinement) means
    the selection contained one line of print - its final pixel extent
    divided by the existing ppm is the measured height, and only that
    number (never the rectangle's own height, never the dilated blob's
    own height) is passed to the UNMODIFIED rule7_verdict(). No
    candidates, or two candidates too close to call, both come back as
    "not measurable" - this function never guesses which part of an
    ambiguous selection is the intended numeral, and never averages
    multiple candidates.

    gap_ppm_fraction is accepted for signature stability but is no longer
    used - the row-projection gap tolerance it used to configure was
    replaced by the joining-dilation clustering above.

    rotation_deg selects which orientation's rectified frame `region` is
    expressed in - the same 0/90/180/270 convention
    rule7_candidates_all_orientations() and rule7_overlay_for_orientation()
    already use, so an inspector already looking at one of those oriented
    overlays can draw directly on it.

    region: (x, y, w, h) in that orientation's rectified-pixel coordinates
    - exactly the coordinate space rule7_candidates_all_orientations()'s
    rows already use.

    Returns:
        {
          "measured_height_mm": float or None - None unless exactly one
                                 confident candidate cluster was found,
          "threshold_mm": float or None - None unless a height was
                          measured AND pdp_area_cm2 was given,
          "verdict": "PASS"/"FAIL"/"REVIEW" or None,
          "problem": str or None - set whenever a verdict could not be
                     produced, naming exactly why (no glyph found /
                     N candidates found - ambiguous / PDP area not
                     provided),
          "band_count": int - how many candidate clusters were found on
                        the inspector's OWN selection (before any
                        refinement), for a caller that wants the raw
                        diagnostic without parsing `problem`,
          "overlay_png": PNG bytes. Always shows the raw selection (thin,
                         unhighlighted, like any other non-target
                         candidate); when exactly one confident candidate
                         was found, its TRIMMED, REFINED ink extent is
                         drawn as the highlighted target (labelled with
                         the measured height and verdict) - the selection
                         rectangle itself is never drawn as if it were the
                         measurement. When zero or several
                         similarly-positioned candidates were found, every
                         candidate is drawn unhighlighted alongside the
                         selection, so the evidence shows an inspector
                         exactly why their selection was rejected.
        }

    Raises MarkerNotFound / MarkerTilted exactly as measure() does - this
    calls measure() for the tilt gate and calibration, same as every other
    Rule 7 path.
    """
    measure(image_path, marker_mm=marker_mm, chart_columns=False)   # enforces the tilt gate

    bgr = cv2.imread(image_path)
    pts = find_marker(bgr)
    rect, ppm = rectify(bgr, pts, marker_mm=marker_mm)
    code = _ROTATIONS[rotation_deg]
    frame = rect if code is None else cv2.rotate(rect, code)

    _, th = text_rows(frame, ppm, chart_columns=False)   # reuse the unmodified threshold; rows unused

    x, y, w, h = (int(v) for v in region)
    crop = th[y:y + h, x:x + w]

    status, payload, num_clusters = _locate_target(crop, w, ppm)

    measured_height_mm = threshold_mm = verdict = problem = None
    selection_row = {"x": x, "y": y, "w": w, "h": h, "height_mm": h / ppm}
    display_rows = [selection_row]
    target_index = None

    if status == "none":
        problem = "No measurable glyph found inside the selected region."
    elif status == "ambiguous":
        ranked = payload
        problem = (f"{len(ranked)} candidate values found inside the selected "
                  "region, at similarly central positions - unclear which "
                  "one is the intended numeral. Redraw the selection around "
                  "only the complete MRP numeral.")
        for cx, cy, cw, ch in ranked:
            sub = crop[cy:cy + ch, cx:cx + cw]
            ink_rows = np.where((sub > 0).any(axis=1))[0]
            if ink_rows.size == 0:
                continue
            cy0 = cy + int(ink_rows.min())
            cy1 = cy + int(ink_rows.max())
            display_rows.append({"x": x + cx, "y": y + cy0, "w": cw,
                                 "h": cy1 - cy0 + 1, "height_mm": (cy1 - cy0 + 1) / ppm})
    else:
        # status == "target": one confident cluster on the inspector's OWN
        # selection. Refine it against its own bounding box - see the
        # "ITERATIVE REFINEMENT" docstring section above for why zero
        # margin and a 2-iteration cap.
        cx, cy, cw, ch, cy0, cy1 = payload
        tx, ty, tw, tgt_h = x + cx, y + cy0, cw, cy1 - cy0 + 1

        for _ in range(2):
            next_crop = th[ty:ty + tgt_h, tx:tx + tw]
            next_status, next_payload, _ = _locate_target(next_crop, tw, ppm)
            if next_status != "target":
                break   # never discard an already-confident result
            ncx, ncy, ncw, nch, ncy0, ncy1 = next_payload
            ntx, nty, ntw, nth = tx + ncx, ty + ncy0, ncw, ncy1 - ncy0 + 1
            if (ntx, nty, ntw, nth) == (tx, ty, tw, tgt_h):
                break   # converged
            tx, ty, tw, tgt_h = ntx, nty, ntw, nth

        measured_height_mm = tgt_h / ppm
        display_rows.append({"x": tx, "y": ty, "w": tw, "h": tgt_h,
                             "height_mm": measured_height_mm})
        target_index = 1
        if pdp_area_cm2 is None:
            problem = "PDP area not provided - cannot look up the Rule 7 threshold."
        else:
            threshold_mm, verdict = rule7_verdict(measured_height_mm, pdp_area_cm2, container)

    overlay = annotate(frame, display_rows, target_index, verdict, threshold_mm)
    ok, buf = cv2.imencode(".png", overlay)

    return {
        "measured_height_mm": measured_height_mm,
        "threshold_mm": threshold_mm,
        "verdict": verdict,
        "problem": problem,
        "band_count": num_clusters,
        "overlay_png": buf.tobytes() if ok else None,
    }


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

    # --- Rule 7 manual-selection: _locate_target() clustering & refinement --
    def refine(crop, box_w, ppm_, max_iters=2):
        """
        Mirrors rule7_measure_selected_region()'s own iterative-refinement
        loop exactly, so its convergence behaviour can be unit-tested on a
        synthetic binary image - no photo, marker, or file I/O needed.
        """
        status, payload, n = _locate_target(crop, box_w, ppm_)
        if status != "target":
            return status, None, n
        cx, cy, cw, ch, cy0, cy1 = payload
        tx, ty, tw, th_ = cx, cy0, cw, cy1 - cy0 + 1
        for _ in range(max_iters):
            nxt = crop[ty:ty + th_, tx:tx + tw]
            nstatus, npayload, _ = _locate_target(nxt, tw, ppm_)
            if nstatus != "target":
                break
            ncx, ncy, ncw, nch, ncy0, ncy1 = npayload
            nt = (tx + ncx, ty + ncy0, ncw, ncy1 - ncy0 + 1)
            if nt == (tx, ty, tw, th_):
                break
            tx, ty, tw, th_ = nt
        return "target", th_ / ppm_, n

    glyph = np.zeros((150, 300), np.uint8)
    cv2.rectangle(glyph, (60, 50), (240, 100), 255, -1)   # one 50px (2.5mm) glyph

    status_tight, h_tight, _ = refine(glyph[45:105, 55:245], 190, ppm)
    status_wide, h_wide, _ = refine(glyph[20:130, 10:290], 280, ppm)
    check("different reasonable boxes around one glyph converge to the same height",
          status_tight == "target" and status_wide == "target" and
          h_tight is not None and abs(h_tight - h_wide) < 1e-9)
    check("converged height matches the drawn 2.5mm glyph",
          status_tight == "target" and abs(h_tight - 2.5) < 0.15)

    exact = glyph[50:100, 60:240]   # exactly the glyph's own drawn extent
    status_exact, payload_exact, _ = _locate_target(exact, exact.shape[1], ppm)
    check("zero margin: an exact-fit box's own ink extent is returned unchanged",
          status_exact == "target" and payload_exact[4] == 0 and
          payload_exact[5] == exact.shape[0] - 1)

    two_glyphs = np.zeros((150, 400), np.uint8)
    cv2.rectangle(two_glyphs, (40, 50), (140, 100), 255, -1)    # left glyph
    cv2.rectangle(two_glyphs, (260, 50), (360, 100), 255, -1)   # right glyph, symmetric
    status_amb, _, n_amb = _locate_target(two_glyphs, 400, ppm)
    check("two similarly-central glyphs are reported ambiguous, not guessed",
          status_amb == "ambiguous" and n_amb == 2)

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

    # --- Rule 7 manual-selection refinement on real photos ------------------
    # Not "no photo needed" like the checks above - skipped gracefully (not
    # counted as failed) when the file isn't present, so --self-test still
    # runs clean on a fresh clone before any demo photo is captured. Present
    # in this repository today, so they run for real here.
    def region_height(image_path, marker_mm, region, tag):
        if not os.path.exists(image_path):
            print(f"  skip  {tag} ({image_path} not found - not required for self-test)")
            return None
        r = rule7_measure_selected_region(image_path, marker_mm, 0, region, pdp_area_cm2=None)
        return r["measured_height_mm"]

    cookie_h1 = region_height("archive/experiments/cookie.jpg", 28, (2360, 1490, 380, 94),
                              "cookie.jpg former 4.70mm bug case")
    if cookie_h1 is not None:
        check("cookie.jpg: former 4.70mm bug case now measures ~2.8mm",
              abs(cookie_h1 - 2.8) < 0.15)
        cookie_h2 = region_height("archive/experiments/cookie.jpg", 28, (2385, 1508, 330, 62),
                                  "cookie.jpg tighter box")
        check("cookie.jpg: a tighter reasonable box converges to the same height",
              cookie_h2 is not None and abs(cookie_h1 - cookie_h2) < 0.15)

    walnutt_regions = [
        (370, 2760, 330, 110),
        (380, 2775, 300, 80),
        (390, 2780, 280, 70),
        (170, 2760, 730, 110),
    ]
    walnutt_heights = [region_height("demo/walnutt.jpg", 28, r, "walnutt.jpg region")
                       for r in walnutt_regions]
    if all(hh is not None for hh in walnutt_heights):
        check("walnutt.jpg: four differently-sized reasonable boxes all measure ~2.35mm",
              all(abs(hh - 2.35) < 0.05 for hh in walnutt_heights))

    print(f"\n{ok}/{total} checks passed")
    return ok == total


if __name__ == "__main__":
    main()

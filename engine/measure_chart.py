"""
measure_chart.py  —  PRAMAAN, THE GATE (no-printer edition)
============================================================
Takes your photograph of the test chart and answers one question:

    At what character height does your camera stop measuring accurately?

It finds the ArUco marker, derives pixels-per-millimetre, then measures every
text row and compares the result against what that row is known to be.

    python measure_chart.py photo.jpg
"""

import sys
import cv2
import numpy as np

MARKER_MM = 40.0
TEST_MM = [1.0, 1.5, 2.0, 3.0, 4.0, 6.0]
TOLERANCE_MM = 0.15      # what counts as a good enough measurement for Rule 7


def find_marker(bgr):
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    p = cv2.aruco.DetectorParameters()
    p.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    corners, ids, _ = cv2.aruco.ArucoDetector(d, p).detectMarkers(bgr)
    if ids is None or len(ids) == 0:
        return None
    return corners[0].reshape(4, 2)


def rectify(bgr, pts, out_ppm=20.0):
    """Flatten the chart plane so 1 mm == out_ppm px everywhere."""
    side = MARKER_MM * out_ppm
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


def text_rows(rect, ppm):
    """
    Find the 'MRP 45.00' rows and measure each one's height.
    Rows are grouped by connected components merged horizontally.
    """
    g = cv2.cvtColor(rect, cv2.COLOR_BGR2GRAY)
    th = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY_INV, 31, 12)
    # join characters into words/lines but do not bridge separate rows
    ker = cv2.getStructuringElement(cv2.MORPH_RECT, (int(3 * ppm), 1))
    joined = cv2.dilate(th, ker, iterations=1)
    cnts, _ = cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # rectify() translates the frame so nothing is clipped, so the marker corner
    # is NOT at the origin afterwards. Re-detect it to find the true origin.
    # On the chart the marker sits at 8 mm and the MRP rows start at 62 mm, so
    # rows begin (62 - 8) = 54 mm right of the marker's left edge. Filtering on
    # that band drops the grey "1.0 mm" labels and the marker caption, which
    # would otherwise be counted as rows and shift every pairing.
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
        if not (x_lo <= x <= x_hi):             # wrong column
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


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: python measure_chart.py yourphoto.jpg")
    bgr = cv2.imread(sys.argv[1])
    if bgr is None:
        sys.exit(f"could not read {sys.argv[1]}")

    H, W = bgr.shape[:2]
    pts = find_marker(bgr)
    if pts is None:
        sys.exit("NO MARKER FOUND.\n"
                 "  - is the whole black square visible in the photo?\n"
                 "  - is the photo sharp? tap to focus before shooting\n"
                 "  - reduce screen glare; tilt the screen slightly")

    sides = [np.linalg.norm(pts[i] - pts[(i + 1) % 4]) for i in range(4)]
    ppm_raw = (sum(sides) / 4) / MARKER_MM
    spread = (max(sides) - min(sides)) / max(sides) * 100

    print(f"photo         : {W} x {H} px  ({W*H/1e6:.1f} MP)")
    print(f"marker found  : yes")
    print(f"tilt spread   : {spread:.1f} %  "
          f"({'ok' if spread < 8 else 'RESHOOT - too angled'})")
    print(f"CAPTURE SCALE : {ppm_raw:.1f} px per mm")
    print()

    rect, ppm = rectify(bgr, pts)
    rows, _ = text_rows(rect, ppm)

    print(f"found {len(rows)} text rows (expected {len(TEST_MM)})")
    print()
    print(f"{'true mm':>8} {'measured':>10} {'error':>9}   verdict")
    print("-" * 46)

    # rows come out top-to-bottom, same order the chart was drawn
    ok_floor = None
    for true_mm, (x, y, w, h) in zip(TEST_MM, rows):
        meas = h / ppm
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
    print(f"  capture scale was {ppm_raw:.1f} px/mm. Roughly 20 px/mm or better")
    print("  is where 1 mm text becomes comfortable to measure AND read.")


if __name__ == "__main__":
    main()

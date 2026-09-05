"use client";

import { useCallback, useRef, useState } from "react";
import { CapturePanel } from "@/components/capture-panel";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  getRule7Candidates,
  getRule7RegionMeasurement,
  inspectPack,
  ScanError,
  type CandidatesResponse,
  type InspectionResponse,
  type Rule7Region,
  type Rule7Result,
  type ScanResponse,
} from "@/lib/api";

interface Rule7PanelProps {
  /**
   * The already-computed Rule 6 result for this pack, passed straight
   * through to inspectPack() as rule6Result instead of the original photo.
   * Rule 6 already ran once, on the initial scan — this lets adding a Rule
   * 7 measurement skip a second, identical VLM extraction.
   */
  rule6Result: ScanResponse;
  /**
   * The MRP value Rule 6 already read from the package photo, shown as a
   * plain-text hint on the candidate/selection screens below — nothing
   * more. It does not select, filter, or validate any candidate; it only
   * gives the inspector a concrete printed value to check against before
   * they tap or drag, since the automatic detector has no idea which box
   * is legally the MRP (see rule7_candidates_all_orientations()'s
   * docstring in engine/measure_chart.py). Purely additive: omitting it
   * leaves this component's behavior exactly as it was.
   */
  rule6MrpValue?: string | null;
  /**
   * The history record the initial Rule 6 scan was saved as. Sent back to
   * POST /inspect so this measurement UPDATES that record instead of
   * writing a second row holding only a Rule 7 photo — one physical pack
   * stays one inspection, with one set of evidence images.
   *
   * Null when the initial scan could not be persisted. The measurement
   * still runs and still returns a verdict; the server simply creates a
   * fresh record for it rather than updating one that does not exist.
   */
  inspectionId?: number | null;
  onComplete: (inspection: InspectionResponse) => void;
  onCancel: () => void;
}

type Phase =
  | { kind: "capture" }
  | { kind: "loading-candidates" }
  | { kind: "select"; image: Blob; filename: string; candidates: CandidatesResponse }
  | { kind: "submitting" }
  | { kind: "error"; message: string };

/** Automatic candidate picking is the primary path; manual precision
 * selection is the fallback for when no automatic candidate cleanly
 * bounds the complete numeral. */
type SelectionMode = "auto" | "manual_region";

const DEFAULT_MARKER_MM = 28;
/** Rectangle currently being dragged, in CSS pixels relative to the image element. */
interface DragRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

/**
 * Guides an inspector through the Rule 7 capture-and-measure step.
 *
 * Two selection mechanisms exist, neither ever presented as an "index" or
 * internal detail to the inspector:
 *   - auto: tap a detected candidate box (measure_chart.rule7_result()'s
 *     target_index — an internal row number, resolved here from a tap).
 *   - manual_region: drag a box around the complete MRP numeral when no
 *     automatic candidate is clean (measure_chart.rule7_measure_selected_
 *     region() — the rectangle is never itself the measurement; the
 *     backend trims it to the actual printed ink extent). This is the
 *     FALLBACK path, used only when automatic discovery didn't work -
 *     see this component's mode toggle below.
 */
export function Rule7Panel({
  rule6Result,
  rule6MrpValue,
  inspectionId,
  onComplete,
  onCancel,
}: Rule7PanelProps) {
  const [phase, setPhase] = useState<Phase>({ kind: "capture" });
  const [markerMm, setMarkerMm] = useState(DEFAULT_MARKER_MM);
  const [pdpArea, setPdpArea] = useState("");
  const [container, setContainer] = useState<"normal" | "blown">("normal");
  const [mode, setMode] = useState<SelectionMode>("auto");

  // auto mode
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);

  // manual_region mode
  const [dragOrigin, setDragOrigin] = useState<{ x: number; y: number } | null>(null);
  const [dragRect, setDragRect] = useState<DragRect | null>(null);
  const [region, setRegion] = useState<Rule7Region | null>(null);
  const [regionPreview, setRegionPreview] = useState<Rule7Result | null>(null);
  const [regionPreviewLoading, setRegionPreviewLoading] = useState(false);

  const imgRef = useRef<HTMLImageElement>(null);

  const handleRule7Image = useCallback(
    async (image: Blob, filename: string) => {
      setPhase({ kind: "loading-candidates" });
      try {
        const candidates = await getRule7Candidates(image, filename, markerMm);
        setSelectedIndex(candidates.rows.length === 1 ? 0 : null);
        setPhase({ kind: "select", image, filename, candidates });
      } catch (err) {
        console.error("PRAMAAN Rule 7 candidate read failed:", err);
        setPhase({
          kind: "error",
          message:
            err instanceof ScanError
              ? err.friendlyMessage
              : "Could not read the calibration photo. Please retry with a clearer image.",
        });
      }
    },
    [markerMm]
  );

  const switchToManualRegion = useCallback(() => {
    setMode("manual_region");
    setRegion(null);
    setRegionPreview(null);
    setDragRect(null);
  }, []);

  const switchToAuto = useCallback(() => {
    setMode("auto");
    setRegion(null);
    setRegionPreview(null);
    setDragRect(null);
  }, []);

  const handleImageClick = useCallback(
    (e: React.MouseEvent<HTMLImageElement>) => {
      if (mode !== "auto" || phase.kind !== "select") return;
      const img = imgRef.current;
      if (!img) return;
      const rect = img.getBoundingClientRect();
      const scaleX = img.naturalWidth / rect.width;
      const scaleY = img.naturalHeight / rect.height;
      const x = (e.clientX - rect.left) * scaleX;
      const y = (e.clientY - rect.top) * scaleY;

      const pad = 0.15;
      const hit = phase.candidates.rows.findIndex((r) => {
        const padX = r.w * pad;
        const padY = r.h * pad;
        return (
          x >= r.x - padX && x <= r.x + r.w + padX && y >= r.y - padY && y <= r.y + r.h + padY
        );
      });
      if (hit >= 0) setSelectedIndex(hit);
    },
    [mode, phase]
  );

  const handleDragStart = useCallback(
    (e: React.MouseEvent<HTMLImageElement>) => {
      if (mode !== "manual_region") return;
      const img = imgRef.current;
      if (!img) return;
      const rect = img.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      setDragOrigin({ x, y });
      setDragRect({ left: x, top: y, width: 0, height: 0 });
      setRegionPreview(null);
    },
    [mode]
  );

  const handleDragMove = useCallback(
    (e: React.MouseEvent<HTMLImageElement>) => {
      if (mode !== "manual_region" || !dragOrigin) return;
      const img = imgRef.current;
      if (!img) return;
      const rect = img.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      setDragRect({
        left: Math.min(dragOrigin.x, x),
        top: Math.min(dragOrigin.y, y),
        width: Math.abs(x - dragOrigin.x),
        height: Math.abs(y - dragOrigin.y),
      });
    },
    [mode, dragOrigin]
  );

  const handleDragEnd = useCallback(async () => {
    if (mode !== "manual_region" || !dragOrigin || phase.kind !== "select") {
      setDragOrigin(null);
      return;
    }
    setDragOrigin(null);
    const img = imgRef.current;
    if (!img || !dragRect || dragRect.width < 5 || dragRect.height < 5) {
      setDragRect(null);
      return;
    }

    const displayRect = img.getBoundingClientRect();
    const scaleX = img.naturalWidth / displayRect.width;
    const scaleY = img.naturalHeight / displayRect.height;
    const naturalRegion: Rule7Region = {
      x: dragRect.left * scaleX,
      y: dragRect.top * scaleY,
      w: dragRect.width * scaleX,
      h: dragRect.height * scaleY,
    };
    setRegion(naturalRegion);
    setDragRect(null);
    setRegionPreviewLoading(true);
    try {
      // rotation_deg 0: the only orientation the automatic candidate photo
      // is shown in today (see rule7_candidates_all_orientations() for the
      // separately-existing, not-yet-wired multi-orientation picker).
      // No PDP area is sent here - this is a preview of the measured
      // height only, mirroring how the automatic candidate list never
      // computes a verdict either; the verdict is always resolved fresh at
      // final submit.
      const preview = await getRule7RegionMeasurement(
        phase.image,
        phase.filename,
        markerMm,
        0,
        naturalRegion
      );
      setRegionPreview(preview);
    } catch (err) {
      console.error("PRAMAAN manual region preview failed:", err);
      setRegionPreview(null);
    } finally {
      setRegionPreviewLoading(false);
    }
  }, [mode, dragOrigin, dragRect, phase, markerMm]);

  const handleSubmit = useCallback(async () => {
    if (phase.kind !== "select") return;
    const areaNum = Number(pdpArea);
    if (!pdpArea || !Number.isFinite(areaNum) || areaNum <= 0) return;
    if (mode === "auto" && selectedIndex === null) return;
    if (mode === "manual_region" && (!region || regionPreview?.measured_height_mm == null)) return;

    setPhase({ kind: "submitting" });
    try {
      const inspection = await inspectPack({
        rule6Result,
        inspectionId,
        rule7Image: phase.image,
        rule7Filename: phase.filename,
        markerMm,
        pdpAreaCm2: areaNum,
        container,
        ...(mode === "manual_region"
          ? { region: region!, rotationDeg: 0 }
          : { targetIndex: selectedIndex! }),
      });
      onComplete(inspection);
    } catch (err) {
      console.error("PRAMAAN Rule 7 verdict failed:", err);
      setPhase({
        kind: "error",
        message:
          err instanceof ScanError
            ? err.friendlyMessage
            : "Analysis could not be completed. Please retry with a clearer image.",
      });
    }
  }, [
    phase,
    mode,
    selectedIndex,
    region,
    regionPreview,
    pdpArea,
    container,
    markerMm,
    rule6Result,
    inspectionId,
    onComplete,
  ]);

  if (phase.kind === "capture") {
    return (
      <Card className="w-full max-w-md mx-auto">
        <CardHeader>
          <CardTitle>Rule 7 measurement (optional)</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <p className="text-sm text-muted-foreground">
            Rule 6 already ran from the package photo alone. This step adds a precise,
            physical measurement of the printed price&apos;s character height — it
            requires a calibrated reference photo, not just any picture of the pack.
            Photograph a known-size marker together with the MRP declaration in one
            tight frame; the marker gives the camera a real-world scale to measure
            against.
          </p>
          <CapturePanel onImageReady={handleRule7Image} />
          <Button variant="ghost" onClick={onCancel}>
            Skip Rule 7 measurement
          </Button>
        </CardContent>
      </Card>
    );
  }

  if (phase.kind === "loading-candidates" || phase.kind === "submitting") {
    return (
      <div className="w-full max-w-md mx-auto text-center text-sm text-muted-foreground py-8">
        {phase.kind === "loading-candidates" ? "Reading the marker…" : "Computing verdict…"}
      </div>
    );
  }

  if (phase.kind === "error") {
    return (
      <div className="flex flex-col gap-4 w-full max-w-md mx-auto">
        <Alert variant="destructive">
          <AlertTitle>Rule 7 measurement failed</AlertTitle>
          <AlertDescription>{phase.message}</AlertDescription>
        </Alert>
        <Button onClick={() => setPhase({ kind: "capture" })}>Try again</Button>
        <Button variant="ghost" onClick={onCancel}>
          Skip Rule 7 measurement
        </Button>
      </div>
    );
  }

  // phase.kind === "select"
  const areaNum = Number(pdpArea);
  const pdpValid = pdpArea !== "" && Number.isFinite(areaNum) && areaNum > 0;
  const canSubmit =
    pdpValid &&
    (mode === "auto" ? selectedIndex !== null : regionPreview?.measured_height_mm != null);

  // What image to display: in manual_region mode, once a preview has been
  // measured, show ITS overlay (raw selection + trimmed ink extent) so the
  // inspector sees the real evidence, not just their own drag rectangle.
  const displayedImageBase64 =
    mode === "manual_region" && regionPreview?.overlay_png_base64
      ? regionPreview.overlay_png_base64
      : phase.candidates.overlay_png_base64;

  return (
    <Card className="w-full max-w-2xl mx-auto">
      <CardHeader>
        <CardTitle>
          {mode === "auto" ? "Select the MRP price on the photo" : "Manual precision selection"}
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {mode === "auto" ? (
          <p className="text-sm text-muted-foreground">
            Tap directly on the printed price. Every other box is just what the camera
            picked up as text-shaped — only the one you tap is measured against Rule 7.
          </p>
        ) : (
          <p className="text-sm text-muted-foreground">
            Drag a box around the <strong>complete MRP numeral</strong> — not a single
            digit, and not the whole price row. The system measures the actual printed
            height inside your selection; the box you draw is only a guide, never the
            measurement itself.
          </p>
        )}

        {rule6MrpValue && (
          <p className="text-sm font-medium text-foreground/80">
            Rule 6 detected MRP: ₹{rule6MrpValue} — confirm you are selecting this value.
          </p>
        )}

        <div className="relative w-full select-none">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            ref={imgRef}
            src={`data:image/png;base64,${displayedImageBase64}`}
            alt={mode === "auto" ? "Detected measurement candidates" : "Draw a selection"}
            onClick={handleImageClick}
            onMouseDown={handleDragStart}
            onMouseMove={handleDragMove}
            onMouseUp={handleDragEnd}
            onMouseLeave={() => dragOrigin && handleDragEnd()}
            draggable={false}
            className={`w-full rounded-lg border ${
              mode === "auto" ? "cursor-crosshair" : "cursor-crosshair"
            }`}
          />
          {mode === "manual_region" && dragRect && (
            <div
              className="pointer-events-none absolute border-2 border-orange-500 bg-orange-500/10"
              style={{
                left: dragRect.left,
                top: dragRect.top,
                width: dragRect.width,
                height: dragRect.height,
              }}
            />
          )}
        </div>

        {mode === "auto" && selectedIndex === null && (
          <p className="text-sm text-amber-600">Tap the price to select it.</p>
        )}
        {mode === "auto" && selectedIndex !== null && (
          <p className="text-sm text-green-700">
            Selected — measured {phase.candidates.rows[selectedIndex].height_mm.toFixed(2)} mm.
          </p>
        )}
        {mode === "auto" && (
          <button
            type="button"
            onClick={switchToManualRegion}
            className="self-start text-sm text-muted-foreground underline"
          >
            None of these boxes cleanly show the complete price — use manual precision
            selection instead.
          </button>
        )}

        {mode === "manual_region" && regionPreviewLoading && (
          <p className="text-sm text-muted-foreground">Measuring selection…</p>
        )}
        {mode === "manual_region" && !regionPreviewLoading && regionPreview?.measured_height_mm != null && (
          <p className="text-sm text-green-700">
            Selected — measured {regionPreview.measured_height_mm.toFixed(2)} mm.
          </p>
        )}
        {mode === "manual_region" &&
          !regionPreviewLoading &&
          regionPreview &&
          regionPreview.measured_height_mm == null &&
          regionPreview.problem && (
            <p className="text-sm text-amber-600">{regionPreview.problem}</p>
          )}
        {mode === "manual_region" && !regionPreviewLoading && !regionPreview && (
          <p className="text-sm text-amber-600">Drag a box around the complete MRP numeral.</p>
        )}
        {mode === "manual_region" && (
          <button
            type="button"
            onClick={switchToAuto}
            className="self-start text-sm text-muted-foreground underline"
          >
            Back to automatic detection
          </button>
        )}

        <div className="grid grid-cols-2 gap-3">
          <label className="flex flex-col gap-1 text-sm">
            Principal display panel area (cm²)
            <input
              type="number"
              min="0"
              step="0.1"
              value={pdpArea}
              onChange={(e) => setPdpArea(e.target.value)}
              className="rounded-md border px-2 py-1"
              placeholder="e.g. 77"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            Container
            <select
              value={container}
              onChange={(e) => setContainer(e.target.value as "normal" | "blown")}
              className="rounded-md border px-2 py-1"
            >
              <option value="normal">Normal print</option>
              <option value="blown">Blown / formed / molded</option>
            </select>
          </label>
        </div>
        <label className="flex flex-col gap-1 text-sm">
          Printed marker size (mm)
          <input
            type="number"
            min="1"
            step="1"
            value={markerMm}
            onChange={(e) => setMarkerMm(Number(e.target.value) || DEFAULT_MARKER_MM)}
            className="rounded-md border px-2 py-1 w-32"
          />
        </label>

        <div className="flex gap-3">
          <Button onClick={handleSubmit} disabled={!canSubmit}>
            Get Rule 7 verdict
          </Button>
          <Button variant="ghost" onClick={onCancel}>
            Skip Rule 7 measurement
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

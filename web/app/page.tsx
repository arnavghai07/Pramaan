"use client";

import { useCallback, useState } from "react";
import Link from "next/link";
import { RequireAuth } from "@/components/auth-provider";
import { CapturePanel } from "@/components/capture-panel";
import { ResultsPanel } from "@/components/results-panel";
import { ResultsSkeleton } from "@/components/results-skeleton";
import { Rule7Panel } from "@/components/rule7-panel";
import { StatusBanner } from "@/components/status-banner";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { inspectPack, ScanError, type InspectionResponse } from "@/lib/api";

type Status =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | {
      kind: "done";
      inspection: InspectionResponse;
      addingRule7: boolean;
    };

const RULE7_VERDICT_STYLE: Record<
  "PASS" | "FAIL" | "REVIEW",
  { label: string; className: string }
> = {
  PASS: { label: "PASS", className: "text-green-700" },
  FAIL: { label: "FAIL", className: "text-red-700" },
  REVIEW: { label: "NEEDS MANUAL REVIEW", className: "text-amber-700" },
};

/**
 * Explicit Rule 7 readout for the final results screen. The evidence overlay
 * image carries the same numbers baked into its pixels, but a judge should
 * never have to zoom into a photo to find the verdict — CLAUDE.md rule 2,
 * "silence is never a pass", extended to "don't make the reader dig for it".
 *
 * Handles all three states a real scan can be in, and is careful not to
 * conflate "not measured" with "non-compliant": no calibration photo, or a
 * calibration photo the marker pipeline rejected (not found / too tilted),
 * both read as "not assessed" — never as a violation. Only a resolved
 * PASS/FAIL/REVIEW verdict from the (untouched) Rule 7 engine is shown as
 * such.
 */
function Rule7Readout({ rule7 }: { rule7: InspectionResponse["rule7"] }) {
  if (!rule7) {
    return (
      <div className="w-full max-w-2xl mx-auto text-sm text-muted-foreground">
        Rule 7 physical measurement was not performed for this scan — no calibrated
        reference photo was supplied. This does not affect the Rule 6 result above.
      </div>
    );
  }

  if (rule7.problem) {
    return (
      <div className="w-full max-w-2xl mx-auto flex flex-col gap-2">
        <h2 className="font-medium text-sm text-muted-foreground">
          Rule 7 — physical measurement
        </h2>
        <p className="text-sm text-amber-700">
          Not assessed — the calibration photo could not be used: {rule7.problem}
        </p>
      </div>
    );
  }

  if (rule7.verdict == null) {
    return (
      <div className="w-full max-w-2xl mx-auto text-sm text-muted-foreground">
        Rule 7 physical measurement: a calibration photo was supplied but no
        measurement target has been selected yet.
      </div>
    );
  }

  const style = RULE7_VERDICT_STYLE[rule7.verdict];
  const height = rule7.measured_height_mm;
  const threshold = rule7.threshold_mm;
  const margin = height != null && threshold != null ? height - threshold : null;

  return (
    <div className="w-full max-w-2xl mx-auto flex flex-col gap-2">
      <h2 className="font-medium text-sm text-muted-foreground">
        Rule 7 — physical measurement
      </h2>
      <div className="rounded-lg border px-4 py-3 flex flex-col gap-1">
        <span className={`font-semibold ${style.className}`}>
          Rule 7 verdict: {style.label}
        </span>
        {height != null && threshold != null && (
          <span className="text-sm text-muted-foreground">
            Measured {height.toFixed(2)} mm against a {threshold.toFixed(1)} mm required
            minimum
            {margin != null && (
              <>
                {" "}
                ({margin >= 0 ? "+" : ""}
                {margin.toFixed(2)} mm {margin >= 0 ? "above" : "below"} the minimum)
              </>
            )}
          </span>
        )}
      </div>
      {rule7.overlay_png_base64 && (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={`data:image/png;base64,${rule7.overlay_png_base64}`}
          alt="Rule 7 measurement evidence"
          className="w-full rounded-lg border"
        />
      )}
    </div>
  );
}

export default function Home() {
  // The guard is a convenience for the user, not the security boundary —
  // /scan and /inspect are protected in FastAPI regardless (api/auth.py).
  return (
    <RequireAuth>
      <ScannerPage />
    </RequireAuth>
  );
}

function ScannerPage() {
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  const handleImageReady = useCallback(async (image: Blob, filename: string) => {
    setStatus({ kind: "loading" });
    try {
      const inspection = await inspectPack({ rule6Image: image, rule6Filename: filename });
      setStatus({
        kind: "done",
        inspection,
        addingRule7: false,
      });
    } catch (err) {
      // The technical detail (raw backend/model error) stays in the console
      // for debugging; the screen only ever shows a short, professional
      // message — CLAUDE.md's spirit extended to error UX, not just verdicts.
      console.error("PRAMAAN scan failed:", err);
      const message =
        err instanceof ScanError
          ? err.friendlyMessage
          : "Analysis could not be completed. Please retry with a clearer image.";
      setStatus({ kind: "error", message });
    }
  }, []);

  const reset = useCallback(() => setStatus({ kind: "idle" }), []);

  const startRule7 = useCallback(() => {
    setStatus((prev) => (prev.kind === "done" ? { ...prev, addingRule7: true } : prev));
  }, []);

  const cancelRule7 = useCallback(() => {
    setStatus((prev) => (prev.kind === "done" ? { ...prev, addingRule7: false } : prev));
  }, []);

  const completeRule7 = useCallback((inspection: InspectionResponse) => {
    setStatus((prev) =>
      prev.kind === "done" ? { ...prev, inspection, addingRule7: false } : prev
    );
  }, []);

  return (
    <div className="min-h-screen px-4 py-10 sm:px-8">
      <header className="mb-8 text-center">
        <h1 className="text-2xl font-bold">New inspection</h1>
        <p className="text-muted-foreground text-sm">
          Legal Metrology compliance scanner
        </p>
      </header>

      <main>
        {status.kind === "idle" && (
          <>
            <div className="mx-auto mb-6 flex w-full max-w-md flex-col gap-3 text-center text-sm text-muted-foreground">
              <p>
                PRAMAAN reads packaged-commodity declarations, applies deterministic
                Legal Metrology checks, and — when a calibrated reference photo is
                supplied — physically measures declaration dimensions.
              </p>
              <p className="flex flex-wrap items-center justify-center gap-x-1.5 font-medium text-foreground/80">
                <span>Declarations</span>
                <span aria-hidden>→</span>
                <span>Rules</span>
                <span aria-hidden>→</span>
                <span>Physical measurement</span>
                <span aria-hidden>→</span>
                <span>Evidence</span>
                <span aria-hidden>→</span>
                <span>Verdict</span>
              </p>
            </div>
            <CapturePanel onImageReady={handleImageReady} />
          </>
        )}

        {status.kind === "loading" && (
          <>
            <CapturePanel onImageReady={handleImageReady} disabled />
            <div className="mt-8">
              <ResultsSkeleton />
            </div>
          </>
        )}

        {status.kind === "error" && (
          <div className="flex flex-col gap-4 w-full max-w-md mx-auto">
            <Alert variant="destructive">
              <AlertTitle>Scan failed</AlertTitle>
              <AlertDescription>{status.message}</AlertDescription>
            </Alert>
            <Button onClick={reset}>Try again</Button>
          </div>
        )}

        {status.kind === "done" && status.addingRule7 && (
          <Rule7Panel
            rule6Result={status.inspection.rule6}
            rule6MrpValue={
              // Plain-text hint only — see Rule7Panel's prop doc comment.
              // Rule 6 already extracted this; nothing new is computed here.
              typeof status.inspection.rule6.fields.mrp_value === "string" ||
              typeof status.inspection.rule6.fields.mrp_value === "number"
                ? String(status.inspection.rule6.fields.mrp_value)
                : null
            }
            inspectionId={status.inspection.inspection_id}
            onComplete={completeRule7}
            onCancel={cancelRule7}
          />
        )}

        {status.kind === "done" && !status.addingRule7 && (
          <div className="flex flex-col gap-6">
            <div className="w-full max-w-2xl mx-auto">
              <StatusBanner
                status={status.inspection.overall_status}
                findings={status.inspection.findings}
              />
            </div>
            <ResultsPanel result={status.inspection.rule6} />

            <Rule7Readout rule7={status.inspection.rule7} />

            <div className="w-full max-w-2xl mx-auto flex flex-col gap-3">
              {!status.inspection.rule7 && (
                <div className="flex flex-col gap-2">
                  <p className="text-xs text-muted-foreground">
                    Rule 6 works from the package photo alone. Rule 7&apos;s physical
                    measurement is an optional extra step that additionally requires a
                    calibrated reference photo (this prototype uses a known-size ArUco
                    marker photographed next to the printed price).
                  </p>
                  <Button variant="outline" onClick={startRule7} className="w-full">
                    Add Rule 7 measurement (calibrated)
                  </Button>
                </div>
              )}
              {status.inspection.inspection_id != null ? (
                <Button
                  variant="outline"
                  className="w-full"
                  nativeButton={false}
                  render={
                    <Link href={`/inspections/${status.inspection.inspection_id}`} />
                  }
                >
                  View saved record #{status.inspection.inspection_id}
                </Button>
              ) : (
                // inspection_id is null only when the server could not write
                // the record. The verdict above is unaffected and still
                // stands; saying so beats a link that would 404.
                <p className="text-xs text-amber-700">
                  This inspection was completed but could not be saved to
                  history. The verdict above is unaffected.
                </p>
              )}
              <Button variant="outline" onClick={reset} className="w-full">
                Scan another pack
              </Button>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

"use client";

import { useCallback, useState } from "react";
import { CapturePanel } from "@/components/capture-panel";
import { ResultsPanel } from "@/components/results-panel";
import { ResultsSkeleton } from "@/components/results-skeleton";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { scanImage, ScanError, type ScanResponse } from "@/lib/api";

type Status =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "done"; result: ScanResponse };

export default function Home() {
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  const handleImageReady = useCallback(async (image: Blob, filename: string) => {
    setStatus({ kind: "loading" });
    try {
      const result = await scanImage(image, filename);
      setStatus({ kind: "done", result });
    } catch (err) {
      const message =
        err instanceof ScanError ? err.message : "Unexpected error during scan.";
      setStatus({ kind: "error", message });
    }
  }, []);

  const reset = useCallback(() => setStatus({ kind: "idle" }), []);

  return (
    <div className="min-h-screen px-4 py-10 sm:px-8">
      <header className="mb-8 text-center">
        <h1 className="text-2xl font-bold">PRAMAAN</h1>
        <p className="text-muted-foreground text-sm">
          Legal Metrology compliance scanner
        </p>
      </header>

      <main>
        {status.kind === "idle" && (
          <CapturePanel onImageReady={handleImageReady} />
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

        {status.kind === "done" && (
          <div className="flex flex-col gap-6">
            <ResultsPanel result={status.result} />
            <div className="w-full max-w-2xl mx-auto">
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

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

interface CapturePanelProps {
  onImageReady: (image: Blob, filename: string) => void;
  disabled?: boolean;
}

/**
 * Camera preview is the primary path; the file input below it is always
 * rendered, not hidden behind a camera-failure branch. Per CLAUDE.md this is
 * the actual demo path — venue lighting and browser permission prompts on
 * stage are not something to depend on.
 */
export function CapturePanel({ onImageReady, disabled }: CapturePanelProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [cameraError, setCameraError] = useState<string | null>(null);
  const [cameraReady, setCameraReady] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function startCamera() {
      if (!navigator.mediaDevices?.getUserMedia) {
        setCameraError("This browser does not support camera capture.");
        return;
      }
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: "environment" },
          audio: false,
        });
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        streamRef.current = stream;
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          setCameraReady(true);
        }
      } catch {
        setCameraError("Camera unavailable or permission denied — use upload below.");
      }
    }

    startCamera();

    return () => {
      cancelled = true;
      streamRef.current?.getTracks().forEach((t) => t.stop());
    };
  }, []);

  const handleCapture = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(video, 0, 0);
    canvas.toBlob(
      (blob) => {
        if (blob) onImageReady(blob, "capture.jpg");
      },
      "image/jpeg",
      0.92
    );
  }, [onImageReady]);

  const handleFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) onImageReady(file, file.name);
      e.target.value = "";
    },
    [onImageReady]
  );

  return (
    <div className="flex flex-col gap-4 w-full max-w-md mx-auto">
      <div className="relative aspect-[4/3] w-full overflow-hidden rounded-lg bg-muted">
        {cameraError ? (
          <div className="absolute inset-0 flex items-center justify-center p-4">
            <Alert variant="destructive">
              <AlertTitle>Camera unavailable</AlertTitle>
              <AlertDescription>{cameraError}</AlertDescription>
            </Alert>
          </div>
        ) : (
          <>
            <video
              ref={videoRef}
              autoPlay
              playsInline
              muted
              className="h-full w-full object-cover"
            />
            {/* Framing guide — a static rect, not detection-driven. */}
            <div className="pointer-events-none absolute inset-6 rounded-md border-2 border-dashed border-white/70" />
          </>
        )}
      </div>

      <Button
        type="button"
        size="lg"
        onClick={handleCapture}
        disabled={disabled || !cameraReady}
      >
        Scan pack
      </Button>

      <div className="flex items-center gap-3 text-sm text-muted-foreground">
        <div className="h-px flex-1 bg-border" />
        or
        <div className="h-px flex-1 bg-border" />
      </div>

      <Button
        type="button"
        variant="outline"
        size="lg"
        disabled={disabled}
        onClick={() => fileInputRef.current?.click()}
      >
        Upload photo instead
      </Button>
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={handleFileChange}
      />
    </div>
  );
}

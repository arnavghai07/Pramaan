"use client";

import { useEffect, useState } from "react";
import { fetchEvidenceObjectUrl, type EvidenceKind } from "@/lib/api";

const KIND_LABEL: Record<EvidenceKind, string> = {
  rule6: "Rule 6 — declaration panel photograph",
  rule7: "Rule 7 — calibrated measurement photograph",
  overlay: "Rule 7 — measurement evidence overlay",
};

/**
 * Renders one stored evidence image.
 *
 * The image is fetched with the officer's bearer token and shown from a
 * blob URL, because a bare <img src> cannot send an Authorization header
 * and putting a token in the URL would leak it into history and proxy logs
 * (see fetchEvidenceObjectUrl in lib/api.ts).
 *
 * Missing evidence is a normal, expected state, not an error: a pack
 * inspected for declarations alone has no Rule 7 photograph, and a Rule 7
 * photo whose marker was rejected never produced an overlay. Those say so
 * plainly instead of showing a broken image.
 */
export function EvidenceImage({
  inspectionId,
  kind,
  available,
}: {
  inspectionId: number;
  kind: EvidenceKind;
  available: boolean;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!available) return;

    let objectUrl: string | null = null;
    let cancelled = false;

    fetchEvidenceObjectUrl(inspectionId, kind)
      .then((next) => {
        objectUrl = next;
        if (cancelled) {
          URL.revokeObjectURL(next);
          return;
        }
        setUrl(next);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });

    return () => {
      cancelled = true;
      // Blob URLs pin their data in memory until revoked, and these are
      // multi-megabyte photographs.
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [inspectionId, kind, available]);

  return (
    <figure className="flex flex-col gap-2">
      <figcaption className="text-xs font-medium text-muted-foreground">
        {KIND_LABEL[kind]}
      </figcaption>
      {!available ? (
        <div className="rounded-lg border border-dashed px-4 py-6 text-center text-sm text-muted-foreground">
          Not captured for this inspection.
        </div>
      ) : failed ? (
        <div className="rounded-lg border border-dashed px-4 py-6 text-center text-sm text-amber-700">
          This image is recorded but could not be loaded from evidence storage.
        </div>
      ) : url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={url} alt={KIND_LABEL[kind]} className="w-full rounded-lg border" />
      ) : (
        <div className="h-40 animate-pulse rounded-lg border bg-muted" />
      )}
    </figure>
  );
}

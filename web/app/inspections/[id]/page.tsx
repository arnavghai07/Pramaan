"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { RequireAuth, useAuth } from "@/components/auth-provider";
import { EvidenceImage } from "@/components/evidence-image";
import { ResultsPanel } from "@/components/results-panel";
import { StatusBanner } from "@/components/status-banner";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  deleteInspection,
  getInspection,
  ScanError,
  type InspectionDetail,
} from "@/lib/api";

const RULE7_STYLE: Record<"PASS" | "FAIL" | "REVIEW", { label: string; className: string }> = {
  PASS: { label: "PASS", className: "text-green-700" },
  FAIL: { label: "FAIL", className: "text-red-700" },
  REVIEW: { label: "NEEDS MANUAL REVIEW", className: "text-amber-700" },
};

export default function InspectionDetailPage() {
  return (
    <RequireAuth>
      <Detail />
    </RequireAuth>
  );
}

/**
 * One stored inspection, replayed.
 *
 * Everything on this page is read back from the database exactly as
 * engine/verdict.py decided it at the time. Nothing is recomputed here: a
 * record that was NEEDS_MANUAL_REVIEW in August still reads
 * NEEDS_MANUAL_REVIEW today, whatever the rules table says now. That is the
 * point of an evidence record.
 */
function Detail() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { user } = useAuth();

  const id = Number(params?.id);
  const [record, setRecord] = useState<InspectionDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  useEffect(() => {
    if (!Number.isInteger(id) || id <= 0) {
      setError("That is not a valid inspection number.");
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    getInspection(id)
      .then((data) => {
        if (!cancelled) setRecord(data);
      })
      .catch((err) => {
        console.error("PRAMAAN inspection load failed:", err);
        if (!cancelled) {
          setError(
            err instanceof ScanError
              ? err.message.includes("no stored inspection")
                ? `No inspection is stored with the number ${id}.`
                : err.friendlyMessage
              : "This inspection could not be loaded."
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  const handleDelete = useCallback(async () => {
    setDeleting(true);
    try {
      await deleteInspection(id);
      router.push("/inspections");
    } catch (err) {
      console.error("PRAMAAN delete failed:", err);
      setError(
        err instanceof ScanError
          ? err.friendlyMessage
          : "This inspection could not be deleted."
      );
      setDeleting(false);
      setConfirmingDelete(false);
    }
  }, [id, router]);

  if (loading) {
    return (
      <div className="mx-auto flex max-w-3xl flex-col gap-4 px-4 py-8 sm:px-8">
        <Skeleton className="h-8 w-56" />
        <Skeleton className="h-24 w-full rounded-lg" />
        <Skeleton className="h-64 w-full rounded-lg" />
      </div>
    );
  }

  if (error || !record) {
    return (
      <div className="mx-auto flex max-w-3xl flex-col gap-4 px-4 py-8 sm:px-8">
        <Alert variant="destructive">
          <AlertTitle>Inspection unavailable</AlertTitle>
          <AlertDescription>{error ?? "Unknown error."}</AlertDescription>
        </Alert>
        <Button variant="outline" nativeButton={false} render={<Link href="/inspections" />}>
          Back to history
        </Button>
      </div>
    );
  }

  const rule7 = record.rule7;
  const when = new Date(record.created_at);

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6 px-4 py-8 sm:px-8">
      <header className="flex flex-col gap-1">
        <Link
          href="/inspections"
          className="text-sm text-muted-foreground hover:underline"
        >
          ← Inspection history
        </Link>
        <h1 className="text-2xl font-bold">
          {record.product_name ?? record.manufacturer ?? "Unnamed pack"}
        </h1>
        <p className="text-sm text-muted-foreground">
          Inspection #{record.id} ·{" "}
          {Number.isNaN(when.getTime()) ? record.created_at : when.toLocaleString()}
          {record.inspector_name && <> · recorded by {record.inspector_name}</>}
        </p>
      </header>

      <StatusBanner status={record.overall_status} findings={record.findings} />

      <Card>
        <CardHeader>
          <CardTitle>Recorded values</CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
            <Row label="Manufacturer" value={record.manufacturer} />
            <Row
              label="MRP"
              value={record.mrp != null ? `₹${record.mrp.toFixed(2)}` : null}
            />
            <Row label="Net quantity" value={record.net_quantity} />
            <Row label="Manufacture date" value={record.mfg_date} />
          </dl>
        </CardContent>
      </Card>

      <ResultsPanel result={record.rule6} />

      <section className="flex flex-col gap-2">
        <h2 className="text-sm font-medium text-muted-foreground">
          Rule 7 — physical measurement
        </h2>
        {!rule7 ? (
          <p className="text-sm text-muted-foreground">
            Not performed for this inspection — no calibrated reference photo was
            supplied. This does not affect the Rule 6 result above.
          </p>
        ) : rule7.problem ? (
          <p className="text-sm text-amber-700">
            Not assessed — the calibration photo could not be used: {rule7.problem}
          </p>
        ) : rule7.verdict == null ? (
          <p className="text-sm text-muted-foreground">
            A calibration photo was recorded, but no measurement target was
            selected, so no Rule 7 verdict was issued.
          </p>
        ) : (
          <div className="flex flex-col gap-1 rounded-lg border px-4 py-3">
            <span className={`font-semibold ${RULE7_STYLE[rule7.verdict].className}`}>
              Rule 7 verdict: {RULE7_STYLE[rule7.verdict].label}
            </span>
            {rule7.measured_height_mm != null && rule7.threshold_mm != null && (
              <span className="text-sm text-muted-foreground">
                Measured {rule7.measured_height_mm.toFixed(2)} mm against a{" "}
                {rule7.threshold_mm.toFixed(1)} mm required minimum
              </span>
            )}
          </div>
        )}
      </section>

      <section className="flex flex-col gap-4">
        <h2 className="text-sm font-medium text-muted-foreground">Evidence</h2>
        <EvidenceImage
          inspectionId={record.id}
          kind="rule6"
          available={record.rule6_image_stored}
        />
        <EvidenceImage
          inspectionId={record.id}
          kind="overlay"
          available={record.rule7_overlay_stored}
        />
        <EvidenceImage
          inspectionId={record.id}
          kind="rule7"
          available={record.rule7_image_stored}
        />
      </section>

      {/* Deleting destroys the evidence behind an enforcement decision, so
          the control only appears for an administrator — and the server
          enforces the same rule regardless of what is rendered here. */}
      {user?.role === "ADMIN" && (
        <section className="flex flex-col gap-2 border-t pt-6">
          {confirmingDelete ? (
            <div className="flex flex-col gap-3 rounded-lg border border-destructive/40 p-4">
              <p className="text-sm">
                Permanently delete inspection #{record.id} and its stored evidence
                images? This cannot be undone.
              </p>
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="destructive"
                  disabled={deleting}
                  onClick={handleDelete}
                >
                  {deleting ? "Deleting…" : "Delete permanently"}
                </Button>
                <Button
                  variant="ghost"
                  disabled={deleting}
                  onClick={() => setConfirmingDelete(false)}
                >
                  Cancel
                </Button>
              </div>
            </div>
          ) : (
            <Button
              variant="destructive"
              className="self-start"
              onClick={() => setConfirmingDelete(true)}
            >
              Delete inspection
            </Button>
          )}
        </section>
      )}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="flex flex-col">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      {/* An absent value is a fact about the pack, not a blank to hide. */}
      <dd className={value ? "" : "text-muted-foreground"}>
        {value ?? "Not recorded"}
      </dd>
    </div>
  );
}

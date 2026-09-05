"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { RequireAuth } from "@/components/auth-provider";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  listInspections,
  ScanError,
  type InspectionSummary,
  type OverallStatus,
} from "@/lib/api";

const PAGE_SIZE = 20;

const STATUS_BADGE: Record<
  OverallStatus,
  { label: string; className: string }
> = {
  COMPLIANT: { label: "COMPLIANT", className: "bg-green-600 text-white" },
  NON_COMPLIANT: { label: "NON-COMPLIANT", className: "bg-red-600 text-white" },
  NEEDS_MANUAL_REVIEW: {
    label: "NEEDS MANUAL REVIEW",
    className: "bg-amber-500 text-white",
  },
};

function formatWhen(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export default function InspectionsPage() {
  return (
    <RequireAuth>
      <InspectionHistory />
    </RequireAuth>
  );
}

function InspectionHistory() {
  const router = useRouter();

  // Draft filter inputs, applied on submit rather than on every keystroke:
  // each change is a network round-trip, and a search box that refetches
  // per character makes the list flicker while an officer is still typing.
  const [draftQuery, setDraftQuery] = useState("");
  const [draftStatus, setDraftStatus] = useState<OverallStatus | "">("");
  const [draftFrom, setDraftFrom] = useState("");
  const [draftTo, setDraftTo] = useState("");

  const [applied, setApplied] = useState({
    q: "",
    status: "" as OverallStatus | "",
    dateFrom: "",
    dateTo: "",
  });
  const [offset, setOffset] = useState(0);

  const [items, setItems] = useState<InspectionSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [jumpId, setJumpId] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await listInspections({
        limit: PAGE_SIZE,
        offset,
        status: applied.status,
        q: applied.q,
        dateFrom: applied.dateFrom || undefined,
        dateTo: applied.dateTo || undefined,
      });
      setItems(page.items);
      setTotal(page.total);
    } catch (err) {
      console.error("PRAMAAN history load failed:", err);
      setError(
        err instanceof ScanError
          ? err.friendlyMessage
          : "Inspection history could not be loaded."
      );
    } finally {
      setLoading(false);
    }
  }, [applied, offset]);

  useEffect(() => {
    load();
  }, [load]);

  function applyFilters(event: React.FormEvent) {
    event.preventDefault();
    setOffset(0); // a new filter set starts at its own first page
    setApplied({
      q: draftQuery,
      status: draftStatus,
      dateFrom: draftFrom,
      dateTo: draftTo,
    });
  }

  function clearFilters() {
    setDraftQuery("");
    setDraftStatus("");
    setDraftFrom("");
    setDraftTo("");
    setOffset(0);
    setApplied({ q: "", status: "", dateFrom: "", dateTo: "" });
  }

  function jumpToId(event: React.FormEvent) {
    event.preventDefault();
    const id = Number(jumpId.trim());
    if (Number.isInteger(id) && id > 0) router.push(`/inspections/${id}`);
  }

  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const filtered =
    applied.q !== "" ||
    applied.status !== "" ||
    applied.dateFrom !== "" ||
    applied.dateTo !== "";

  return (
    <div className="mx-auto max-w-5xl px-4 py-8 sm:px-8">
      <header className="mb-6">
        <h1 className="text-2xl font-bold">Inspection history</h1>
        <p className="text-sm text-muted-foreground">
          Every completed inspection, with its stored declarations, measurement and
          evidence.
        </p>
      </header>

      <div className="mb-6 flex flex-col gap-4 rounded-lg border p-4">
        <form onSubmit={applyFilters} className="flex flex-col gap-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="q">Product or manufacturer</Label>
              <Input
                id="q"
                value={draftQuery}
                placeholder="e.g. VMG Foods"
                onChange={(e) => setDraftQuery(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="status">Verdict</Label>
              <Select
                id="status"
                value={draftStatus}
                onChange={(e) => setDraftStatus(e.target.value as OverallStatus | "")}
              >
                <option value="">All verdicts</option>
                <option value="COMPLIANT">Compliant</option>
                <option value="NON_COMPLIANT">Non-compliant</option>
                <option value="NEEDS_MANUAL_REVIEW">Needs manual review</option>
              </Select>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="from">From date</Label>
              <Input
                id="from"
                type="date"
                value={draftFrom}
                onChange={(e) => setDraftFrom(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="to">To date</Label>
              <Input
                id="to"
                type="date"
                value={draftTo}
                onChange={(e) => setDraftTo(e.target.value)}
              />
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button type="submit">Apply filters</Button>
            {filtered && (
              <Button type="button" variant="ghost" onClick={clearFilters}>
                Clear
              </Button>
            )}
          </div>
        </form>

        <form
          onSubmit={jumpToId}
          className="flex flex-wrap items-end gap-2 border-t pt-4"
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="jump">Go to inspection ID</Label>
            <Input
              id="jump"
              inputMode="numeric"
              className="w-40"
              placeholder="e.g. 12"
              value={jumpId}
              onChange={(e) => setJumpId(e.target.value)}
            />
          </div>
          <Button type="submit" variant="outline">
            Open
          </Button>
        </form>
      </div>

      {error && (
        <Alert variant="destructive" className="mb-4">
          <AlertTitle>Could not load history</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {loading ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-20 w-full rounded-lg" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-lg border border-dashed px-6 py-12 text-center">
          <p className="text-sm text-muted-foreground">
            {filtered
              ? "No inspections match these filters."
              : "No inspections recorded yet."}
          </p>
          {!filtered && (
            <Button className="mt-4" nativeButton={false} render={<Link href="/" />}>
              Start an inspection
            </Button>
          )}
        </div>
      ) : (
        <ul className="flex flex-col gap-2">
          {items.map((item) => {
            const badge = STATUS_BADGE[item.overall_status];
            return (
              <li key={item.id}>
                <Link
                  href={`/inspections/${item.id}`}
                  className="flex flex-col gap-2 rounded-lg border px-4 py-3 transition-colors hover:bg-muted"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-xs text-muted-foreground">
                      #{item.id}
                    </span>
                    <span className="font-medium">
                      {item.product_name ?? item.manufacturer ?? "Unnamed pack"}
                    </span>
                    <Badge className={`ml-auto ${badge.className}`}>
                      {badge.label}
                    </Badge>
                  </div>
                  <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                    <span>{formatWhen(item.created_at)}</span>
                    <span>
                      Rule 6: {item.mandatory_present}/{item.mandatory_total} mandatory
                    </span>
                    <span>
                      Rule 7:{" "}
                      {item.has_rule7
                        ? (item.rule7_verdict ?? "no target selected")
                        : "not measured"}
                    </span>
                    {item.mrp != null && <span>MRP ₹{item.mrp.toFixed(2)}</span>}
                    {item.net_quantity && <span>{item.net_quantity}</span>}
                    {item.inspector_name && <span>by {item.inspector_name}</span>}
                  </div>
                </Link>
              </li>
            );
          })}
        </ul>
      )}

      {total > PAGE_SIZE && (
        <div className="mt-6 flex items-center justify-between gap-4">
          <Button
            variant="outline"
            disabled={offset === 0 || loading}
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
          >
            Previous
          </Button>
          <span className="text-sm text-muted-foreground">
            Page {page} of {pages} · {total} inspection{total === 1 ? "" : "s"}
          </span>
          <Button
            variant="outline"
            disabled={offset + PAGE_SIZE >= total || loading}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            Next
          </Button>
        </div>
      )}
    </div>
  );
}

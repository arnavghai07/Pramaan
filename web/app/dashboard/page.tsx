"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { RequireAuth, useAuth } from "@/components/auth-provider";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  getDashboard,
  ScanError,
  type DashboardResponse,
  type OverallStatus,
} from "@/lib/api";

/**
 * Enforcement dashboard.
 *
 * READS ONLY. Every figure on this page arrives already counted from
 * GET /dashboard, which aggregates stored verdicts in SQL. Nothing here
 * fetches inspection history to add it up in the browser, and nothing here
 * derives a compliance status — a page that recomputed a verdict would be a
 * second source of truth for the thing PRAMAAN exists to state once.
 */

const STATUS_STYLE: Record<
  OverallStatus,
  { label: string; badge: string; bar: string }
> = {
  COMPLIANT: {
    label: "Compliant",
    badge: "bg-green-600 text-white",
    bar: "bg-green-600",
  },
  NON_COMPLIANT: {
    label: "Non-compliant",
    badge: "bg-red-600 text-white",
    bar: "bg-red-600",
  },
  NEEDS_MANUAL_REVIEW: {
    label: "Needs manual review",
    badge: "bg-amber-500 text-white",
    bar: "bg-amber-500",
  },
};

function formatWhen(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export default function DashboardPage() {
  return (
    <RequireAuth>
      <Dashboard />
    </RequireAuth>
  );
}

function Dashboard() {
  const { user } = useAuth();
  const [data, setData] = useState<DashboardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await getDashboard(8));
    } catch (err) {
      console.error("PRAMAAN dashboard load failed:", err);
      setError(
        err instanceof ScanError
          ? err.friendlyMessage
          : "The dashboard could not be loaded."
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="mx-auto max-w-5xl px-4 py-8 sm:px-8">
      <header className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">Enforcement dashboard</h1>
          <p className="text-sm text-muted-foreground">
            Compliance position across every inspection recorded on this
            installation
            {user?.full_name ? ` · signed in as ${user.full_name}` : ""}.
          </p>
        </div>
        <Button variant="outline" onClick={load} disabled={loading}>
          Refresh
        </Button>
      </header>

      {error && (
        <Alert variant="destructive" className="mb-4">
          <AlertTitle>Could not load the dashboard</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {loading && !data ? (
        <div className="flex flex-col gap-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-24 w-full rounded-xl" />
            ))}
          </div>
          <Skeleton className="h-48 w-full rounded-xl" />
        </div>
      ) : data ? (
        data.total === 0 ? (
          <div className="rounded-lg border border-dashed px-6 py-12 text-center">
            <p className="text-sm text-muted-foreground">
              No inspections recorded yet. The dashboard fills in as packs are
              inspected.
            </p>
            <Button className="mt-4" nativeButton={false} render={<Link href="/" />}>
              Start an inspection
            </Button>
          </div>
        ) : (
          <DashboardBody data={data} />
        )
      ) : null}
    </div>
  );
}

function StatCard({
  label,
  value,
  hint,
  accent,
}: {
  label: string;
  value: string;
  hint?: string;
  accent?: string;
}) {
  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          {label}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className={`text-3xl font-bold ${accent ?? ""}`}>{value}</div>
        {hint && <div className="mt-1 text-xs text-muted-foreground">{hint}</div>}
      </CardContent>
    </Card>
  );
}

function DashboardBody({ data }: { data: DashboardResponse }) {
  const { status, rule7, total } = data;

  // Percentages for the breakdown bar only. The counts themselves are what
  // the server sent; these are bar widths, never a restated verdict.
  const share = (n: number) => (total > 0 ? (n / total) * 100 : 0);

  const statusRows: Array<{ key: OverallStatus; count: number }> = [
    { key: "COMPLIANT", count: status.compliant },
    { key: "NON_COMPLIANT", count: status.non_compliant },
    { key: "NEEDS_MANUAL_REVIEW", count: status.needs_manual_review },
  ];

  return (
    <div className="flex flex-col gap-6">
      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Total inspections"
          value={String(total)}
          hint={
            status.other > 0
              ? `${status.other} with an unrecognised verdict`
              : "Recorded on this installation"
          }
        />
        <StatCard
          label="Compliant"
          value={String(status.compliant)}
          accent="text-green-700 dark:text-green-500"
          hint="Every applicable check passed"
        />
        <StatCard
          label="Non-compliant"
          value={String(status.non_compliant)}
          accent="text-red-700 dark:text-red-500"
          hint="An evidence-backed violation"
        />
        <StatCard
          label="Needs manual review"
          value={String(status.needs_manual_review)}
          accent="text-amber-600"
          hint="Not decided — an officer must look"
        />
      </section>

      <section className="grid gap-3 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>Compliance rate</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-4xl font-bold">
              {data.compliance_rate == null
                ? "—"
                : `${data.compliance_rate.toFixed(1)}%`}
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              {status.compliant} of {total} inspection{total === 1 ? "" : "s"}{" "}
              cleared every applicable check. Packs awaiting manual review are
              counted in the denominator, not as passes.
            </p>
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Verdict breakdown</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <div className="flex h-3 w-full overflow-hidden rounded-full bg-muted">
              {statusRows.map(({ key, count }) =>
                count > 0 ? (
                  <div
                    key={key}
                    className={STATUS_STYLE[key].bar}
                    style={{ width: `${share(count)}%` }}
                    title={`${STATUS_STYLE[key].label}: ${count}`}
                  />
                ) : null
              )}
            </div>
            <ul className="flex flex-col gap-2">
              {statusRows.map(({ key, count }) => (
                <li
                  key={key}
                  className="flex items-center justify-between gap-3 text-sm"
                >
                  <span className="flex items-center gap-2">
                    <span
                      className={`h-2.5 w-2.5 rounded-full ${STATUS_STYLE[key].bar}`}
                    />
                    {STATUS_STYLE[key].label}
                  </span>
                  <span className="text-muted-foreground">
                    {count} · {share(count).toFixed(1)}%
                  </span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      </section>

      <section className="grid gap-3 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Rule 7 — character height</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="flex flex-col gap-1.5 text-sm">
              <Row label="Meets the prescribed height" value={rule7.passed} />
              <Row label="Below the prescribed height" value={rule7.failed} />
              <Row label="Within the review band" value={rule7.review} />
              <Row
                label="Measured, no target selected"
                value={rule7.pending_selection}
              />
              <Row label="Not measured" value={rule7.not_measured} muted />
            </dl>
            <p className="mt-3 text-xs text-muted-foreground">
              An inspection with no measurement photo cannot pass Rule 7 — it is
              recorded as undecided, never as compliant.
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Rule 6 — declaration shortfall</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="flex flex-col gap-1.5 text-sm">
              <Row
                label="Inspections missing a mandatory declaration"
                value={data.incomplete_declarations}
              />
              <Row
                label="Mandatory declarations missing in total"
                value={data.missing_declarations}
              />
            </dl>
            <p className="mt-3 text-xs text-muted-foreground">
              Counted from the mandatory-field tally stored with each inspection.
            </p>
          </CardContent>
        </Card>
      </section>

      <section>
        <Card>
          <CardHeader>
            <CardTitle>Most frequent findings</CardTitle>
          </CardHeader>
          <CardContent>
            {data.top_findings.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No findings recorded on inspections that did not come out clean.
              </p>
            ) : (
              <>
                <ul className="flex flex-col gap-2">
                  {data.top_findings.map((f) => (
                    <li
                      key={f.finding}
                      className="flex items-start justify-between gap-4 border-b pb-2 text-sm last:border-b-0 last:pb-0"
                    >
                      <span className="text-muted-foreground">{f.finding}</span>
                      <Badge variant="secondary" className="shrink-0">
                        {f.count}
                      </Badge>
                    </li>
                  ))}
                </ul>
                <p className="mt-3 text-xs text-muted-foreground">
                  Across the {data.findings_considered} most recent inspection
                  {data.findings_considered === 1 ? "" : "s"} that did not come
                  out clean.
                </p>
              </>
            )}
          </CardContent>
        </Card>
      </section>

      <section>
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 className="text-lg font-semibold">Recent inspections</h2>
          <Link href="/inspections" className="text-sm hover:underline">
            View all history
          </Link>
        </div>
        <ul className="flex flex-col gap-2">
          {data.recent.map((item) => {
            const style = STATUS_STYLE[item.overall_status];
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
                    <Badge className={`ml-auto ${style?.badge ?? ""}`}>
                      {style?.label ?? item.overall_status}
                    </Badge>
                  </div>
                  <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                    <span>{formatWhen(item.created_at)}</span>
                    {item.manufacturer && <span>{item.manufacturer}</span>}
                    <span>
                      Rule 6: {item.mandatory_present}/{item.mandatory_total}{" "}
                      mandatory
                    </span>
                    <span>
                      Rule 7:{" "}
                      {item.has_rule7
                        ? (item.rule7_verdict ?? "no target selected")
                        : "not measured"}
                    </span>
                    <span>{item.inspector_name ?? "inspector not recorded"}</span>
                  </div>
                </Link>
              </li>
            );
          })}
        </ul>
      </section>
    </div>
  );
}

function Row({
  label,
  value,
  muted,
}: {
  label: string;
  value: number;
  muted?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={muted ? "text-muted-foreground" : "font-medium"}>{value}</dd>
    </div>
  );
}

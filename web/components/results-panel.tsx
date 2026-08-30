import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { FieldRow, ScanResponse } from "@/lib/api";

const STATE_BADGE: Record<FieldRow["state"], string> = {
  PRESENT: "bg-green-100 text-green-800 border-green-300",
  MISSING: "bg-red-100 text-red-800 border-red-300",
  REVIEW: "bg-amber-100 text-amber-800 border-amber-300",
};

function FieldTable({ rows }: { rows: FieldRow[] }) {
  return (
    <>
      {/* A 3-column table can't fit a long address plus a state badge on a
          phone screen without clipping one of them, so mobile gets a
          stacked layout instead of a narrower table. */}
      <div className="hidden sm:block">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Field</TableHead>
              <TableHead>Value</TableHead>
              <TableHead className="text-right">State</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.field}>
                <TableCell className="font-medium">
                  {row.field.replaceAll("_", " ")}
                </TableCell>
                <TableCell className="max-w-xs whitespace-normal break-words text-muted-foreground">
                  {row.value ?? "—"}
                </TableCell>
                <TableCell className="text-right">
                  <Badge variant="outline" className={STATE_BADGE[row.state]}>
                    {row.state}
                  </Badge>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <div className="flex flex-col divide-y sm:hidden">
        {rows.map((row) => (
          <div key={row.field} className="flex flex-col gap-1 py-3">
            <div className="flex items-center justify-between gap-2">
              <span className="font-medium">
                {row.field.replaceAll("_", " ")}
              </span>
              <Badge variant="outline" className={STATE_BADGE[row.state]}>
                {row.state}
              </Badge>
            </div>
            <span className="break-words text-muted-foreground">
              {row.value ?? "—"}
            </span>
          </div>
        ))}
      </div>
    </>
  );
}

/**
 * Renders the Rule 6 field table and cross-check problems for one scan.
 * The overall compliance banner is StatusBanner, driven by the server's
 * combine_status() result — this component only shows what was extracted,
 * never a verdict of its own.
 */
export function ResultsPanel({ result }: { result: ScanResponse }) {
  const mandatoryRows = result.rows.filter((r) => r.mandatory);
  const optionalRows = result.rows.filter((r) => !r.mandatory);

  return (
    <div className="flex flex-col gap-6 w-full max-w-2xl mx-auto">
      <div className="text-sm text-muted-foreground">
        {result.mandatory_present}/{result.mandatory_total} mandatory declarations detected
      </div>

      {result.problems.length > 0 && (
        <Alert variant="destructive">
          <AlertTitle>Flagged for officer review</AlertTitle>
          <AlertDescription>
            <ul className="list-disc pl-4">
              {result.problems.map((p, i) => (
                <li key={i}>{p}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Mandatory declarations</CardTitle>
        </CardHeader>
        <CardContent>
          <FieldTable rows={mandatoryRows} />
        </CardContent>
      </Card>

      {optionalRows.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-muted-foreground">
              Optional fields
            </CardTitle>
          </CardHeader>
          <CardContent>
            <FieldTable rows={optionalRows} />
          </CardContent>
        </Card>
      )}
    </div>
  );
}

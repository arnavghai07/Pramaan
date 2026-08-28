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
import { deriveVerdict, type FieldRow, type ScanResponse } from "@/lib/api";

const VERDICT_STYLES: Record<
  ReturnType<typeof deriveVerdict>,
  { label: string; className: string }
> = {
  OK: {
    label: "COMPLIANT — all mandatory declarations present",
    className: "bg-green-600 text-white border-green-700",
  },
  REVIEW: {
    label: "REVIEW NEEDED — an officer must confirm flagged fields",
    className: "bg-amber-500 text-white border-amber-600",
  },
  FAIL: {
    label: "NON-COMPLIANT — mandatory declaration(s) missing",
    className: "bg-red-600 text-white border-red-700",
  },
};

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

export function ResultsPanel({ result }: { result: ScanResponse }) {
  const verdict = deriveVerdict(result);
  const style = VERDICT_STYLES[verdict];
  const mandatoryRows = result.rows.filter((r) => r.mandatory);
  const optionalRows = result.rows.filter((r) => !r.mandatory);

  return (
    <div className="flex flex-col gap-6 w-full max-w-2xl mx-auto">
      <div className={`rounded-lg border px-4 py-3 font-semibold ${style.className}`}>
        {style.label}
        <span className="ml-2 font-normal opacity-90">
          ({result.mandatory_present}/{result.mandatory_total} mandatory fields present)
        </span>
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

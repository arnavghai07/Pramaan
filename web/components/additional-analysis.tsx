import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { AdditionalAnalysis, AnalysisState } from "@/lib/api";

/**
 * The checks that sit beside Rule 6 and Rule 7: declaration placement,
 * capture observation, readability and declaration validation.
 *
 * Nothing here decides anything. Every state and every sentence below was
 * produced by engine/analysis.py and is rendered verbatim — this component
 * only chooses a colour and a layout for it.
 *
 * FOUR STATES, FOUR COLOURS. NOT_ASSESSED is grey and says so in words, and
 * is never allowed to look like PASS: a check that shows nothing reads to an
 * inspector as a check that passed, which is the exact failure the engine's
 * "silence is never a pass" rule exists to prevent.
 *
 * An ADVISORY check (the capture observation) is rendered with the same
 * prominence as the rest and carries an explicit "does not affect the
 * verdict" note. It reads OFFICER REVIEW rather than NEEDS REVIEW, because
 * the two say different things: one asks a person to look at the photograph,
 * the other says the compliance status could not be settled.
 */
const STATE_STYLE: Record<AnalysisState, { label: string; className: string }> = {
  PASS: { label: "PASS", className: "bg-green-100 text-green-800 border-green-300" },
  FAIL: { label: "FAIL", className: "bg-red-100 text-red-800 border-red-300" },
  REVIEW: {
    label: "NEEDS REVIEW",
    className: "bg-amber-100 text-amber-800 border-amber-300",
  },
  NOT_ASSESSED: {
    label: "NOT ASSESSED",
    className: "bg-slate-100 text-slate-700 border-slate-300",
  },
};

/**
 * A finding is a claim about a pack, so it is set off from the explanatory
 * prose with a coloured rule rather than left as another line of grey text.
 * An officer scanning the card must be able to see at a glance which lines
 * are the actual problems.
 */
const SEVERITY_STYLE: Record<string, string> = {
  FAIL: "border-red-400 bg-red-50/60 text-red-900",
  REVIEW: "border-amber-400 bg-amber-50/60 text-amber-900",
  NOT_ASSESSED: "border-slate-300 bg-slate-50 text-slate-700",
};

const SEVERITY_LABEL: Record<string, string> = {
  FAIL: "Violation",
  REVIEW: "Officer review",
  NOT_ASSESSED: "Not assessed",
};

/** The checks a record should carry, in print order, for the null case. */
const ABSENT_CHECKS = [
  "Declaration placement",
  "Capture observation",
  "Readability",
  "Declaration validation",
];

/**
 * An advisory REVIEW is a request to look at the photograph, not an
 * undecided compliance question, so it gets its own wording.
 */
function badgeFor(check: { state: AnalysisState; advisory?: boolean }) {
  const style = STATE_STYLE[check.state] ?? STATE_STYLE.NOT_ASSESSED;
  if (check.advisory && check.state === "REVIEW") {
    return { ...style, label: "OFFICER REVIEW" };
  }
  return style;
}

export function AdditionalAnalysisPanel({
  analysis,
}: {
  analysis: AdditionalAnalysis | null;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Additional compliance analysis</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col">
        {!analysis ? (
          <>
            {/* An inspection recorded before these checks existed. It is not
                re-analysed on read: a stored record must replay as it was
                decided, not as today's software would decide it. */}
            <p className="text-sm text-muted-foreground">
              Not assessed for this inspection. These checks were added to
              PRAMAAN after this record was created, and the record is shown as
              it was decided rather than re-analysed.
            </p>
            <ul className="mt-4 flex flex-col gap-2">
              {ABSENT_CHECKS.map((title) => (
                <li key={title} className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium">{title}</span>
                  <Badge
                    variant="outline"
                    className={STATE_STYLE.NOT_ASSESSED.className}
                  >
                    {STATE_STYLE.NOT_ASSESSED.label}
                  </Badge>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <div className="divide-y">
            {analysis.checks.map((check) => {
              const style = badgeFor(check);
              return (
                <div key={check.check} className="flex flex-col gap-2 py-4 first:pt-0 last:pb-0">
                  <div className="flex items-center justify-between gap-3">
                    <h3 className="text-sm font-semibold">{check.title}</h3>
                    <Badge variant="outline" className={style.className}>
                      {style.label}
                    </Badge>
                  </div>
                  {check.advisory && (
                    <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                      Advisory — does not affect the compliance verdict
                    </p>
                  )}
                  {check.explanation && (
                    <p className="text-xs leading-relaxed text-muted-foreground">
                      {check.explanation}
                    </p>
                  )}
                  {check.findings.length > 0 && (
                    <ul className="flex flex-col gap-2">
                      {check.findings.map((finding, i) => (
                        <li
                          key={i}
                          className={`rounded-md border-l-2 px-3 py-2 text-sm leading-relaxed ${
                            SEVERITY_STYLE[finding.severity] ??
                            SEVERITY_STYLE.NOT_ASSESSED
                          }`}
                        >
                          <span className="mb-1 block text-[10px] font-semibold uppercase tracking-wide opacity-70">
                            {SEVERITY_LABEL[finding.severity] ?? finding.severity}
                          </span>
                          <span className="block">{finding.message}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

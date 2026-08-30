import type { OverallStatus } from "@/lib/api";

const STATUS_STYLES: Record<OverallStatus, { label: string; className: string }> = {
  COMPLIANT: {
    label: "COMPLIANT",
    className: "bg-green-600 text-white border-green-700",
  },
  NON_COMPLIANT: {
    label: "NON-COMPLIANT",
    className: "bg-red-600 text-white border-red-700",
  },
  NEEDS_MANUAL_REVIEW: {
    label: "NEEDS MANUAL REVIEW",
    className: "bg-amber-500 text-white border-amber-600",
  },
};

/**
 * Renders whatever engine/verdict.py's combine_status() decided — this
 * component never computes a status itself (CLAUDE.md rule 1: the verdict
 * is never decided in a UI component). findings are the same strings the
 * server returned, each traceable to a specific rule and piece of evidence.
 */
export function StatusBanner({
  status,
  findings,
}: {
  status: OverallStatus;
  findings: string[];
}) {
  const style = STATUS_STYLES[status];
  return (
    <div className={`flex flex-col gap-2 rounded-lg border px-4 py-3 ${style.className}`}>
      <span className="font-semibold">{style.label}</span>
      {findings.length > 0 && (
        <ul className="list-disc pl-4 text-sm font-normal opacity-90">
          {findings.map((f, i) => (
            <li key={i}>{f}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

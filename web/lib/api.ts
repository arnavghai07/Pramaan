/**
 * Typed client for the PRAMAAN FastAPI service. Mirrors api/models.py exactly
 * — if that file's shapes change, these types must change with them.
 */

export type FieldState = "PRESENT" | "MISSING" | "REVIEW";

export interface FieldRow {
  field: string;
  state: FieldState;
  value: string | null;
  mandatory: boolean;
}

export interface ScanResponse {
  fields: Record<string, unknown>;
  rows: FieldRow[];
  problems: string[];
  mandatory_present: number;
  mandatory_total: number;
  best_rotation: string | null;
  orientations_tried: string[];
}

interface ApiErrorBody {
  detail: string;
  orientations_tried?: string[];
}

export class ScanError extends Error {
  orientationsTried?: string[];

  constructor(detail: string, orientationsTried?: string[]) {
    super(detail);
    this.name = "ScanError";
    this.orientationsTried = orientationsTried;
  }
}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function scanImage(
  image: File | Blob,
  filename = "capture.jpg"
): Promise<ScanResponse> {
  const form = new FormData();
  form.append("image", image, filename);

  let res: Response;
  try {
    res = await fetch(`${API_URL}/scan`, { method: "POST", body: form });
  } catch {
    throw new ScanError(
      `Could not reach the PRAMAAN API at ${API_URL}. Is uvicorn running?`
    );
  }

  if (!res.ok) {
    let body: ApiErrorBody = { detail: `Scan failed (HTTP ${res.status})` };
    try {
      body = await res.json();
    } catch {
      // Non-JSON error body — keep the generic message set above.
    }
    throw new ScanError(body.detail, body.orientations_tried);
  }

  return res.json();
}

export type Verdict = "OK" | "REVIEW" | "FAIL";

/**
 * A problem (illegible field, cross-check mismatch) means the engine isn't
 * confident, which outranks a plain missing-field count: CLAUDE.md rule 3,
 * "confident wrong beats nothing is false" — REVIEW before FAIL.
 */
export function deriveVerdict(result: ScanResponse): Verdict {
  if (result.problems.length > 0) return "REVIEW";
  if (result.mandatory_present < result.mandatory_total) return "FAIL";
  return "OK";
}

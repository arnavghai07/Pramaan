/**
 * Typed client for the PRAMAAN FastAPI service. Mirrors api/models.py exactly
 * — if that file's shapes change, these types must change with them.
 *
 * Every call below sends the bearer token from lib/auth.ts. A 401 comes back
 * as AuthError rather than a generic failure, so the UI can send the officer
 * to the sign-in page instead of showing "analysis failed" for what is really
 * an expired session.
 */
import { authHeaders, clearToken, type User } from "@/lib/auth";

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

const SERVICE_UNAVAILABLE_MESSAGE =
  "Inspection service is temporarily unavailable. Please try again.";
const ANALYSIS_FAILED_MESSAGE =
  "Analysis could not be completed. Please retry with a clearer image.";

export class ScanError extends Error {
  orientationsTried?: string[];
  /**
   * Short, professional message safe to show as the primary UI text.
   * `message` (inherited from Error) keeps the raw backend/model detail —
   * useful in the browser console for debugging, but never meant to reach
   * the screen directly: a raw Ollama/JSON/Windows-path error dump reads as
   * a crash to a judge, not a status.
   */
  friendlyMessage: string;

  constructor(detail: string, orientationsTried?: string[], friendlyMessage?: string) {
    super(detail);
    this.name = "ScanError";
    this.orientationsTried = orientationsTried;
    this.friendlyMessage = friendlyMessage ?? ANALYSIS_FAILED_MESSAGE;
  }
}

/**
 * The session is gone (never signed in, expired, or the account was
 * deactivated). Thrown instead of ScanError so callers can redirect rather
 * than render a scan-failure message.
 */
export class AuthError extends ScanError {
  constructor(detail: string) {
    super(detail, undefined, "Your session has ended. Please sign in again.");
    this.name = "AuthError";
  }
}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** Network-level failure shared by every call. */
function unreachable(): ScanError {
  return new ScanError(
    `Could not reach the PRAMAAN API at ${API_URL}. Is uvicorn running?`,
    undefined,
    SERVICE_UNAVAILABLE_MESSAGE
  );
}

export async function scanImage(
  image: File | Blob,
  filename = "capture.jpg"
): Promise<ScanResponse> {
  const form = new FormData();
  form.append("image", image, filename);

  let res: Response;
  try {
    res = await fetch(`${API_URL}/scan`, {
      method: "POST",
      body: form,
      headers: authHeaders(),
    });
  } catch {
    throw unreachable();
  }

  if (!res.ok) throw await readApiError(res);

  return res.json();
}

export type Verdict = "OK" | "REVIEW" | "FAIL";

/**
 * A problem (illegible field, cross-check mismatch) means the engine isn't
 * confident, which outranks a plain missing-field count: CLAUDE.md rule 3,
 * "confident wrong beats nothing is false" — REVIEW before FAIL.
 *
 * Kept for callers that only ever hit /scan directly (e.g. the CLI-parity
 * gate). The unified workflow below gets its status from the server
 * (engine/verdict.py's combine_status()) instead of recomputing it here —
 * CLAUDE.md rule 1, the verdict is never decided in a UI component.
 */
export function deriveVerdict(result: ScanResponse): Verdict {
  if (result.problems.length > 0) return "REVIEW";
  if (result.mandatory_present < result.mandatory_total) return "FAIL";
  return "OK";
}

// ---------------------------------------------------------------------------
// Phase D — unified inspection (Rule 6 + Rule 7 combined verdict)
// ---------------------------------------------------------------------------

export interface Rule7Row {
  x: number;
  y: number;
  w: number;
  h: number;
  height_mm: number;
}

export interface CandidatesResponse {
  tilt_spread_pct: number;
  capture_scale_ppm: number;
  rows: Rule7Row[];
  overlay_png_base64: string;
}

/**
 * Rule 7's contribution to a combined inspection. There is deliberately no
 * "target index" here — see rule7-panel.tsx, which resolves a tap on the
 * candidate overlay to a row internally and never surfaces the concept of
 * an index to the inspector. "manual_region" is the fallback path: the
 * inspector drew a rectangle because no automatic candidate cleanly
 * bounded the complete numeral — see rule7_measure_selected_region()'s
 * docstring in engine/measure_chart.py for why the rectangle itself is
 * never the reported measurement.
 */
export interface Rule7Result {
  attempted: boolean;
  problem: string | null;
  tilt_spread_pct: number | null;
  capture_scale_ppm: number | null;
  rows: Rule7Row[];
  measured_height_mm: number | null;
  threshold_mm: number | null;
  verdict: "PASS" | "FAIL" | "REVIEW" | null;
  selection_method: "manual" | "manual_region" | "auto" | null;
  overlay_png_base64: string | null;
}

/** A rectangle in one orientation's rectified-frame pixel coordinates. */
export interface Rule7Region {
  x: number;
  y: number;
  w: number;
  h: number;
}

export type OverallStatus = "COMPLIANT" | "NON_COMPLIANT" | "NEEDS_MANUAL_REVIEW";

export interface InspectionResponse {
  rule6: ScanResponse;
  rule7: Rule7Result | null;
  overall_status: OverallStatus;
  findings: string[];
  /**
   * The stored history record this inspection was saved as. Null when the
   * server could not persist it — the verdict above is still valid and is
   * still shown; only the history link is missing.
   */
  inspection_id: number | null;
}

async function readApiError(res: Response): Promise<ScanError> {
  let body: ApiErrorBody = { detail: `Request failed (HTTP ${res.status})` };
  try {
    body = await res.json();
  } catch {
    // Non-JSON error body — keep the generic message set above.
  }
  if (res.status === 401) {
    // The stored token is provably useless; drop it so the next page load
    // doesn't retry with it.
    clearToken();
    return new AuthError(body.detail);
  }
  return new ScanError(body.detail, body.orientations_tried);
}

/**
 * Rule 7 candidate rows on a photo, with no target chosen — drives the
 * "tap the price" picker in rule7-panel.tsx. Internally this is a plain
 * POST /measure/candidates call; nothing here talks about row indices.
 */
export async function getRule7Candidates(
  image: File | Blob,
  filename = "rule7.jpg",
  markerMm = 40.0
): Promise<CandidatesResponse> {
  const form = new FormData();
  form.append("image", image, filename);
  form.append("marker_mm", String(markerMm));

  let res: Response;
  try {
    res = await fetch(`${API_URL}/measure/candidates`, {
      method: "POST",
      body: form,
      headers: authHeaders(),
    });
  } catch {
    throw unreachable();
  }
  if (!res.ok) throw await readApiError(res);
  return res.json();
}

/**
 * Preview the FALLBACK manual-region measurement — mirrors
 * getRule7Candidates() above, but for a hand-drawn rectangle instead of a
 * tapped candidate. Lets the UI show the trimmed-glyph evidence (or an
 * "ambiguous"/"nothing found" outcome) before the inspector commits to the
 * full inspectPack() call.
 */
export async function getRule7RegionMeasurement(
  image: File | Blob,
  filename: string,
  markerMm: number,
  rotationDeg: number,
  region: Rule7Region,
  pdpAreaCm2?: number,
  container: "normal" | "blown" = "normal"
): Promise<Rule7Result> {
  const form = new FormData();
  form.append("image", image, filename);
  form.append("marker_mm", String(markerMm));
  form.append("rotation_deg", String(rotationDeg));
  form.append("region_x", String(Math.round(region.x)));
  form.append("region_y", String(Math.round(region.y)));
  form.append("region_w", String(Math.round(region.w)));
  form.append("region_h", String(Math.round(region.h)));
  if (pdpAreaCm2 != null) form.append("pdp_area_cm2", String(pdpAreaCm2));
  form.append("container", container);

  let res: Response;
  try {
    res = await fetch(`${API_URL}/measure/region`, {
      method: "POST",
      body: form,
      headers: authHeaders(),
    });
  } catch {
    throw unreachable();
  }
  if (!res.ok) throw await readApiError(res);
  return res.json();
}

export interface InspectParams {
  rule6Image?: File | Blob;
  rule6Filename?: string;
  /**
   * A Rule 6 result this client already received from an earlier call
   * (initial scan), sent back instead of the photo so the server can skip
   * re-running VLM extraction. Takes priority over rule6Image when both are
   * set — see inspectPack()'s doc comment.
   */
  rule6Result?: ScanResponse;
  rule7Image?: File | Blob;
  rule7Filename?: string;
  markerMm?: number;
  pdpAreaCm2?: number;
  container?: "normal" | "blown";
  /** Internal selection mechanism — see Rule7Result's doc comment. */
  targetIndex?: number;
  /** Fallback selection mechanism — takes priority over targetIndex if both are set. */
  region?: Rule7Region;
  rotationDeg?: number;
  /**
   * Update this existing history record instead of creating a new one.
   * Set when adding a Rule 7 measurement to a pack that was already
   * scanned, so one physical inspection stays one row with one set of
   * evidence images.
   */
  inspectionId?: number | null;
  /** Optional label for the history list. Never inferred from the photo. */
  productName?: string;
}

/**
 * The unified workflow entry point: Rule 6 declarations plus an optional
 * Rule 7 measurement, combined server-side into one overall_status.
 *
 * Exactly one of rule6Image / rule6Result should be given. Use rule6Image
 * for an initial scan (Rule 6 has not run yet). Use rule6Result when Rule 6
 * already ran earlier in this session and you're only adding a Rule 7
 * measurement — passing the already-known result avoids paying for a
 * second, identical VLM inference on the same photo.
 */
export async function inspectPack(params: InspectParams): Promise<InspectionResponse> {
  const form = new FormData();
  if (params.inspectionId != null) {
    form.append("inspection_id", String(params.inspectionId));
  }
  if (params.productName) {
    form.append("product_name", params.productName);
  }
  if (params.rule6Result) {
    form.append("rule6_result", JSON.stringify(params.rule6Result));
  } else if (params.rule6Image) {
    form.append("rule6_image", params.rule6Image, params.rule6Filename ?? "capture.jpg");
  }
  if (params.rule7Image) {
    form.append("rule7_image", params.rule7Image, params.rule7Filename ?? "rule7.jpg");
    form.append("marker_mm", String(params.markerMm ?? 40.0));
    if (params.pdpAreaCm2 != null) form.append("pdp_area_cm2", String(params.pdpAreaCm2));
    if (params.container) form.append("container", params.container);
    if (params.region) {
      form.append("rotation_deg", String(params.rotationDeg ?? 0));
      form.append("region_x", String(Math.round(params.region.x)));
      form.append("region_y", String(Math.round(params.region.y)));
      form.append("region_w", String(Math.round(params.region.w)));
      form.append("region_h", String(Math.round(params.region.h)));
    } else if (params.targetIndex != null) {
      form.append("target_index", String(params.targetIndex));
    }
  }

  let res: Response;
  try {
    res = await fetch(`${API_URL}/inspect`, {
      method: "POST",
      body: form,
      headers: authHeaders(),
    });
  } catch {
    throw unreachable();
  }
  if (!res.ok) throw await readApiError(res);
  return res.json();
}

// ---------------------------------------------------------------------------
// Authentication
// ---------------------------------------------------------------------------

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: User;
}

/**
 * Exchange credentials for a bearer token. This is the one call that does
 * NOT send an Authorization header — there is nothing to send yet.
 */
export async function login(username: string, password: string): Promise<LoginResponse> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
  } catch {
    throw unreachable();
  }
  if (!res.ok) {
    let detail = `Sign-in failed (HTTP ${res.status})`;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      // keep the generic message
    }
    // A rejected sign-in is not an expired session: surfacing the server's
    // own wording ("incorrect username or password") is what the person at
    // the keyboard actually needs here.
    throw new ScanError(detail, undefined, detail);
  }
  return res.json();
}

/** Who the server believes the stored token belongs to. */
export async function fetchMe(): Promise<User> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/auth/me`, { headers: authHeaders() });
  } catch {
    throw unreachable();
  }
  if (!res.ok) throw await readApiError(res);
  return res.json();
}

// ---------------------------------------------------------------------------
// Inspection history
// ---------------------------------------------------------------------------

export interface InspectionSummary {
  id: number;
  created_at: string;
  product_name: string | null;
  overall_status: OverallStatus;
  mandatory_present: number;
  mandatory_total: number;
  rule7_verdict: "PASS" | "FAIL" | "REVIEW" | null;
  manufacturer: string | null;
  mrp: number | null;
  net_quantity: string | null;
  mfg_date: string | null;
  has_rule7: boolean;
  inspector_name: string | null;
}

/** Which stored evidence images exist for an inspection. */
export type EvidenceKind = "rule6" | "rule7" | "overlay";

export interface InspectionDetail extends InspectionSummary {
  rule6: ScanResponse;
  rule7: Rule7Result | null;
  findings: string[];
  evidence: EvidenceKind[];
  rule6_image_stored: boolean;
  rule7_image_stored: boolean;
  rule7_overlay_stored: boolean;
}

export interface InspectionListResponse {
  items: InspectionSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface InspectionFilters {
  limit?: number;
  offset?: number;
  status?: OverallStatus | "";
  q?: string;
  dateFrom?: string;   // YYYY-MM-DD
  dateTo?: string;     // YYYY-MM-DD
}

export async function listInspections(
  filters: InspectionFilters = {}
): Promise<InspectionListResponse> {
  const params = new URLSearchParams();
  params.set("limit", String(filters.limit ?? 20));
  params.set("offset", String(filters.offset ?? 0));
  if (filters.status) params.set("status", filters.status);
  if (filters.q?.trim()) params.set("q", filters.q.trim());
  if (filters.dateFrom) params.set("date_from", filters.dateFrom);
  if (filters.dateTo) params.set("date_to", filters.dateTo);

  let res: Response;
  try {
    res = await fetch(`${API_URL}/inspections?${params}`, { headers: authHeaders() });
  } catch {
    throw unreachable();
  }
  if (!res.ok) throw await readApiError(res);
  return res.json();
}

/**
 * One stored inspection. The Rule 7 overlay is not inlined — fetch it with
 * evidenceUrl()/fetchEvidence() instead, because that image alone is about
 * 9 MB of base64 and would make opening a record slower than the scan was.
 */
export async function getInspection(id: number): Promise<InspectionDetail> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/inspections/${id}`, { headers: authHeaders() });
  } catch {
    throw unreachable();
  }
  if (!res.ok) throw await readApiError(res);
  return res.json();
}

/** ADMIN only; an inspector account gets a 403 here. */
export async function deleteInspection(id: number): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/inspections/${id}`, {
      method: "DELETE",
      headers: authHeaders(),
    });
  } catch {
    throw unreachable();
  }
  if (!res.ok) throw await readApiError(res);
}

/**
 * Fetch one evidence image as an object URL.
 *
 * A plain <img src> cannot carry an Authorization header, and the
 * alternative — putting the token in the query string — would write
 * credentials into browser history and any proxy log. So the bytes are
 * fetched properly and handed to the <img> as a blob URL. Callers must
 * URL.revokeObjectURL() the result when the image unmounts; see
 * components/evidence-image.tsx.
 */
export async function fetchEvidenceObjectUrl(
  id: number,
  kind: EvidenceKind
): Promise<string> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/inspections/${id}/evidence/${kind}`, {
      headers: authHeaders(),
    });
  } catch {
    throw unreachable();
  }
  if (!res.ok) throw await readApiError(res);
  return URL.createObjectURL(await res.blob());
}

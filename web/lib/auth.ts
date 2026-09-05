/**
 * Session handling for the PRAMAAN console.
 *
 * WHERE THE TOKEN LIVES, AND WHY
 * -------------------------------
 * The bearer token is kept in localStorage and sent as an Authorization
 * header. The more locked-down alternative is an httpOnly cookie the browser
 * attaches automatically, which JavaScript cannot read and therefore cannot
 * leak through an XSS bug. That option is not available here: the API (port
 * 8000) and the console (port 3000) are different origins, so the cookie
 * would have to be SameSite=None, and SameSite=None requires Secure, which
 * requires HTTPS — which a local demo does not have.
 *
 * So: header-based tokens, and the tradeoff is written down rather than
 * discovered later. The signing secret never reaches the browser; only a
 * signed, expiring token does. Moving the API behind the same origin (a
 * Next.js rewrite, or one container in Phase J) is what makes httpOnly
 * cookies available, and is the right time to switch.
 */

export type Role = "ADMIN" | "INSPECTOR";

export interface User {
  id: number;
  username: string;
  role: Role;
  full_name: string | null;
}

export interface LoginResult {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: User;
}

const TOKEN_KEY = "pramaan.token";

/** Human-readable role name. INSPECTOR is the enforcement-officer role. */
export const ROLE_LABEL: Record<Role, string> = {
  ADMIN: "Administrator",
  INSPECTOR: "Inspector / Enforcement Officer",
};

/**
 * localStorage is unavailable in a server render and can throw outright in
 * a browser configured to block site data, so every access is guarded. A
 * failure means "not signed in", never a crashed page.
 */
export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string): void {
  try {
    window.localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // Session then lasts only as long as this tab's memory of it; the user
    // is still signed in for the current page.
  }
}

export function clearToken(): void {
  try {
    window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    // Nothing stored, nothing to clear.
  }
}

/** Authorization header for an API call, or {} when signed out. */
export function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

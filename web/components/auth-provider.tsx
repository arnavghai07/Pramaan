"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { useRouter } from "next/navigation";
import { fetchMe, login as loginRequest } from "@/lib/api";
import { clearToken, getToken, setToken, type User } from "@/lib/auth";

interface AuthState {
  user: User | null;
  /** True until the stored token has been checked against the server. */
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

/**
 * Holds the signed-in officer for the whole console.
 *
 * On mount it does not trust the token it finds in storage: it calls
 * /auth/me and lets the SERVER say who the caller is and what role they
 * hold. A token is just a claim, and a role decoded from one in the browser
 * would be a role the browser could edit. The server re-reads the role from
 * the database on every request regardless — this call is what keeps the
 * navigation honest about it.
 */
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    if (!getToken()) {
      setLoading(false);
      return;
    }

    fetchMe()
      .then((me) => {
        if (!cancelled) setUser(me);
      })
      .catch(() => {
        // Expired, tampered with, or the API is down. Either way this
        // session cannot be used; treat it as signed out rather than
        // leaving a half-authenticated UI on screen.
        if (!cancelled) {
          clearToken();
          setUser(null);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const result = await loginRequest(username, password);
    setToken(result.access_token);
    setUser(result.user);
  }, []);

  const logout = useCallback(() => {
    clearToken();
    setUser(null);
  }, []);

  const value = useMemo(
    () => ({ user, loading, login, logout }),
    [user, loading, login, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}

/**
 * Guards a page. Renders nothing while the session is being established,
 * then either the page or a redirect to sign-in.
 *
 * This is a convenience for the person using the console, NOT the security
 * boundary — every protected route is enforced in FastAPI (api/auth.py).
 * A user who defeats this guard reaches a page whose every API call still
 * returns 401.
 */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [loading, user, router]);

  if (loading) {
    return (
      <div className="py-20 text-center text-sm text-muted-foreground">
        Restoring your session…
      </div>
    );
  }
  if (!user) return null;
  return <>{children}</>;
}

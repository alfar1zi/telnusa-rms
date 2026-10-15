/**
 * AuthContext: session identity managed by the server.
 *
 * The browser NEVER decides "logged in = true".
 * Identity comes exclusively from GET /api/auth/me (HttpOnly cookie session).
 *
 * In mock mode: returns a synthetic admin user immediately.
 * In real mode: fetches /api/auth/me; 401 → user is null → ProtectedRoute redirects.
 *
 * NEVER store the user object in localStorage or sessionStorage.
 */

import { createContext, useContext, useEffect, useState, useCallback } from "react";
import type { ReactNode }                                               from "react";
import { getAuthUser, logout as apiLogout }                            from "@/services/api";
import type { AuthUser }                                               from "@/types/auth";

interface AuthContextValue {
  user:    AuthUser | null;
  loading: boolean;
  // True when the session check failed for a backend reason (5xx, timeout,
  // unreachable). The session is neither confirmed nor rejected, so the guard
  // shows an outage screen instead of the login form.
  sessionError: boolean;
  logout:  () => Promise<void>;
  refresh: () => Promise<void>; // re-validate session (e.g. after navigation)
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user,    setUser]    = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [sessionError, setSessionError] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const u = await getAuthUser();
      setUser(u);
      setSessionError(false);
    } catch {
      setUser(null);
      setSessionError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  const logout = useCallback(async () => {
    await apiLogout(); // navigates to /login internally
    setUser(null);
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  return (
    <AuthContext.Provider value={{ user, loading, sessionError, logout, refresh }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}

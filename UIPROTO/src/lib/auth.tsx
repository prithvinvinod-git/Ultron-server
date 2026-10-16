/**
 * Authentication state for the console.
 *
 * The token is the only credential the browser holds; it is validated once on
 * mount by calling `/auth/me`, so a stale token from a previous session is
 * discovered before the console renders anything sensitive. Initial state is
 * deliberately "loading" rather than reading localStorage during render: the SPA
 * shell is prerendered in Node, so a synchronous localStorage read would produce
 * a hydration mismatch.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { ApiError, getToken, login as loginRequest, logout as logoutRequest, setToken, whoami } from "./api";
import type { Principal } from "./api";

type AuthStatus = "loading" | "authenticated" | "anonymous";

interface AuthContextValue {
  status: AuthStatus;
  principal: Principal | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const AUTH_EVENT = "ultron:unauthorized";

/** Tell listeners the token was rejected, so the console can drop to the gate. */
export function broadcastUnauthorized(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(AUTH_EVENT));
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [principal, setPrincipal] = useState<Principal | null>(null);

  const verify = useCallback(async () => {
    if (!getToken()) {
      setStatus("anonymous");
      setPrincipal(null);
      return;
    }
    try {
      setPrincipal(await whoami());
      setStatus("authenticated");
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) setToken(null);
      setPrincipal(null);
      setStatus("anonymous");
    }
  }, []);

  useEffect(() => {
    void verify();
  }, [verify]);

  useEffect(() => {
    const onUnauthorized = () => {
      setToken(null);
      setPrincipal(null);
      setStatus("anonymous");
    };
    window.addEventListener(AUTH_EVENT, onUnauthorized);
    return () => window.removeEventListener(AUTH_EVENT, onUnauthorized);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const tokens = await loginRequest(username, password);
    setToken(tokens.access_token);
    setPrincipal(await whoami());
    setStatus("authenticated");
  }, []);

  const logout = useCallback(async () => {
    try {
      await logoutRequest();
    } catch {
      // Logout is best-effort: the local token is cleared regardless, which is
      // the outcome the user asked for.
    }
    setToken(null);
    setPrincipal(null);
    setStatus("anonymous");
  }, []);

  const value = useMemo(
    () => ({ status, principal, login, logout }),
    [status, principal, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside <AuthProvider>");
  return context;
}

/** Run an async call and drop to the login gate if the server says 401. */
export function useApiGuard() {
  const ref = useRef(false);
  return useCallback(async <T,>(fn: () => Promise<T>): Promise<T | undefined> => {
    try {
      return await fn();
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        if (!ref.current) {
          ref.current = true;
          broadcastUnauthorized();
        }
        return undefined;
      }
      throw error;
    }
  }, []);
}

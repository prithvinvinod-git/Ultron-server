/**
 * Unauthorized-call signalling for the console.
 *
 * Built-in authentication has been removed (it moves to an external provider,
 * Firebase), so the console no longer holds a token, logs in, or renders a gate.
 * What remains is the one thing callers still need: a single place to report a
 * `401` from the API. Today nothing listens — there is no gate to drop to — so
 * the handler callers wrap their requests in is a thin pass-through that keeps
 * the error handling shape stable until the Firebase gate is wired in here.
 */

import { useCallback, useRef } from "react";

import { ApiError } from "./api";

const AUTH_EVENT = "ultron:unauthorized";

/** Announce that the server rejected a call as unauthorized. */
export function broadcastUnauthorized(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(AUTH_EVENT));
}

/** Run an async call, swallowing a single `401` and reporting it once. */
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

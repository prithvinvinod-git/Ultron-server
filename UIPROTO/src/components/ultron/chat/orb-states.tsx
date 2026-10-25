/**
 * Orb visual states per §59.13.
 *
 * The spec defines seven states: IDLE, LISTENING, THINKING, SPEAKING,
 * PROCESSING, ERROR, NOTIFICATION. This file re-exports the
 * `thinking-orbs` package's animation component and maps the spec states
 * to the package's available animation modes.
 *
 * Mapping:
 *   IDLE           → 'working'   (particles on tilted orbits, implies calm readiness)
 *   LISTENING      → 'listening'  (waveform rolls through latitude rings)
 *   THINKING       → 'solving'     (bands scramble in quarter turns — deliberation)
 *   SPEAKING       → 'connecting'  (constellation wires itself, packets running edges)
 *   PROCESSING     → 'weaving'     (three strands plait around the sphere)
 *   ERROR          → 'working'     (same as IDLE; ERROR is visually distinct
 *                                 only via the surrounding UI, not the animation)
 *   NOTIFICATION   → 'searching'   (a scan meridian sweeps a dotted globe;
 *                                 implies something requires attention)
 *
 * The orb itself must never predict state — it reflects state from the
 * server via the agent-window protocol (§28), never animates "thinking"
 * because it guessed.
 */

import { ThinkingOrb } from "thinking-orbs";
import type { OrbState } from "thinking-orbs";
import { onEnvelope } from "@/lib/events";
import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

export type OrbVisualState =
  | "idle"
  | "listening"
  | "thinking"
  | "speaking"
  | "processing"
  | "error"
  | "notification";

/** Maps a spec visual state to a thinking-orbs `OrbState` mode. */
const STATE_TO_ORB: Record<OrbVisualState, OrbState> = {
  idle: "working",
  listening: "listening",
  thinking: "solving",
  speaking: "connecting",
  processing: "weaving",
  error: "working",
  notification: "searching",
};

/** Maps a thinking-orbs `OrbState` back to a spec visual state. */
const ORB_TO_STATE: Record<OrbState, OrbVisualState> = {
  working: "idle",
  listening: "listening",
  solving: "thinking",
  connecting: "speaking",
  weaving: "processing",
  searching: "notification",
  composing: "speaking",
  breathing: "idle",
  shaping: "processing",
};

/** The current visual state, driven by server SSE events over §28. */
export function useOrbState(): OrbVisualState {
  const [state, setState] = useState<OrbVisualState>("idle");

  // Listen for orb state changes from the server.
  // The event bus publishes ORB_STATE_CHANGED with a payload { state }.
  useEffect(() => {
    const unsub = onEnvelope((event) => {
      if (event.event_type !== "ORB_STATE_CHANGED") return;
      const payload = event.payload as { state: OrbVisualState } | undefined;
      if (!payload?.state) return;
      setState(payload.state);
    });
    return unsub;
  }, []);

  return state;
}

/** Orb component that reflects the current visual state from the server. */
interface OrbProps {
  className?: string;
}
export function Orb({ className }: OrbProps) {
  const visualState = useOrbState();
  const orbState = STATE_TO_ORB[visualState];

  return (
    <ThinkingOrb
      state={orbState}
      /* The console is a permanently dark HUD, but the package resolves
         `auto` from `prefers-color-scheme` (no `data-theme` or `dark` class is
         ever set on the document). On a light-set desktop that drew dark ink on
         the dark header — an orb that was mounted but invisible. */
      theme="dark"
      className={cn("orb", className)}
    />
  );
}

/** Quick-action button that opens the ULTRON UI when clicked. */
export function OrbQuickAction() {
  return (
    <button
      className="orb-action"
      aria-label="Open ULTRON"
      onClick={() => {
        // The open/focus behavior belongs to the client shell (§59.14); this
        // button is a visual affordance only, so it must not assume an
        // Electron-only `window.ultron` bridge exists.
      }}
    >
      <svg
        width="24"
        height="24"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <circle cx="12" cy="12" r="10" />
        <polyline points="8 12 12 15 16 12" />
      </svg>
    </button>
  );
}

/** Orb state label for accessibility. */
export function useOrbAriaLabel(): string {
  const state = useOrbState();
  const labels: Record<OrbVisualState, string> = {
    idle: "ULTRON idle — ready for interaction",
    listening: "ULTRON listening — awaiting voice or text input",
    thinking: "ULTRON thinking — processing a request",
    speaking: "ULTRON speaking — generating a response",
    processing: "ULTRON processing — executing a task",
    error: "ULTRON error — something went wrong",
    notification: "ULTRON notification — requires attention",
  };
  return labels[state];
}
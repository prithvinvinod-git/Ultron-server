/**
 * A single shared connection to the ULTRON event bus (`GET /events`).
 *
 * One connection serves the whole console: opening a stream per panel would
 * multiply the server's fan-out and make reconnect storms during an outage far
 * worse. The store implements what `EventSource` would have given us for free --
 * frame parsing, automatic reconnect, and `Last-Event-ID` resumption -- because
 * `/events` authenticates with an `Authorization` header, which `EventSource`
 * cannot send (spec §19/§30).
 */

import { useSyncExternalStore } from "react";

import { ApiError, getToken, readSse } from "./api";
import type { EventEnvelope, StreamConnectionInfo } from "./api";
import { broadcastUnauthorized } from "./auth";

export type ConnectionState = "idle" | "connecting" | "open" | "closed";

interface EventsSnapshot {
  state: ConnectionState;
  info: StreamConnectionInfo | null;
  events: EventEnvelope[];
  dropped: number;
  error: string | null;
}

const MAX_BUFFERED = 60;
const INITIAL_BACKOFF_MS = 1000;
const MAX_BACKOFF_MS = 30000;

let snapshot: EventsSnapshot = {
  state: "idle",
  info: null,
  events: [],
  dropped: 0,
  error: null,
};

const listeners = new Set<() => void>();
const envelopeHandlers = new Set<(event: EventEnvelope) => void>();

let controller: AbortController | null = null;
let lastEventId: number | null = null;
let backoff = INITIAL_BACKOFF_MS;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let running = false;

function emit(): void {
  for (const listener of listeners) listener();
}

function update(patch: Partial<EventsSnapshot>): void {
  snapshot = { ...snapshot, ...patch };
  emit();
}

function pushEvent(event: EventEnvelope): void {
  const events = [event, ...snapshot.events].slice(0, MAX_BUFFERED);
  snapshot = { ...snapshot, events };
  emit();
  for (const handler of envelopeHandlers) handler(event);
}

async function connect(): Promise<void> {
  if (!getToken()) {
    update({ state: "idle" });
    return;
  }
  controller = new AbortController();
  update({ state: "connecting", error: null });

  const headers = new Headers({ Accept: "text/event-stream" });
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (lastEventId !== null) headers.set("Last-Event-ID", String(lastEventId));

  try {
    const res = await fetch("/events", { headers, signal: controller.signal });
    if (res.status === 401) {
      broadcastUnauthorized();
      return;
    }
    if (!res.ok || !res.body) throw new ApiError(res.status, `event stream refused (${res.status})`);

    update({ state: "open" });
    backoff = INITIAL_BACKOFF_MS;

    for await (const frame of readSse(res.body)) {
      if (frame.id && /^\d+$/.test(frame.id)) lastEventId = Number(frame.id);
      if (!frame.event || !frame.data) continue;
      const parsed = JSON.parse(frame.data) as EventEnvelope;
      if (frame.event === "stream.connected") {
        const info = parsed.payload as unknown as StreamConnectionInfo;
        update({ info, dropped: info.dropped ?? 0 });
        continue;
      }
      if (frame.event === "stream.lagged") {
        update({ dropped: snapshot.dropped + 1 });
      }
      pushEvent(parsed);
    }
  } catch (error) {
    if ((error as { name?: string }).name === "AbortError") return;
    update({ state: "closed", error: error instanceof Error ? error.message : "stream error" });
  }

  if (running) scheduleReconnect();
}

function scheduleReconnect(): void {
  if (reconnectTimer !== null) return;
  update({ state: "closed" });
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    if (running) void connect();
  }, backoff);
  backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
}

function start(): void {
  if (running) return;
  running = true;
  void connect();
}

function stop(): void {
  running = false;
  if (reconnectTimer !== null) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  controller?.abort();
  controller = null;
  lastEventId = null;
  update({ state: "idle", info: null });
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  if (listeners.size === 1) start();
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) stop();
  };
}

function getSnapshot(): EventsSnapshot {
  return snapshot;
}

/** React binding: current connection state plus the most recent envelopes. */
export function useEventStream(): EventsSnapshot {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

/** Fire a callback for every incoming envelope without re-rendering on each. */
export function onEnvelope(handler: (event: EventEnvelope) => void): () => void {
  envelopeHandlers.add(handler);
  return () => envelopeHandlers.delete(handler);
}

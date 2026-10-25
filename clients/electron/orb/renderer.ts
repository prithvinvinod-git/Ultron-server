/**
 * Floating orb renderer (spec §2/§5/§6).
 *
 * The orb is a *display*: it renders the state ULTRON Core publishes and never
 * invents one. Every transition below is caused by an SSE frame from `/events`
 * (`ORB_STATE_CHANGED`, `ORB_SHOW`, `ORB_HIDE`, `TRANSCRIPT_*`) or by the
 * transport itself (`DISCONNECTED` when the stream drops — a dead server cannot
 * report its own death).
 *
 * Voice amplitude note: the orb cannot read the microphone or the TTS buffer;
 * capture and playback live in the console's existing `VoiceClient`, in a
 * different renderer. `SPEAKING` is therefore driven by the real
 * `TTS_STARTED`/`TTS_COMPLETED` projection, and the equalizer inside the core is
 * a *rendering* of "speech is playing", not an audio measurement. Wiring real
 * levels would mean posting them from the console over IPC; until then this
 * stays honest by not claiming to be a mic meter.
 */
import { MODE_FRAMES, paintFrame, resolvePreset } from "thinking-orbs/engine";
import type { OrbState as ThinkingOrbState } from "thinking-orbs/engine";

type OrbStateName =
  | "hidden"
  | "idle"
  | "listening"
  | "thinking"
  | "executing"
  | "responding"
  | "speaking"
  | "notification"
  | "error"
  | "disconnected";

type Intensity = "low" | "normal" | "high";
type QuickAction = "voice" | "open";

interface OrbPosition {
  x: number;
  y: number;
}

interface OrbConfig {
  position: OrbPosition;
  bounds: { width: number; height: number };
  visible: boolean;
  intensity: Intensity;
  quickAction: QuickAction;
  origin: string;
  backendUrl: string;
  version: string;
}

/**
 * Interactive geometry of the widget, in window CSS px. Main polls the OS cursor
 * against this instead of relying on pointer events, because a click-through
 * window receives none (so it could never re-arm itself).
 */
interface OrbHitRegion {
  x: number;
  y: number;
  radius: number;
  caption: { left: number; top: number; right: number; bottom: number } | null;
}

interface OrbBridge {
  getConfig: () => Promise<OrbConfig | null>;
  setVisible: (visible: boolean) => Promise<void>;
  setPosition: (position: OrbPosition) => Promise<void>;
  move: (position: OrbPosition) => void;
  /** Drag is executed in the main process; the renderer only drives the ticks. */
  dragStart: (offset: OrbPosition) => void;
  dragMove: () => void;
  dragEnd: () => void;
  /** Report where the orb/caption are; main decides click-through from this. */
  setHitRegion: (region: OrbHitRegion) => void;
  openMain: () => void;
  startVoice: () => void;
  stopVoice: () => void;
  cancel: () => void;
  quickAction: () => void;
  showMenu: () => void;
  hide: () => void;
  quit: () => void;
  onConfigChanged: (listener: (config: Partial<OrbConfig>) => void) => () => void;
  onReconnect: (listener: () => void) => () => void;
  /** Main's view of the cursor, needed because click-through drops moves. */
  onPointerInside: (listener: (inside: boolean) => void) => () => void;
}

declare global {
  interface Window {
    ultronOrb?: OrbBridge;
  }
}

interface OrbEnvelope {
  id?: number;
  event_type?: string;
  payload?: Record<string, unknown>;
  occurred_at?: string;
}

interface OrbErrorPayload {
  code?: unknown;
  message?: unknown;
}

interface OrbNotificationPayload {
  kind?: unknown;
  message?: unknown;
}

interface OrbStatePayload {
  state?: unknown;
  previous_state?: unknown;
  reason?: unknown;
  visible?: unknown;
  pending?: unknown;
  wake?: { wake_word?: unknown } | null;
  error?: OrbErrorPayload | null;
  notification?: OrbNotificationPayload | null;
}

const STATE_NAMES: readonly OrbStateName[] = [
  "hidden",
  "idle",
  "listening",
  "thinking",
  "executing",
  "responding",
  "speaking",
  "notification",
  "error",
  "disconnected",
];

const STATE_TEXT: Record<OrbStateName, string> = {
  hidden: "Hidden",
  idle: "Idle",
  listening: "Listening",
  thinking: "Thinking",
  executing: "Executing a task",
  responding: "Responding",
  speaking: "Speaking",
  notification: "Notification",
  error: "Error",
  disconnected: "Disconnected",
};

/**
 * Where the orb sits inside the window, in CSS px (see `orb.css`: centred
 * horizontally, `top: 108px`). Nothing else hard-codes a window size: hit
 * testing and the affordance rings read the orb's *live* rect, so the grab area
 * always follows the painted orb even if the frame's size ever changes.
 */
const SIZE = 64;
const HIT_RADIUS = 44;

interface Point {
  x: number;
  y: number;
}

/** Live centre of the painted orb, in this window's CSS pixels. */
function orbCenter(): Point {
  const rect = canvas.getBoundingClientRect();
  return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
}

/**
 * The widget orb *is* the console orb, not a lookalike.
 *
 * `orb-states.tsx` renders `ThinkingOrb` at its default size and dark theme,
 * which mounts this element:
 *
 *   <canvas role="img" aria-label="Working…" class="orb"
 *           width="80" height="80"
 *           style="width: 64px; height: 64px; display: block;"></canvas>
 *
 * (`width`/`height` are `64 × devicePixelRatio`, capped at 2 — 80 on a 125%
 * Windows display.) The renderer below reproduces that host loop exactly:
 * the same 64px preset, the same capped-DPR backing store, the same dark
 * theme, the same `paintFrame` call. Only the surrounding window chrome
 * (caption, hover ring) is widget-specific.
 */

/** `thinking-orbs`' own aria labels, mirrored for the canvas element. */
const ORB_LABELS: Record<ThinkingOrbState, string> = {
  working: "Working…",
  searching: "Searching…",
  solving: "Solving…",
  listening: "Listening…",
  connecting: "Connecting…",
  weaving: "Weaving…",
  composing: "Composing…",
  breathing: "Thinking…",
  shaping: "Shaping…",
};

/**
 * Spec visual state → `thinking-orbs` animation. Mirrors `orb-states.tsx`
 * (the in-app orb) exactly; the widget-only states reuse the closest mode from
 * the same set rather than a bespoke drawing.
 */
const ORB_VISUALS: Record<OrbStateName, ThinkingOrbState> = {
  hidden: "working",
  idle: "working",
  listening: "listening",
  thinking: "solving",
  executing: "weaving",
  responding: "composing",
  speaking: "connecting",
  notification: "searching",
  error: "working",
  disconnected: "breathing",
};

/** Menu "Animation intensity" → multiplier on the preset's baked speed. */
const INTENSITY_SPEED: Record<Intensity, number> = { low: 0.6, normal: 1, high: 1.5 };

const FRAME_MS = 1000 / 30;

function element<T extends HTMLElement>(id: string): T {
  const found = document.getElementById(id);
  if (found === null) throw new Error(`orb markup missing #${id}`);
  return found as T;
}

const root = element<HTMLElement>("orb-root");
/**
 * Selected exactly like the console's canvas: the `thinking-orbs` component
 * owns the element's identity (`class="orb"`, `role="img"`, aria-label), so we
 * look it up by that class rather than adding a widget-only id to it.
 */
function requireOrbCanvas(): HTMLCanvasElement {
  const found = document.querySelector<HTMLCanvasElement>("canvas.orb");
  if (found === null) throw new Error("orb markup missing canvas.orb");
  return found;
}

const canvas = requireOrbCanvas();
const overlay = element<HTMLCanvasElement>("orb-affordances");
const hit = element<HTMLDivElement>("orb-hit");
const captionEl = element<HTMLParagraphElement>("orb-caption");
const captionTopEl = element<HTMLParagraphElement>("orb-caption-top");
const context = canvas.getContext("2d");
const overlayContext = overlay.getContext("2d");

const bridge = window.ultronOrb;

let config: OrbConfig | null = null;
let intensity: Intensity = "normal";
let state: OrbStateName = "idle";
let visible = true;
let connected = true;
let hovering = false;
let dragging = false;
let holding = false;
let holdStartedAt = 0;
/** Bumped whenever a transcript lands, so state text cannot overwrite it. */
let captionLockUntil = 0;
let captionTimer: number | null = null;
let pixelRatio = 1;

let lastEventId = 0;
let lastEventAt = 0;
const seenStateIds = new Set<number>();

// ------------------------------------------------------------------------- //
// Canvas
// ------------------------------------------------------------------------- //
function rgba(hex: string, alpha: number): string {
  const value = hex.replace("#", "");
  const r = Number.parseInt(value.slice(0, 2), 16);
  const g = Number.parseInt(value.slice(2, 4), 16);
  const b = Number.parseInt(value.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function resize(): void {
  pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
  // Same backing-store rule as `ThinkingOrb`: 64 CSS px × capped DPR. This is
  // what produces width="80" height="80" on a 125% Windows display.
  canvas.width = Math.round(SIZE * pixelRatio);
  canvas.height = Math.round(SIZE * pixelRatio);
  overlay.width = Math.round(window.innerWidth * pixelRatio);
  overlay.height = Math.round(window.innerHeight * pixelRatio);
  // The rings are positioned from the orb's live rect, so a resize must repaint
  // them immediately instead of waiting for the next animation frame.
  drawAffordances();
  // The frame size may have been corrected; main needs the new geometry before
  // the next cursor poll.
  publishHitRegion();
}

function strokeArc(
  ctx: CanvasRenderingContext2D,
  center: Point,
  radius: number,
  rotation: number,
  span: number,
  color: string,
  alpha: number,
  width: number,
): void {
  ctx.beginPath();
  ctx.arc(center.x, center.y, radius, rotation, rotation + span);
  ctx.strokeStyle = rgba(color, alpha);
  ctx.lineWidth = width;
  ctx.stroke();
}

/**
 * Widget affordances only (hover, press, drag). These live on a separate,
 * full-window overlay canvas so `canvas.orb` keeps exactly the pixels, element
 * and backing-store size the console's orb has.
 */
function drawAffordances(): void {
  if (overlayContext === null) return;
  const ctx = overlayContext;
  const center = orbCenter();
  ctx.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
  ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);
  if (dragging) {
    strokeArc(ctx, center, HIT_RADIUS - 2, 0, Math.PI * 2, "#9fe9ff", 0.35, 1.2);
  } else if (hovering || holding) {
    strokeArc(ctx, center, HIT_RADIUS - 3, 0, Math.PI * 2, "#9fe9ff", 0.28, 1);
  }
  if (holding) {
    const progress = Math.min(1, (performance.now() - holdStartedAt) / 550);
    strokeArc(ctx, center, HIT_RADIUS - 6, -Math.PI / 2, Math.PI * 2 * progress, "#9fe9ff", 0.85, 2.4);
  }
}

function draw(now: number): void {
  if (context === null) return;
  const ctx = context;

  // Verbatim `ThinkingOrb` frame composition: preset for the state, baked
  // speed (times the widget's intensity preference), dark theme, 64px box.
  const resolved = resolvePreset(ORB_VISUALS[state], SIZE);
  const frame = MODE_FRAMES[resolved.mode];
  const speed = resolved.speed * INTENSITY_SPEED[intensity];
  const t = reduceMotion ? 0.6 : (now / 1000) * speed;

  ctx.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
  ctx.clearRect(0, 0, SIZE, SIZE);
  paintFrame(ctx, frame(SIZE, t, resolved.opts), true);
}

// ------------------------------------------------------------------------- //
// Captions
// ------------------------------------------------------------------------- //
function hideCaption(): void {
  captionEl.dataset.visible = "false";
  publishHitRegion();
}

/**
 * Sets the bottom caption text from STT recognized text.
 * The top caption is NOT affected - it keeps its own text.
 * @param text - The STT recognized text
 * @param kind - The caption kind (partial/final/error/notice/disconnected)
 * @param holdMs - How long to keep the caption visible (ms), 0 = until next caption
 */
function setSTTCaption(text: string, kind: string, holdMs = 0): void {
  const trimmed = text.trim();
  if (trimmed.length === 0) return;
  if (captionTimer !== null) {
    window.clearTimeout(captionTimer);
    captionTimer = null;
  }
  if (captionEl.textContent !== trimmed) captionEl.textContent = trimmed;
  captionEl.dataset.kind = kind;
  captionEl.dataset.visible = "true";
  publishHitRegion();
  if (holdMs > 0) {
    captionLockUntil = performance.now() + holdMs;
    captionTimer = window.setTimeout(() => {
      captionTimer = null;
      hideCaption();
    }, holdMs);
  }
}

/**
 * Sets the top caption text from AI response text.
 * The bottom caption is NOT affected - it keeps its own STT text.
 * @param text - The AI response text
 * @param kind - The caption kind (partial/final/error/notice/disconnected)
 * @param holdMs - How long to keep the caption visible (ms), 0 = until next caption
 */
function setResponseText(text: string, kind: string, holdMs = 0): void {
  const trimmed = text.trim();
  if (trimmed.length === 0) return;
  if (captionTopEl.textContent !== trimmed) captionTopEl.textContent = trimmed;
  captionTopEl.dataset.kind = kind;
  captionTopEl.dataset.visible = "true";
  publishHitRegion();
  if (holdMs > 0) {
    captionLockUntil = performance.now() + holdMs;
    captionTimer = window.setTimeout(() => {
      captionTimer = null;
      // Only hide the top caption, leave bottom intact
      captionTopEl.dataset.visible = "false";
      captionTopEl.dataset.kind = "";
      publishHitRegion();
    }, holdMs);
  }
}

function stateCaption(payload: OrbStatePayload): void {
  const now = performance.now();
  const error = payload.error;
  if (error && typeof error.message === "string" && error.message.length > 0) {
    setSTTCaption(error.message.replace(/_/g, " ").toLowerCase(), "error");
    return;
  }
  const notification = payload.notification;
  if (notification && typeof notification.message === "string" && notification.message.length > 0) {
    setSTTCaption(notification.message, "notice", 6500);
    return;
  }
  if (now < captionLockUntil) return;
  switch (state) {
    case "listening":
      setSTTCaption(
        payload.pending === true ? "Listening…" : "Listening…",
        "notice",
      );
      break;
    case "thinking":
      setSTTCaption("Thinking…", "notice");
      break;
    case "executing":
      setSTTCaption("Working on it…", "notice");
      break;
    case "responding":
      // AI response goes to top caption, not STT bottom caption
      break;
    case "disconnected":
      setSTTCaption("Reconnecting to ULTRON…", "disconnected");
      break;
    case "idle":
    case "hidden":
    case "speaking":
    case "notification":
    case "error":
      // No caption change needed
      break;
  }
}

// ------------------------------------------------------------------------- //
// State machine
// ------------------------------------------------------------------------- //
function applyStateName(next: OrbStateName): void {
  if (next === state) return;
  state = next;
  root.dataset.state = next;
  // The canvas' accessible name is the animation's own label, exactly like
  // `ThinkingOrb` (`aria-label` prop → LABELS[state]).
  canvas.setAttribute("aria-label", ORB_LABELS[ORB_VISUALS[next]]);
  updateHitLabel();
}

/**
 * The hit region is the only focusable element, so its label has to describe the
 * gestures as they actually behave: the double-click runs the configured quick
 * action (voice by default, or open ULTRON), a single click does nothing.
 */
function updateHitLabel(): void {
  const quick = config?.quickAction === "open" ? "open ULTRON" : "start voice";
  hit.setAttribute(
    "aria-label",
    `ULTRON orb. ${STATE_TEXT[state]}. Double-click to ${quick}, hold to talk, right-click for options.`,
  );
}

function isStateName(value: unknown): value is OrbStateName {
  return typeof value === "string" && (STATE_NAMES as readonly string[]).includes(value);
}

function applyVisibility(next: boolean): void {
  if (next === visible) return;
  visible = next;
  if (bridge !== undefined) void bridge.setVisible(next);
}

function applyStatePayload(payload: OrbStatePayload): void {
  if (!isStateName(payload.state)) return;
  applyStateName(payload.state);
  if (typeof payload.visible === "boolean") applyVisibility(payload.visible);
  if (payload.state === "hidden") applyVisibility(false);
  if (payload.state !== "hidden" && payload.visible === true) applyVisibility(true);
  stateCaption(payload);
}

function envelopeIsStale(envelope: OrbEnvelope, id: number): boolean {
  if (id <= 0) return false;
  const at = typeof envelope.occurred_at === "string" ? Date.parse(envelope.occurred_at) : 0;
  const newerByClock = Number.isFinite(at) && at > lastEventAt + 250;
  if (id <= lastEventId && !newerByClock) return true;
  if (seenStateIds.has(id) && !newerByClock) return true;
  return false;
}

function recordEnvelope(envelope: OrbEnvelope, id: number): void {
  if (id > 0) {
    seenStateIds.add(id);
    lastEventId = Math.max(lastEventId, id);
    if (seenStateIds.size > 400) {
      const oldest = Math.min(...seenStateIds);
      seenStateIds.delete(oldest);
    }
  }
  const at = typeof envelope.occurred_at === "string" ? Date.parse(envelope.occurred_at) : 0;
  if (Number.isFinite(at)) lastEventAt = Math.max(lastEventAt, at);
}

function handleOrbState(envelope: OrbEnvelope, id: number): void {
  if (envelopeIsStale(envelope, id)) return;
  recordEnvelope(envelope, id);
  applyStatePayload((envelope.payload ?? {}) as OrbStatePayload);
}

function handleTranscript(envelope: OrbEnvelope, partial: boolean): void {
  const payload = envelope.payload ?? {};
  const text = typeof payload.text === "string" ? payload.text : "";
  if (text.length === 0) return;
  setSTTCaption(text, partial ? "partial" : "final", partial ? 3200 : 7000);
}

function setDisconnected(): void {
  if (!connected) return;
  connected = false;
  applyStateName("disconnected");
  setSTTCaption("Reconnecting to ULTRON…", "disconnected");
  root.dataset.connection = "down";
}

function setConnected(): void {
  connected = true;
  root.dataset.connection = "up";
}

// ------------------------------------------------------------------------- //
// Transport
// ------------------------------------------------------------------------- //
const WATCHED = [
  "ORB_STATE_CHANGED",
  "ORB_SHOW",
  "ORB_HIDE",
  "TRANSCRIPT_PARTIAL",
  "TRANSCRIPT_FINAL",
  "MODEL_RESPONSE",
  "WAKE_DETECTED",
] as const;

async function syncFromServer(): Promise<void> {
  try {
    const response = await fetch("/orb/state", { cache: "no-store" });
    if (!response.ok) throw new Error(`orb state ${response.status}`);
    const snapshot = (await response.json()) as OrbStatePayload;
    setConnected();
    applyStatePayload(snapshot);
  } catch {
    setDisconnected();
  }
}

let stream: EventSource | null = null;
let retryTimer: number | null = null;
let backoff = 1000;

function scheduleReconnect(): void {
  if (retryTimer !== null) return;
  retryTimer = window.setTimeout(() => {
    retryTimer = null;
    backoff = Math.min(backoff * 1.8, 15_000);
    connectStream();
  }, backoff);
}

function connectStream(): void {
  stream?.close();
  stream = new EventSource("/events");

  stream.addEventListener("stream.connected", () => {
    backoff = 1000;
    setConnected();
    void syncFromServer();
  });

  for (const type of WATCHED) {
    stream.addEventListener(type, (event) => {
      const message = event as MessageEvent<string>;
      let envelope: OrbEnvelope;
      try {
        envelope = JSON.parse(message.data) as OrbEnvelope;
      } catch {
        return; // a malformed frame is ignored, never rendered
      }
      const id = Number(message.lastEventId || envelope.id || 0);
      setConnected();
      if (type === "ORB_STATE_CHANGED") {
        handleOrbState(envelope, id);
      } else if (type === "ORB_SHOW") {
        applyVisibility(true);
      } else if (type === "ORB_HIDE") {
        applyVisibility(false);
      } else if (type === "TRANSCRIPT_PARTIAL") {
        handleTranscript(envelope, true);
      } else if (type === "TRANSCRIPT_FINAL") {
        handleTranscript(envelope, false);
      } else if (type === "MODEL_RESPONSE") {
        const payload = envelope.payload ?? {};
        const text = typeof payload.text === "string" ? payload.text : "";
        if (text.length > 0) {
          setResponseText(text, "final", 7000);
        }
      }
    });
  }

  stream.onerror = () => {
    stream?.close();
    stream = null;
    setDisconnected();
    scheduleReconnect();
  };
}

// ------------------------------------------------------------------------- //
// Interaction
// ------------------------------------------------------------------------- //
interface Press {
  pointerId: number;
  /** Cursor position when the press started (screen + client, for the slop). */
  startScreenX: number;
  startScreenY: number;
  startClientX: number;
  startClientY: number;
  moved: boolean;
  holdTimer: number | null;
}

let press: Press | null = null;
let clickTimer: number | null = null;
/** Window in which a second click counts as the double-click gesture. */
const DOUBLE_CLICK_MS = 320;
let dragFrame = 0;

function isInteractive(x: number, y: number): boolean {
  const center = orbCenter();
  const dx = x - center.x;
  const dy = y - center.y;
  if (dx * dx + dy * dy <= HIT_RADIUS * HIT_RADIUS) return true;
  if (captionEl.dataset.visible === "true") {
    const rect = captionEl.getBoundingClientRect();
    if (x >= rect.left - 10 && x <= rect.right + 10 && y >= rect.top - 6 && y <= rect.bottom + 6) {
      return true;
    }
  }
  return false;
}

/**
 * The region main uses for click-through. It mirrors `isInteractive`, including
 * the small slack around the caption text, so the pixels that accept input are
 * exactly the pixels the renderer would have accepted itself.
 */
function hitRegion(): OrbHitRegion {
  const center = orbCenter();
  const captionVisible = captionEl.dataset.visible === "true";
  const rect = captionVisible ? captionEl.getBoundingClientRect() : null;
  const captionTopVisible = captionTopEl.dataset.visible === "true";
  const rectTop = captionTopVisible ? captionTopEl.getBoundingClientRect() : null;
  
  // Union of both caption rects if both are visible
  let captionRect: { left: number; top: number; right: number; bottom: number } | null = rect
    ? { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom }
    : null;
  if (rectTop !== null) {
    if (captionRect === null) {
      captionRect = { left: rectTop.left, top: rectTop.top, right: rectTop.right, bottom: rectTop.bottom };
    } else {
      captionRect = {
        left: Math.min(captionRect.left, rectTop.left),
        top: Math.min(captionRect.top, rectTop.top),
        right: Math.max(captionRect.right, rectTop.right),
        bottom: Math.max(captionRect.bottom, rectTop.bottom),
      };
    }
  }
  
  return {
    x: center.x,
    y: center.y,
    radius: HIT_RADIUS,
    caption:
      captionRect === null
        ? null
        : {
            left: captionRect.left - 10,
            top: captionRect.top - 6,
            right: captionRect.right + 10,
            bottom: captionRect.bottom + 6,
          },
  };
}

/** Last region sent to main, so caption/state churn cannot spam the IPC channel. */
let publishedRegion = "";

/**
 * Tell main where the widget actually is so it can keep the transparent frame
 * click-through. Called whenever the geometry moves: boot, resize, caption
 * show/hide.
 */
function publishHitRegion(): void {
  if (bridge === undefined) return;
  const region = hitRegion();
  const key = JSON.stringify(region);
  if (key === publishedRegion) return;
  publishedRegion = key;
  bridge.setHitRegion(region);
}

function endHold(): void {
  if (!holding) return;
  holding = false;
  bridge?.stopVoice();
}

function cancelDragFrame(): void {
  if (dragFrame === 0) return;
  window.cancelAnimationFrame(dragFrame);
  dragFrame = 0;
}

/** Ask the main process to re-place the window under the cursor. */
function requestDragMove(): void {
  if (!dragging || dragFrame !== 0) return;
  dragFrame = window.requestAnimationFrame(() => {
    dragFrame = 0;
    if (dragging) bridge?.dragMove();
  });
}

function startDrag(): void {
  if (press === null || press.moved) return;
  press.moved = true;
  dragging = true;
  if (press.holdTimer !== null) {
    window.clearTimeout(press.holdTimer);
    press.holdTimer = null;
  }
  // Grab point inside the frame (DIP). Main turns `cursor − grab` into the new
  // position on every tick, so repeated drags never accumulate an error and the
  // orb can never drift away from the frame that receives the clicks.
  bridge?.dragStart({
    x: press.startScreenX - window.screenX,
    y: press.startScreenY - window.screenY,
  });
  requestDragMove();
}

/**
 * Finish the current gesture exactly once, whatever ended it.
 *
 * `source` decides whether a click may still be inferred: a real pointerup does,
 * but a lost capture/cancel/blur must only release the state — otherwise a
 * dropped pointerup would either fire the double-click action unexpectedly or,
 * worse, leave `press` set forever (which used to stop hover re-arming, so the
 * widget became permanently click-through).
 */
function endGesture(source: "pointerup" | "cancel"): void {
  const current = press;
  if (current === null) return;
  press = null;
  if (current.holdTimer !== null) {
    window.clearTimeout(current.holdTimer);
    current.holdTimer = null;
  }
  cancelDragFrame();
  try {
    if (hit.hasPointerCapture(current.pointerId)) hit.releasePointerCapture(current.pointerId);
  } catch {
    // Already released.
  }

  if (holding) {
    endHold();
    dragging = false;
    return;
  }

  if (current.moved) {
    dragging = false;
    bridge?.dragEnd();
    // The cursor is still on the orb after a drag; keep the widget clickable
    // instead of waiting for a mousemove that may never come.
    hovering = true;
    return;
  }

  if (source !== "pointerup") return;

  // Click vs double-click, resolved manually so a drag or hold never counts as
  // a click.
  //
  // A single click deliberately does nothing: the orb is an ambient widget, and
  // a stray click used to yank the console forward. The *gesture* is the
  // double-click, and what it does is the user's choice (`quickAction`): start
  // voice by default, or open ULTRON. The timer only distinguishes the two.
  if (clickTimer !== null) {
    window.clearTimeout(clickTimer);
    clickTimer = null;
    bridge?.quickAction();
    return;
  }
  clickTimer = window.setTimeout(() => {
    clickTimer = null;
  }, DOUBLE_CLICK_MS);
}

function onPointerDown(event: PointerEvent): void {
  if (event.button !== 0) return;
  // The transparent corners of the frame are not a grab handle.
  if (!isInteractive(event.clientX, event.clientY)) return;
  // A previous gesture that never ended must not block this one.
  if (press !== null) endGesture("cancel");
  hovering = true;
  press = {
    pointerId: event.pointerId,
    startScreenX: event.screenX,
    startScreenY: event.screenY,
    startClientX: event.clientX,
    startClientY: event.clientY,
    moved: false,
    holdTimer: window.setTimeout(() => {
      if (press === null || press.moved) return;
      holding = true;
      holdStartedAt = performance.now();
      bridge?.startVoice();
    }, 550),
  };
  try {
    hit.setPointerCapture(event.pointerId);
  } catch {
    // Capture is best-effort; window-level listeners still receive moves.
  }
}

function onPointerMove(event: PointerEvent): void {
  if (press === null) {
    hovering = isInteractive(event.clientX, event.clientY);
    return;
  }
  // A lost pointerup (window hidden, capture stolen, OS quirk) looks exactly
  // like a move with no buttons held. Treat it as the end of the gesture.
  if (event.buttons === 0) {
    endGesture("cancel");
    return;
  }
  if (holding) return;
  if (!press.moved) {
    // Before the window starts following the cursor, both coordinate spaces are
    // usable; after that only the screen delta keeps growing.
    const screenDelta = Math.hypot(event.screenX - press.startScreenX, event.screenY - press.startScreenY);
    const clientDelta = Math.hypot(event.clientX - press.startClientX, event.clientY - press.startClientY);
    if (Math.max(screenDelta, clientDelta) > 4) startDrag();
    return;
  }
  requestDragMove();
}

function onPointerUp(event: PointerEvent): void {
  if (press === null) return;
  if (event.pointerId !== press.pointerId) return;
  const x = event.clientX;
  const y = event.clientY;
  // After a drag the window has just moved under the cursor, so the event's
  // client coordinates are stale; the pointer is on the orb by construction.
  const wasDrag = press.moved;
  endGesture("pointerup");
  hovering = wasDrag || isInteractive(x, y);
}

function onPointerCancel(): void {
  endGesture("cancel");
}


function onContextMenu(event: MouseEvent): void {
  event.preventDefault();
  bridge?.showMenu();
}

function onKeyDown(event: KeyboardEvent): void {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    bridge?.openMain();
  } else if (event.key === "v" || event.key === "V") {
    event.preventDefault();
    bridge?.startVoice();
  } else if (event.key === "Escape") {
    event.preventDefault();
    endHold();
    bridge?.cancel();
    hideCaption();
  } else if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) {
    event.preventDefault();
    bridge?.showMenu();
  }
}

// ------------------------------------------------------------------------- //
// Boot
// ------------------------------------------------------------------------- //
let frameHandle = 0;
let lastFrame = 0;
let reduceMotion = false;

function loop(now: number): void {
  frameHandle = window.requestAnimationFrame(loop);
  if (document.hidden || !visible) return;
  const interval = reduceMotion ? 250 : FRAME_MS;
  if (now - lastFrame < interval) return;
  lastFrame = now;
  draw(now);
  drawAffordances();
}

async function boot(): Promise<void> {
  resize();
  window.addEventListener("resize", resize);
  root.dataset.state = state;
  reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  if (bridge !== undefined) {
    config = await bridge.getConfig();
    if (config !== null) {
      intensity = config.intensity;
      visible = config.visible;
    }
    updateHitLabel();
    bridge.onConfigChanged((next) => {
      if (next.intensity !== undefined) intensity = next.intensity;
      if (next.visible !== undefined) visible = next.visible;
      if (next.quickAction !== undefined && config !== null) {
        // The menu can change what the double-click does; keep the accessible
        // label honest about it.
        config = { ...config, quickAction: next.quickAction };
        updateHitLabel();
      }
    });
    bridge.onReconnect(() => {
      stream?.close();
      stream = null;
      backoff = 500;
      void syncFromServer();
      connectStream();
    });
    // Hover feedback cannot come from pointer events: the frame is click-through
    // whenever the cursor is away, so main reports the cursor instead.
    bridge.onPointerInside((inside) => {
      if (press !== null) return;
      if (inside === hovering) return;
      hovering = inside;
      drawAffordances();
    });
    publishHitRegion();
  }

  hit.addEventListener("pointerdown", onPointerDown);
  window.addEventListener("pointermove", onPointerMove);
  window.addEventListener("pointerup", onPointerUp);
  window.addEventListener("pointercancel", onPointerCancel);
  // Every way a gesture can end must clear the press state, or the widget stays
  // "mid-drag" and never re-arms its hover/click handling again.
  hit.addEventListener("lostpointercapture", onPointerCancel);
  window.addEventListener("blur", onPointerCancel);
  window.addEventListener("contextmenu", onContextMenu);
  document.addEventListener("keydown", onKeyDown);
  hit.addEventListener("pointerenter", () => {
    hovering = true;
  });
  hit.addEventListener("pointerleave", () => {
    if (press === null) hovering = false;
  });

  await syncFromServer();
  connectStream();

  frameHandle = window.requestAnimationFrame(loop);
}

window.addEventListener("beforeunload", () => {
  window.cancelAnimationFrame(frameHandle);
  stream?.close();
  if (retryTimer !== null) window.clearTimeout(retryTimer);
});

void boot();

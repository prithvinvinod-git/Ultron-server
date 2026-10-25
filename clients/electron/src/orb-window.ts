/**
 * Standalone floating orb window (spec §1/§6/§8).
 *
 * This is a *second* window that lives beside the console, not inside it: a
 * frameless, transparent, always-on-top widget that stays above ordinary
 * desktop windows. The console window keeps owning chat, agents and history;
 * the orb is only a display plus a handful of gestures.
 *
 * Responsibilities split (§8):
 *  - main process (here): window lifecycle, always-on-top level, position
 *    persistence/clamping, the native context menu, and the narrow IPC bridge;
 *  - orb renderer (`orb/renderer.ts`): canvas drawing, captions, gestures;
 *  - ULTRON Core: authoritative voice/agent/task events (`/events`), which the
 *    orb consumes directly as a same-origin client of the static server.
 *
 * Security: contextIsolation on, nodeIntegration off, its own preload with an
 * explicit channel list. No API keys or backend credentials are copied here —
 * the orb speaks to the loopback static server, which proxies to the backend
 * exactly like the console does.
 *
 * Persistence: position/visibility/animation preferences are a JSON file in
 * `app.getPath("userData")`, not server state — they belong to this desktop.
 */
import { app, BrowserWindow, ipcMain, Menu, screen } from "electron";
import type { MenuItemConstructorOptions } from "electron";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

import { IPC_CHANNELS } from "./ipc-channels.js";

/** Window size in DIP. Tall enough for the orb plus one caption line. */
export const ORB_WIDTH = 320;
export const ORB_HEIGHT = 264;

/** Keep at least this much of the orb on screen after a monitor change. */
const MIN_VISIBLE = 96;

/**
 * Interactive geometry reported by the renderer, in window CSS px. The orb disc
 * plus (when shown) the caption box; everything else in the frame is
 * click-through.
 */
interface OrbHitRegion {
  x: number;
  y: number;
  radius: number;
  caption: { left: number; top: number; right: number; bottom: number } | null;
}

/**
 * How often main compares the OS cursor with the hit region. Click-through has
 * to be owned here: a window with `setIgnoreMouseEvents(true)` receives no
 * pointer events, so the renderer cannot re-arm itself (Electron 44 does not
 * forward moves to this page in practice). ~30 Hz is cheap (`GetCursorPos`) and
 * keeps hover/click latency invisible.
 */
const POINTER_POLL_MS = 33;

type Logger = (level: "info" | "error", message: string, extra?: Record<string, unknown>) => void;

export type OrbIntensity = "low" | "normal" | "high";
export type OrbQuickAction = "voice" | "open";

interface OrbPrefs {
  x: number | null;
  y: number | null;
  visible: boolean;
  intensity: OrbIntensity;
  quickAction: OrbQuickAction;
}

export interface OrbHandle {
  window: BrowserWindow;
  isVisible: () => boolean;
  setVisible: (visible: boolean) => void;
  destroy: () => void;
}

export interface OrbWindowOptions {
  /** Loopback static-server origin; the orb is a same-origin client. */
  origin: string;
  /** Absolute path to the compiled orb preload. */
  preload: string;
  /** Backend origin, reported to the renderer for display only. */
  backendUrl: string;
  /** `app.getPath("userData")` — injected so tests can use a temp dir. */
  userDataPath: string;
  getMainWindow: () => BrowserWindow | null;
  /** Recreate the console window if the user closed it but kept the orb. */
  ensureMainWindow: () => BrowserWindow;
  log: Logger;
  /** Quit the whole application (Core keeps running until this is called). */
  quit: () => void;
}

const DEFAULTS: OrbPrefs = {
  x: null,
  y: null,
  visible: true,
  intensity: "normal",
  quickAction: "voice",
};

const INTENSITIES: readonly OrbIntensity[] = ["low", "normal", "high"];
const QUICK_ACTIONS: readonly OrbQuickAction[] = ["voice", "open"];

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function readPrefs(path: string, log: Logger): OrbPrefs {
  if (!existsSync(path)) return { ...DEFAULTS };
  try {
    const raw = JSON.parse(readFileSync(path, "utf8")) as Partial<OrbPrefs>;
    return {
      x: isFiniteNumber(raw.x) ? raw.x : null,
      y: isFiniteNumber(raw.y) ? raw.y : null,
      visible: typeof raw.visible === "boolean" ? raw.visible : DEFAULTS.visible,
      intensity: INTENSITIES.includes(raw.intensity as OrbIntensity)
        ? (raw.intensity as OrbIntensity)
        : DEFAULTS.intensity,
      quickAction: QUICK_ACTIONS.includes(raw.quickAction as OrbQuickAction)
        ? (raw.quickAction as OrbQuickAction)
        : DEFAULTS.quickAction,
    };
  } catch (error) {
    // A corrupt preference file must not stop the orb from appearing.
    log("error", "orb preferences unreadable; using defaults", {
      error: error instanceof Error ? error.message : String(error),
    });
    return { ...DEFAULTS };
  }
}

function writePrefs(path: string, prefs: OrbPrefs, log: Logger): void {
  try {
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, `${JSON.stringify(prefs, null, 2)}\n`, "utf8");
  } catch (error) {
    log("error", "orb preferences not saved", {
      error: error instanceof Error ? error.message : String(error),
    });
  }
}

/** Default placement: bottom-right of the primary work area. */
function defaultPosition(): { x: number; y: number } {
  const area = screen.getPrimaryDisplay().workArea;
  return {
    x: Math.round(area.x + area.width - ORB_WIDTH - 24),
    y: Math.round(area.y + area.height - ORB_HEIGHT - 24),
  };
}

/**
 * Clamp a position so the orb stays reachable.
 *
 * A saved position can be off every monitor after a display change (undocked
 * laptop, different scaling). The orb must never be stranded off-screen with no
 * way to drag it back.
 */
export function clampOrbPosition(
  x: number,
  y: number,
): { x: number; y: number } {
  const probe = { x: Math.round(x), y: Math.round(y), width: ORB_WIDTH, height: ORB_HEIGHT };
  const area = screen.getDisplayMatching(probe).workArea;
  const minX = area.x - ORB_WIDTH + MIN_VISIBLE;
  const maxX = area.x + area.width - MIN_VISIBLE;
  const minY = area.y;
  const maxY = area.y + area.height - MIN_VISIBLE;
  return {
    x: Math.min(Math.max(probe.x, minX), maxX),
    y: Math.min(Math.max(probe.y, minY), maxY),
  };
}

export function createOrbWindow(options: OrbWindowOptions): OrbHandle {
  const prefsPath = resolve(options.userDataPath, "orb-window.json");
  const prefs = readPrefs(prefsPath, options.log);
  const position = prefs.x !== null && prefs.y !== null
    ? clampOrbPosition(prefs.x, prefs.y)
    : defaultPosition();

  let orb: BrowserWindow | null = null;
  let lastVisible = prefs.visible;
  /** True while the frame is transparent to input (nothing under the cursor). */
  let releasingMouse = false;
  /** Active drag session: where the cursor sits inside the window, in DIP. */
  let drag: { offsetX: number; offsetY: number } | null = null;
  /** Guards the re-entrant `resize` → `setContentSize` round trip. */
  let enforcingSize = false;
  /** Interactive geometry reported by the renderer; `null` until it loads. */
  let hitRegion: OrbHitRegion | null = null;
  /** Whether main currently considers the cursor to be over the widget. */
  let pointerInside = false;
  let pointerTimer: NodeJS.Timeout | null = null;

  const save = (): void => writePrefs(prefsPath, prefs, options.log);

  const orbWindow = (): BrowserWindow | null =>
    orb !== null && !orb.isDestroyed() ? orb : null;

  const sendToOrb = (channel: string, payload?: unknown): void => {
    const win = orbWindow();
    if (win === null) return;
    win.webContents.send(channel, payload);
  };

  /** Switch the frame between "receives input" and "click-through". */
  const setInteractive = (interactive: boolean): void => {
    const win = orbWindow();
    if (win === null) return;
    releasingMouse = !interactive;
    win.setIgnoreMouseEvents(!interactive, { forward: true });
  };

  /**
   * Follow the OS cursor so the frame is only ever opaque to input where the
   * orb (or its caption) actually is. Runs while the widget is visible; the
   * drag loop short-circuits it because the window is following the cursor.
   */
  const pollPointer = (): void => {
    const win = orbWindow();
    if (win === null || !win.isVisible()) return;
    if (drag !== null) {
      // Mid-drag the frame must keep receiving the pointerup, whatever the
      // cursor does; the hit region is irrelevant while the window follows it.
      if (!pointerInside) {
        pointerInside = true;
        setInteractive(true);
      }
      return;
    }
    const region = hitRegion;
    if (region === null) return;
    const cursor = screen.getCursorScreenPoint();
    const [windowX, windowY] = win.getPosition();
    const x = cursor.x - windowX;
    const y = cursor.y - windowY;
    // A little slack so hovering the rim still counts; the renderer's own hit
    // test stays exact for which element a press lands on.
    const pad = 4;
    const reach = region.radius + pad;
    let inside = (x - region.x) * (x - region.x) + (y - region.y) * (y - region.y) <= reach * reach;
    if (!inside && region.caption !== null) {
      const box = region.caption;
      inside =
        x >= box.left - pad &&
        x <= box.right + pad &&
        y >= box.top - pad &&
        y <= box.bottom + pad;
    }
    if (inside === pointerInside) return;
    pointerInside = inside;
    setInteractive(inside);
    // The renderer gets no pointermove while click-through, so hover state comes
    // from here; it also lets it draw the hover ring.
    sendToOrb(IPC_CHANNELS.ORB_POINTER_INSIDE, { inside });
  };

  const startPointerPoll = (): void => {
    if (pointerTimer !== null) return;
    pointerTimer = setInterval(pollPointer, POINTER_POLL_MS);
  };

  const stopPointerPoll = (): void => {
    if (pointerTimer === null) return;
    clearInterval(pointerTimer);
    pointerTimer = null;
  };

  /**
   * The orb itself is positioned from CSS (`left: 50%`) and every hit test uses
   * the canvas' live rect, so a few DIP of frame drift cannot separate the orb
   * from its grab area. What must never happen is the frame *ballooning* (a
   * stale saved bound, a DPI change, an OS resize): the orb would then float in
   * a mostly-empty transparent window.
   *
   * Windows also reports frameless/transparent content sizes a couple of DIP off
   * what was requested (`320` → `322`/`324`), and calling `setContentSize` with
   * the "correct" value triggers another `resize` that lands on yet another
   * rounded value — an endless correction loop. So: only correct large drift,
   * and never twice in quick succession.
   */
  const SIZE_TOLERANCE = 8;
  const SIZE_COOLDOWN_MS = 750;
  let lastSizeFixAt = 0;

  const enforceSize = (reason: string): void => {
    const win = orbWindow();
    if (win === null || enforcingSize) return;
    const [width, height] = win.getContentSize();
    const drifted =
      Math.abs(width - ORB_WIDTH) > SIZE_TOLERANCE ||
      Math.abs(height - ORB_HEIGHT) > SIZE_TOLERANCE;
    if (!drifted) return;
    const now = Date.now();
    if (now - lastSizeFixAt < SIZE_COOLDOWN_MS) return;
    lastSizeFixAt = now;
    options.log("info", "orb window size corrected", {
      reason,
      actual: { width, height },
      expected: { width: ORB_WIDTH, height: ORB_HEIGHT },
    });
    enforcingSize = true;
    try {
      win.setContentSize(ORB_WIDTH, ORB_HEIGHT);
    } finally {
      enforcingSize = false;
    }
  };

  /** Persist where a drag left the orb and stop following the cursor. */
  const endDrag = (): void => {
    if (drag === null) return;
    drag = null;
    const win = orbWindow();
    if (win === null) return;
    const [x, y] = win.getPosition();
    const clamped = clampOrbPosition(x, y);
    if (clamped.x !== x || clamped.y !== y) win.setPosition(clamped.x, clamped.y);
    prefs.x = clamped.x;
    prefs.y = clamped.y;
    save();
    options.log("info", "orb drag end", { position: clamped });
  };

  /** Tell the console window whether the widget is on screen. */
  const notifyVisibility = (visible: boolean): void => {
    const main = options.getMainWindow();
    if (main === null || main.isDestroyed()) return;
    main.webContents.send(IPC_CHANNELS.ORB_VISIBILITY, { visible });
  };

  const setVisible = (visible: boolean, reason = "api"): void => {
    const win = orbWindow();
    prefs.visible = visible;
    lastVisible = visible;
    notifyVisibility(visible);
    if (!visible) endDrag();
    save();
    if (win === null) return;
    if (visible) {
      // A hidden/resized frame must come back at its designed size.
      enforceSize(`show:${reason}`);
      // showInactive keeps focus on whatever the user was typing into.
      if (!win.isVisible()) win.showInactive();
      win.setAlwaysOnTop(true, "screen-saver");
      startPointerPoll();
    } else if (win.isVisible()) {
      stopPointerPoll();
      // Hidden windows must not keep an armed, invisible hit rectangle.
      pointerInside = false;
      setInteractive(false);
      win.hide();
    }
    options.log("info", "orb visibility", { visible, reason });
  };


  const focusMain = (): BrowserWindow => {
    const main = options.ensureMainWindow();
    if (main.isMinimized()) main.restore();
    main.show();
    main.focus();
    return main;
  };

  const requestVoice = (action: "start" | "stop" | "cancel"): void => {
    const main = focusMain();
    const deliver = (): void => {
      if (!main.isDestroyed()) {
        main.webContents.send(IPC_CHANNELS.VOICE_REQUEST, { action, source: "orb" });
      }
    };
    if (main.webContents.isLoading()) main.webContents.once("did-finish-load", deliver);
    else deliver();
    if (action === "start") setVisible(true, "voice_request");
    options.log("info", "orb voice request", { action });
  };

  const pushConfig = (): void => {
    sendToOrb(IPC_CHANNELS.ORB_CONFIG_CHANGED, {
      intensity: prefs.intensity,
      quickAction: prefs.quickAction,
      visible: prefs.visible,
      backendUrl: options.backendUrl,
    });
  };

  const buildMenu = (): void => {
    const intensityItems: MenuItemConstructorOptions[] = INTENSITIES.map((value) => ({
      label: value === "low" ? "Low" : value === "high" ? "High" : "Normal",
      type: "radio",
      checked: prefs.intensity === value,
      click: () => {
        prefs.intensity = value;
        save();
        pushConfig();
      },
    }));
    const quickItems: MenuItemConstructorOptions[] = [
      {
        label: "Start voice",
        type: "radio",
        checked: prefs.quickAction === "voice",
        click: () => {
          prefs.quickAction = "voice";
          save();
          pushConfig();
        },
      },
      {
        label: "Open ULTRON",
        type: "radio",
        checked: prefs.quickAction === "open",
        click: () => {
          prefs.quickAction = "open";
          save();
          pushConfig();
        },
      },
    ];

    const template: MenuItemConstructorOptions[] = [
      { label: "Open ULTRON", click: () => void focusMain() },
      { label: "Start listening", click: () => requestVoice("start") },
      { type: "separator" },
      { label: "Hide orb", click: () => setVisible(false, "menu") },
      { label: "Double-click action", submenu: quickItems },
      { label: "Animation intensity", submenu: intensityItems },
      { type: "separator" },
      {
        label: "Reconnect",
        click: () => {
          sendToOrb(IPC_CHANNELS.ORB_RECONNECT);
          options.log("info", "orb reconnect requested");
        },
      },
      { type: "separator" },
      { label: "Quit ULTRON", click: () => options.quit() },
    ];

    const win = orbWindow();
    if (win === null) return;
    Menu.buildFromTemplate(template).popup({ window: win });
  };

  // --- window -----------------------------------------------------------
  orb = new BrowserWindow({
    width: ORB_WIDTH,
    height: ORB_HEIGHT,
    // `width`/`height` below are the page viewport, which is what the orb and
    // its hit region are laid out against.
    useContentSize: true,
    x: position.x,
    y: position.y,
    show: false,
    frame: false,
    transparent: true,
    backgroundColor: "#00000000",
    hasShadow: false,
    resizable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    focusable: true,
    // Transparent windows must not show a title; `roundedCorners` is macOS only,
    // so the rounded shape comes from the renderer's alpha.
    title: "ULTRON Orb",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      webSecurity: true,
      // Hidden windows keep their timers: the orb must still hear ORB_SHOW
      // while it is hidden, otherwise it could never come back by itself.
      backgroundThrottling: false,
      preload: options.preload,
    },
  });

  orb.setAlwaysOnTop(true, "screen-saver");
  orb.setMenuBarVisibility(false);
  // Start click-through: until the renderer reports its hit region there is
  // nothing to click, and an armed frame would swallow desktop clicks.
  orb.setIgnoreMouseEvents(true, { forward: true });
  releasingMouse = true;

  // The frame size is part of the widget's geometry contract, not a preference.
  orb.on("will-resize", (event) => {
    event.preventDefault();
    options.log("info", "orb window resize blocked", { reason: "fixed-size widget" });
  });
  orb.on("resize", () => enforceSize("resize"));

  orb.webContents.on("did-fail-load", (_event, code, description) => {
    options.log("error", "orb page failed to load", { code, description });
  });

  orb.webContents.on("did-finish-load", () => enforceSize("load"));

  orb.once("ready-to-show", () => {
    enforceSize("ready");
    if (lastVisible) {
      orb?.showInactive();
      startPointerPoll();
    }
  });

  orb.on("closed", () => {
    stopPointerPoll();
    drag = null;
    orb = null;
  });


  void orb.loadURL(`${options.origin}/__orb/index.html`);

  // Keep the orb on an existing display when monitors are added/removed.
  const reclamp = (): void => {
    const win = orbWindow();
    if (win === null) return;
    const [x, y] = win.getPosition();
    const clamped = clampOrbPosition(x, y);
    if (clamped.x !== x || clamped.y !== y) win.setPosition(clamped.x, clamped.y);
  };
  screen.on("display-removed", reclamp);
  screen.on("display-metrics-changed", reclamp);

  // --- IPC --------------------------------------------------------------
  const isOrbSender = (event: { sender: Electron.WebContents }): boolean => {
    const win = orbWindow();
    return win !== null && event.sender === win.webContents;
  };

  ipcMain.handle(IPC_CHANNELS.ORB_GET_CONFIG, (event) => {
    if (!isOrbSender(event)) return null;
    const win = orbWindow();
    const [x, y] = win?.getPosition() ?? [position.x, position.y];
    const [width, height] = win?.getContentSize() ?? [ORB_WIDTH, ORB_HEIGHT];
    return {
      position: { x, y },
      // Live viewport, not the constant: if the frame is ever off by a pixel the
      // renderer can lay the orb out against reality instead of an assumption.
      bounds: { width, height },
      visible: prefs.visible,
      intensity: prefs.intensity,
      quickAction: prefs.quickAction,
      origin: options.origin,
      backendUrl: options.backendUrl,
      version: app.getVersion(),
    };
  });


  ipcMain.handle(IPC_CHANNELS.ORB_SET_VISIBLE, (event, visible: unknown) => {
    if (!isOrbSender(event) || typeof visible !== "boolean") return;
    setVisible(visible, "renderer");
  });

  ipcMain.handle(IPC_CHANNELS.ORB_SET_POSITION, (event, payload: unknown) => {
    if (!isOrbSender(event)) return;
    const value = payload as { x?: unknown; y?: unknown } | null;
    if (value === null || !isFiniteNumber(value.x) || !isFiniteNumber(value.y)) return;
    const clamped = clampOrbPosition(value.x, value.y);
    prefs.x = clamped.x;
    prefs.y = clamped.y;
    save();
    orbWindow()?.setPosition(clamped.x, clamped.y);
  });

  ipcMain.on(IPC_CHANNELS.ORB_MOVE, (event, payload: unknown) => {
    if (!isOrbSender(event)) return;
    const value = payload as { x?: unknown; y?: unknown } | null;
    if (value === null || !isFiniteNumber(value.x) || !isFiniteNumber(value.y)) return;
    const clamped = clampOrbPosition(value.x, value.y);
    orbWindow()?.setPosition(clamped.x, clamped.y);
  });

  // --- drag -------------------------------------------------------------
  // The renderer reports "the user is still holding the orb"; the *main*
  // process reads the global cursor and places the window at `cursor - grab`.
  // No screen-coordinate deltas are accumulated in the page, so a dropped
  // pointermove, a DPI change or a window that was resized behind our back can
  // never separate the visible orb from the frame that receives the clicks.
  ipcMain.on(IPC_CHANNELS.ORB_DRAG_START, (event, payload: unknown) => {
    if (!isOrbSender(event)) return;
    const win = orbWindow();
    if (win === null) return;
    const [windowX, windowY] = win.getPosition();
    const [width, height] = win.getContentSize();
    const value = payload as { x?: unknown; y?: unknown } | null;
    let offsetX = Number.NaN;
    let offsetY = Number.NaN;
    if (value !== null && isFiniteNumber(value.x) && isFiniteNumber(value.y)) {
      offsetX = value.x;
      offsetY = value.y;
    }
    // The renderer's estimate is used when it is plausible; otherwise the grab
    // point is derived from where the cursor actually is right now.
    const plausible = (n: number, size: number): boolean => n >= -8 && n <= size + 8;
    if (!plausible(offsetX, width) || !plausible(offsetY, height)) {
      const cursor = screen.getCursorScreenPoint();
      offsetX = cursor.x - windowX;
      offsetY = cursor.y - windowY;
    }
    drag = { offsetX, offsetY };
    options.log("info", "orb drag start", {
      grab: { x: Math.round(offsetX), y: Math.round(offsetY) },
      position: { x: windowX, y: windowY },
    });
  });

  ipcMain.on(IPC_CHANNELS.ORB_DRAG_MOVE, (event) => {
    if (!isOrbSender(event) || drag === null) return;
    const win = orbWindow();
    if (win === null) {
      drag = null;
      return;
    }
    const cursor = screen.getCursorScreenPoint();
    const clamped = clampOrbPosition(cursor.x - drag.offsetX, cursor.y - drag.offsetY);
    win.setPosition(clamped.x, clamped.y);
  });

  ipcMain.on(IPC_CHANNELS.ORB_DRAG_END, (event) => {
    if (!isOrbSender(event)) return;
    endDrag();
  });

  // The renderer reports where the orb (and its caption) actually is; main then
  // decides from the OS cursor whether the frame should accept input. Keeping
  // this in one place means the hit area is always the painted orb, and a
  // click-through frame can still be re-armed when the cursor comes back.
  ipcMain.on(IPC_CHANNELS.ORB_SET_HIT_REGION, (event, payload: unknown) => {
    if (!isOrbSender(event)) return;
    const value = payload as
      | {
          x?: unknown;
          y?: unknown;
          radius?: unknown;
          caption?: { left?: unknown; top?: unknown; right?: unknown; bottom?: unknown } | null;
        }
      | null;
    if (
      value === null ||
      !isFiniteNumber(value.x) ||
      !isFiniteNumber(value.y) ||
      !isFiniteNumber(value.radius)
    ) {
      return;
    }
    const caption = value.caption ?? null;
    hitRegion = {
      x: value.x,
      y: value.y,
      radius: Math.max(8, value.radius),
      caption:
        caption !== null &&
        isFiniteNumber(caption.left) &&
        isFiniteNumber(caption.top) &&
        isFiniteNumber(caption.right) &&
        isFiniteNumber(caption.bottom)
          ? {
              left: caption.left,
              top: caption.top,
              right: caption.right,
              bottom: caption.bottom,
            }
          : null,
    };
    // Apply immediately so the first hover does not wait for the next tick.
    pollPointer();
  });

  ipcMain.on(IPC_CHANNELS.ORB_HIDE, (event) => {
    if (!isOrbSender(event)) return;
    setVisible(false, "renderer");
  });

  ipcMain.on(IPC_CHANNELS.ORB_OPEN_MAIN, (event) => {
    if (!isOrbSender(event)) return;
    void focusMain();
  });

  ipcMain.on(IPC_CHANNELS.ORB_START_VOICE, (event) => {
    if (!isOrbSender(event)) return;
    requestVoice("start");
  });

  ipcMain.on(IPC_CHANNELS.ORB_STOP_VOICE, (event) => {
    if (!isOrbSender(event)) return;
    requestVoice("stop");
  });

  ipcMain.on(IPC_CHANNELS.ORB_CANCEL, (event) => {
    if (!isOrbSender(event)) return;
    requestVoice("cancel");
  });

  ipcMain.on(IPC_CHANNELS.ORB_QUICK_ACTION, (event) => {
    if (!isOrbSender(event)) return;
    if (prefs.quickAction === "voice") requestVoice("start");
    else void focusMain();
  });

  ipcMain.on(IPC_CHANNELS.ORB_MENU, (event) => {
    if (!isOrbSender(event)) return;
    buildMenu();
  });

  ipcMain.on(IPC_CHANNELS.ORB_QUIT, (event) => {
    if (!isOrbSender(event)) return;
    options.quit();
  });

  const [createdWidth, createdHeight] = orb.getContentSize();
  options.log("info", "orb window created", {
    position,
    size: { width: createdWidth, height: createdHeight },
    visible: prefs.visible,
    intensity: prefs.intensity,
    releasePassThrough: releasingMouse,
  });

  return {
    window: orb,
    isVisible: () => orbWindow()?.isVisible() ?? false,
    setVisible: (visible: boolean) => setVisible(visible, "host"),
    destroy: () => {
      stopPointerPoll();
      screen.removeListener("display-removed", reclamp);
      screen.removeListener("display-metrics-changed", reclamp);
      drag = null;
      const win = orbWindow();
      orb = null;
      win?.destroy();
    },
  };
}

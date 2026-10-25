/**
 * ULTRON Electron main process (T516).
 *
 * Wraps the existing UIPROTO console — never re-implements it (§69.3/D040):
 *
 * - development: loads the `vite dev` server (ULTRON_DEV_SERVER_URL), where
 *   the existing proxy already forwards the API prefixes to the backend.
 * - production: serves the published `server/app/static/` bundle through a
 *   loopback static server (the SPA uses absolute `/assets/...` URLs, so
 *   `file://` cannot work), keeping every URL identical to the FastAPI mount.
 *
 * Security baseline from day one (§69.4/D041): contextIsolation on, renderer
 * Node integration off, no preload yet (T517 adds the narrow bridge), no
 * navigation to remote origins (T518 hardens further).
 *
 * Usage:
 *   electron .                 normal window (production static server)
 *   ULTRON_DEV_SERVER_URL=...  load the vite dev server instead
 *   electron . --smoke         load, wait for render, print result, exit
 *                              (headless-friendly gate for CI/this machine)
 */
import { app, BrowserWindow, ipcMain } from "electron";
import { existsSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

import { IPC_CHANNELS } from "./ipc-channels.js";
import { LifecycleTracker } from "./lifecycle.js";
import { createOrbWindow } from "./orb-window.js";
import type { OrbHandle } from "./orb-window.js";
import { startStaticServer } from "./static-server.js";
import type { StaticServerHandle } from "./static-server.js";
import { startWindowsNode, WindowsNode } from "./windows-node.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const SMOKE = process.argv.includes("--smoke");
const DEV_URL = process.env.ULTRON_DEV_SERVER_URL ?? "";
const BACKEND_URL = process.env.ULTRON_BACKEND_URL ?? "http://127.0.0.1:8000";

// clients/electron/dist -> clients/electron -> clients -> repo root
const REPO_ROOT = resolve(__dirname, "..", "..", "..");
const PRODUCTION_BUNDLE = resolve(REPO_ROOT, "server", "app", "static");
/** Compiled floating-orb renderer (`dist/orb`), mounted at `/__orb`. */
const ORB_BUNDLE = resolve(__dirname, "orb");

function log(level: "info" | "error", message: string, extra?: Record<string, unknown>): void {
  const entry = {
    ts: new Date().toISOString(),
    component: "electron-main",
    level,
    message,
    ...extra,
  };
  const line = JSON.stringify(entry);
  if (level === "error") console.error(line);
  else console.log(line);
}

let staticHandle: StaticServerHandle | null = null;
let tracker: LifecycleTracker | null = null;
let windowsNode: WindowsNode | null = null;
let mainWindow: BrowserWindow | null = null;
let orbHandle: OrbHandle | null = null;
/** Start URL captured at boot, so a closed console can be reopened. */
let appStartURL = "";

async function resolveStartURL(): Promise<string> {
  if (DEV_URL) {
    log("info", "loading dev server", { url: DEV_URL });
    return DEV_URL;
  }
  if (!existsSync(resolve(PRODUCTION_BUNDLE, "index.html"))) {
    throw new Error(
      `published bundle not found at ${PRODUCTION_BUNDLE} — run the UIPROTO publish step first`,
    );
  }
  staticHandle = await startStaticServer(
    PRODUCTION_BUNDLE,
    { backendUrl: BACKEND_URL, orbRoot: ORB_BUNDLE },
    log,
  );
  log("info", "serving production bundle", {
    origin: staticHandle.origin,
    root: PRODUCTION_BUNDLE,
    backendUrl: BACKEND_URL,
  });
  return staticHandle.origin + "/";
}

function createWindow(startURL: string): BrowserWindow {
  const win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1024,
    minHeight: 640,
    show: false,
    backgroundColor: "#05070c",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      webSecurity: true,
      preload: resolve(__dirname, "preload.js"),
    },
  });

  win.once("ready-to-show", () => {
    if (!SMOKE) win.show();
  });

  win.on("closed", () => {
    if (mainWindow === win) mainWindow = null;
    // Closing the console is not quitting while the standalone orb is still on
    // screen — the orb is the widget and can reopen the console. With no
    // visible orb there is nothing left to interact with, so end the process.
    if (!SMOKE && (orbHandle === null || !orbHandle.isVisible())) {
      log("info", "console closed with no visible orb; quitting");
      app.quit();
    }
  });

  mainWindow = win;
  void win.loadURL(startURL);
  return win;
}

/**
 * Return the console window, recreating it if the user closed it while the orb
 * stayed on screen. The orb's "open ULTRON" gesture must always have a target.
 */
function ensureMainWindow(): BrowserWindow {
  const existing = mainWindow;
  if (existing !== null && !existing.isDestroyed()) return existing;
  const win = createWindow(appStartURL);
  registerNavigationGuards(win, new URL(appStartURL).origin);
  return win;
}

/**
 * Origin the orb renderer loads from. In production it is the same static
 * server as the console; in dev the server is started here anyway so the orb
 * gets its assets and the SSE/WS proxy without a second backend client.
 */
async function resolveOrbOrigin(): Promise<string> {
  if (staticHandle !== null) return staticHandle.origin;
  if (!existsSync(resolve(PRODUCTION_BUNDLE, "index.html"))) {
    throw new Error(
      `published bundle not found at ${PRODUCTION_BUNDLE} — the orb renders from it`,
    );
  }
  staticHandle = await startStaticServer(
    PRODUCTION_BUNDLE,
    { backendUrl: BACKEND_URL, orbRoot: ORB_BUNDLE },
    log,
  );
  return staticHandle.origin;
}

function registerIpcHandlers(
  getWindow: () => BrowserWindow | null,
  tracker: LifecycleTracker,
): void {
  ipcMain.handle(IPC_CHANNELS.APP_VERSION, () => app.getVersion());
  ipcMain.handle(IPC_CHANNELS.BACKEND_INFO, () => ({ backendUrl: BACKEND_URL }));
  ipcMain.handle(IPC_CHANNELS.LIFECYCLE_GET, () => tracker.info);
  ipcMain.handle(
    IPC_CHANNELS.LIFECYCLE_REPORT,
    (_event, payload: { agentCount: number; taskCount: number }) => {
      tracker.reportActivity(payload.agentCount, payload.taskCount);
    },
  );

  // Window channels resolve the *current* console window: the user can close it
  // and reopen it from the orb, and the handlers must follow that instance.
  const windowOrNull = (): BrowserWindow | null => {
    const win = getWindow();
    return win !== null && !win.isDestroyed() ? win : null;
  };

  ipcMain.handle(IPC_CHANNELS.WINDOW_MINIMIZE, () => windowOrNull()?.minimize());
  ipcMain.handle(IPC_CHANNELS.WINDOW_MAXIMIZE, () => windowOrNull()?.maximize());
  ipcMain.handle(IPC_CHANNELS.WINDOW_CLOSE, () => windowOrNull()?.close());
  ipcMain.handle(IPC_CHANNELS.WINDOW_GET_STATE, () => {
    const win = windowOrNull();
    if (win === null) return null;
    return {
      isMaximized: win.isMaximized(),
      isMinimized: win.isMinimized(),
      isFullScreen: win.isFullScreen(),
      bounds: win.getBounds(),
      isAlwaysOnTop: win.isAlwaysOnTop(),
    };
  });
  ipcMain.handle(
    IPC_CHANNELS.WINDOW_SET_BOUNDS,
    (_event, bounds: { x?: number; y?: number; width?: number; height?: number }) => {
      windowOrNull()?.setBounds(bounds);
    },
  );
  ipcMain.handle(IPC_CHANNELS.WINDOW_TOGGLE_FULLSCREEN, () => {
    const win = windowOrNull();
    if (win === null) return;
    win.setFullScreen(!win.isFullScreen());
  });
  ipcMain.handle(
    IPC_CHANNELS.WINDOW_SET_ALWAYS_ON_TOP,
    (_event, flag: boolean) => {
      windowOrNull()?.setAlwaysOnTop(flag);
    },
  );

  // --- floating orb controls (console side) ---
  // The orb hides itself from its own context menu; without these two channels
  // the only way back would be editing the preference file by hand.
  ipcMain.handle(IPC_CHANNELS.ORB_SHOW, (_event, visible: unknown) => {
    const handle = orbHandle;
    if (handle === null) return { visible: false };
    handle.setVisible(typeof visible === "boolean" ? visible : true);
    return { visible: handle.isVisible() };
  });
  ipcMain.handle(IPC_CHANNELS.ORB_GET_STATE, () => ({
    visible: orbHandle?.isVisible() ?? false,
  }));

  // --- Windows tool bridge (T262, §59.15) ---
  // These delegate to the Windows node that connects outbound to the server.
  ipcMain.handle(
    IPC_CHANNELS.TOOL_LIST_WINDOWS,
    async () => {
      const result = await windowsNode?.listWindows();
      return result ?? { windows: [] };
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.TOOL_SCREENSHOT,
    async (_event, args?: { window_id?: string; name?: string }) => {
      const result = await windowsNode?.screenshot(args ?? {});
      return result ?? { path: "" };
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.TOOL_CLICK,
    async (_event, args?: { x: number; y: number; button?: string; clicks?: number }) => {
      const result = await windowsNode?.click(args ?? { x: 0, y: 0 });
      return result ?? {};
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.TOOL_TYPE,
    async (_event, args?: { text: string }) => {
      const result = await windowsNode?.type(args ?? { text: "" });
      return result ?? {};
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.TOOL_KEYS,
    async (_event, args?: { keys: string | string[] }) => {
      const result = await windowsNode?.keys(args ?? { keys: "" });
      return result ?? {};
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.TOOL_OPEN_APP,
    async (_event, args?: { application: string; arguments?: string[] }) => {
      const result = await windowsNode?.openApplication(args ?? { application: "" });
      return result ?? {};
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.TOOL_CLOSE_APP,
    async (_event, args?: { application?: string; window_id?: string }) => {
      const result = await windowsNode?.closeApplication(args ?? {});
      return result ?? {};
    },
  );
}

function registerNavigationGuards(win: BrowserWindow, allowedOrigin: string): void {
  win.webContents.setWindowOpenHandler(() => {
    log("info", "blocked window.open");
    return { action: "deny" };
  });

  win.webContents.on("will-navigate", (event, url) => {
    const target = new URL(url);
    if (target.origin !== allowedOrigin) {
      log("info", "blocked external navigation", { url, allowedOrigin });
      event.preventDefault();
    }
  });
}

async function main(): Promise<void> {
  const startURL = await resolveStartURL();
  appStartURL = startURL;
  const win = createWindow(startURL);

  tracker = new LifecycleTracker({
    backendUrl: BACKEND_URL,
    onState: (state) => {
      // Follow the *current* console window: it can be closed and reopened from
      // the orb, so sending to the instance captured at boot would lose states.
      const target = mainWindow;
      if (target !== null && !target.isDestroyed()) {
        target.webContents.send(IPC_CHANNELS.LIFECYCLE_STATE, state);
      }
    },
    onError: (message) => log("error", "lifecycle", { message }),
  });
  tracker.start();

  registerIpcHandlers(() => mainWindow, tracker);

  const allowedOrigin = new URL(startURL).origin;
  registerNavigationGuards(win, allowedOrigin);

  // The floating orb is a *separate*, always-on-top OS window (spec §1/§6), not
  // a panel inside the console. It is created after the console so the IPC
  // handlers and the static server it renders from already exist. `--smoke`
  // asserts the console only, so the orb is skipped there.
  if (!SMOKE) {
    try {
      const orbOrigin = await resolveOrbOrigin();
      orbHandle = createOrbWindow({
        origin: orbOrigin,
        preload: resolve(__dirname, "orb-preload.js"),
        backendUrl: BACKEND_URL,
        userDataPath: app.getPath("userData"),
        getMainWindow: () => mainWindow,
        ensureMainWindow,
        log,
        quit: () => app.quit(),
      });
      log("info", "floating orb window ready", {
        origin: orbOrigin,
        visible: orbHandle.isVisible(),
      });
    } catch (error) {
      // A missing/failed orb must not take the console down with it (§3: a
      // degraded capability is reported, not fatal).
      log("error", "floating orb window could not be created", {
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }

  if (SMOKE) {
    // Automated gate: wait for the renderer to report a mounted shell, then
    // exit. No window is shown; no backend is contacted or required.
    const timeout = setTimeout(() => {
      log("error", "smoke timed out waiting for renderer");
      app.exit(2);
    }, 30_000);

    win.webContents.once("did-finish-load", () => {
      // Give the SPA a beat to hydrate the boot shell.
      setTimeout(() => {
        win.webContents
          .executeJavaScript(
            `(async () => {
              const hasNode = typeof process !== 'undefined' || typeof require !== 'undefined';
              const hasUltron = typeof window.ultron !== 'undefined';
              let appVersion = null;
              let backendInfo = null;
              if (hasUltron) {
                try { appVersion = await window.ultron.getAppVersion(); } catch {}
                try { backendInfo = await window.ultron.getBackendInfo(); } catch {}
              }
              const initialUrl = location.href;
              try { window.location.href = 'https://evil.example.com/'; } catch {}
              await new Promise(r => setTimeout(r, 500));
              const navBlocked = location.href === initialUrl;
              let openDenied = false;
              try { openDenied = window.open('https://evil.example.com/') === null; } catch { openDenied = true; }
              let proxyStatus = null;
              let proxyBody = null;
              try {
                const r = await fetch('/health');
                proxyStatus = r.status;
                proxyBody = (await r.text()).slice(0, 200);
              } catch (e) { proxyStatus = 'fetch-error'; proxyBody = String(e).slice(0, 100); }
              let wsConnected = false;
              let wsMessage = null;
              try {
                const ws = new WebSocket('ws://' + location.host + '/nodes/ws');
                const wsResult = await Promise.race([
                  new Promise((resolve) => {
                    ws.onmessage = (ev) => { wsConnected = true; wsMessage = String(ev.data).slice(0, 200); resolve('ok'); };
                    ws.onerror = () => resolve('error');
                  }),
                  new Promise((resolve) => setTimeout(() => resolve('timeout'), 5000)),
                ]);
                wsConnected = wsResult === 'ok';
                wsMessage = wsResult === 'ok' ? wsMessage : wsResult;
                ws.close();
              } catch (e) { wsMessage = String(e).slice(0, 100); }
              let lcStates = [];
              let lcInfo = null;
              try {
                const probe = async (label, agents, tasks) => {
                  await (window.ultron).reportActivity(agents, tasks);
                  await new Promise((r) => setTimeout(r, 300));
                  const info = await (window.ultron).getLifecycleInfo();
                  lcStates.push(label + ":" + info.state);
                  return info.state;
                };
                lcInfo = await (window.ultron).getLifecycleInfo();
                lcStates.push("initial:" + String(lcInfo.state));
                await probe("agents", 3, 0);
                await probe("tasks", 0, 2);
                await probe("idle", 0, 0);
                lcInfo = await (window.ultron).getLifecycleInfo();
                lcStates.push("final:" + String(lcInfo.state));
              } catch (e) {
                lcStates.push("error:" + String(e).slice(0, 80));
              }
              return {
                url: location.href,
                title: document.title,
                hasShell: !!document.querySelector('.ultron-shell'),
                text: (document.body.innerText || '').slice(0, 120),
                hasNode,
                hasUltron,
                appVersion,
                backendInfo,
                navBlocked,
                openDenied,
                proxyStatus,
                proxyBody,
                wsConnected,
                wsMessage,
                lcStates,
              };
            })()`,
          )
          .then((result) => {
            clearTimeout(timeout);
            const r = result as {
              hasShell?: boolean;
              hasNode?: boolean;
              hasUltron?: boolean;
              appVersion?: string | null;
              backendInfo?: { backendUrl?: string } | null;
              navBlocked?: boolean;
              openDenied?: boolean;
              proxyStatus?: number | string;
              proxyBody?: string;
              wsConnected?: boolean;
              wsMessage?: string | null;
              lcStates?: string[];
            };
            const lcOk = Array.isArray(r.lcStates) &&
              r.lcStates.some((s) => s.includes(":agent-active")) &&
              r.lcStates.some((s) => s.includes(":task-executing")) &&
              r.lcStates[r.lcStates.length - 1]?.endsWith(":idle");
            const ok = r.hasShell === true && r.hasNode === false && r.hasUltron === true && !!r.appVersion && r.navBlocked === true && r.openDenied === true && r.backendInfo?.backendUrl === 'http://127.0.0.1:8000' && (r.proxyStatus === 200 || r.proxyStatus === 'fetch-error') && (r.wsConnected === true || r.wsMessage === 'timeout') && lcOk;
            log(ok ? "info" : "error", "smoke render result", { ...result });
            app.exit(ok ? 0 : 1);
          })
          .catch((error: unknown) => {
            clearTimeout(timeout);
            log("error", "smoke executeJavaScript failed", {
              error: error instanceof Error ? error.message : String(error),
            });
            app.exit(1);
          });
      }, 1_500);
    });

    win.webContents.once("did-fail-load", (_event, code, description) => {
      clearTimeout(timeout);
      log("error", "smoke page load failed", { code, description });
      app.exit(1);
    });
  }

}

// One ULTRON per desktop: a second launch focuses the existing console instead
// of starting a second orb beside the first.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (!appStartURL) return; // still booting; the first window will show
    ensureMainWindow();
    const target = mainWindow;
    if (target === null || target.isDestroyed()) return;
    if (target.isMinimized()) target.restore();
    target.show();
    target.focus();
  });

  app
    .whenReady()
    .then(() => {
      void main();
      // Start the Windows node bridge that connects back to the server
      // (§59.15): outbound WebSocket only, no inbound LAN listener
      windowsNode = startWindowsNode(BACKEND_URL);
      log("info", "Windows node bridge started", {
        serverUrl: BACKEND_URL,
      });
    })
    .catch((error: unknown) => {
      log("error", "main process failed to start", {
        error: error instanceof Error ? error.message : String(error),
      });
      app.exit(1);
    });

  // macOS dock activation: reopen the console, which may have been closed while
  // the orb stayed on screen.
  app.on("activate", () => {
    if (!appStartURL) return;
    ensureMainWindow();
  });
}

app.on("window-all-closed", () => {
  // The orb is a window too. If it still exists the process must stay up, and
  // if it was hidden with the console closed, `createWindow`'s closed handler
  // has already ended the app.
  if (orbHandle === null) app.quit();
});

app.on("before-quit", () => {
  log("info", "shutting down");
  tracker?.shutdown();
  // Destroy the orb explicitly so its screen listeners and IPC handlers go
  // away; leaving them registered keeps the process alive after quit.
  orbHandle?.destroy();
  orbHandle = null;
  windowsNode?.stop();
  if (staticHandle) {
    void staticHandle.close();
    staticHandle = null;
  }
});

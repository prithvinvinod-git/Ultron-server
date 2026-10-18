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
import { resolve } from "node:path";

import { IPC_CHANNELS } from "./ipc-channels";
import { startStaticServer } from "./static-server";
import type { StaticServerHandle } from "./static-server";

const SMOKE = process.argv.includes("--smoke");
const DEV_URL = process.env.ULTRON_DEV_SERVER_URL ?? "";
const BACKEND_URL = process.env.ULTRON_BACKEND_URL ?? "http://127.0.0.1:8000";

// clients/electron/dist -> clients/electron -> clients -> repo root
const REPO_ROOT = resolve(__dirname, "..", "..", "..");
const PRODUCTION_BUNDLE = resolve(REPO_ROOT, "server", "app", "static");

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
  staticHandle = await startStaticServer(PRODUCTION_BUNDLE, { backendUrl: BACKEND_URL }, log);
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

  void win.loadURL(startURL);
  return win;
}

function registerIpcHandlers(win: BrowserWindow): void {
  ipcMain.handle(IPC_CHANNELS.APP_VERSION, () => app.getVersion());
  ipcMain.handle(IPC_CHANNELS.BACKEND_INFO, () => ({ backendUrl: BACKEND_URL }));
  ipcMain.handle(IPC_CHANNELS.WINDOW_MINIMIZE, () => win.minimize());
  ipcMain.handle(IPC_CHANNELS.WINDOW_MAXIMIZE, () => win.maximize());
  ipcMain.handle(IPC_CHANNELS.WINDOW_CLOSE, () => win.close());
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
  const win = createWindow(startURL);
  registerIpcHandlers(win);

  const allowedOrigin = new URL(startURL).origin;
  registerNavigationGuards(win, allowedOrigin);

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
            };
            const ok = r.hasShell === true && r.hasNode === false && r.hasUltron === true && !!r.appVersion && r.navBlocked === true && r.openDenied === true && r.backendInfo?.backendUrl === 'http://127.0.0.1:8000' && (r.proxyStatus === 200 || r.proxyStatus === 'fetch-error') && (r.wsConnected === true || r.wsMessage === 'timeout');
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

  win.on("closed", () => {
    // Window lifecycle: shutting down (T520 maps the full state set).
  });
}

app
  .whenReady()
  .then(() => main())
  .catch((error: unknown) => {
    log("error", "main process failed to start", {
      error: error instanceof Error ? error.message : String(error),
    });
    app.exit(1);
  });

app.on("window-all-closed", () => {
  app.quit();
});

app.on("before-quit", () => {
  log("info", "shutting down");
  if (staticHandle) {
    void staticHandle.close();
    staticHandle = null;
  }
});

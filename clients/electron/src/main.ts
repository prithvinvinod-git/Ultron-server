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
import { app, BrowserWindow } from "electron";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

import { startStaticServer } from "./static-server";
import type { StaticServerHandle } from "./static-server";

const SMOKE = process.argv.includes("--smoke");
const DEV_URL = process.env.ULTRON_DEV_SERVER_URL ?? "";

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
  staticHandle = await startStaticServer(PRODUCTION_BUNDLE);
  log("info", "serving production bundle", {
    origin: staticHandle.origin,
    root: PRODUCTION_BUNDLE,
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
      sandbox: true,
      webSecurity: true,
    },
  });

  win.once("ready-to-show", () => {
    if (!SMOKE) win.show();
  });

  void win.loadURL(startURL);
  return win;
}

async function main(): Promise<void> {
  const startURL = await resolveStartURL();
  const win = createWindow(startURL);

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
            `(() => ({
              url: location.href,
              title: document.title,
              hasShell: !!document.querySelector('.ultron-shell'),
              text: (document.body.innerText || '').slice(0, 120),
            }))()`,
          )
          .then((result) => {
            clearTimeout(timeout);
            const ok = (result as { hasShell?: boolean }).hasShell === true;
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

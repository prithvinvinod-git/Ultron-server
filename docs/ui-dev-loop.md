# UI development loop (live refresh on the Windows dev system)

Build/test happens **on this Windows dev system — no remote server**. The
remote/server stage is **post-build only** (clone-and-run on the author's
server). The client UI/Electron shell may run here against a local `vite dev`
server; the runtime (uvicorn/worker/scheduler/voice/device bridge) never runs
here (§61, `docs/uiclient-conflict-electron-launch.md`).

## The dev server

`UIPROTO` runs Vite and serves on **http://localhost:8080** (default from
`@lovable.dev/vite-tanstack-config`; `vite.config.ts` adds the API proxy).

```
cd UIPROTO
npm run dev
```

- HMR is on: saving any file under `src/` updates the running page instantly,
  with no rebuild and no page reload (state is preserved where possible).
- The proxy forwards every ULTRON API prefix (`/auth`, `/chat`, `/events`,
  `/agents`, `/tasks`, `/tools`, `/nodes`, `/devices`, `/voice`, `/schedules`,
  `/notifications`, health) to `http://127.0.0.1:8000`. With **no backend
  running**, those calls fail — that is expected here (the runtime is not
  started on this machine). Screens that read live data will show their
  disconnected/empty states; pure visual work is unaffected.

## Two ways to see it live

### Option A — Browser (fastest, recommended for visual work)

1. `cd UIPROTO` then `npm run dev`.
2. Open **http://localhost:8080** in a normal browser.

Fastest refresh and no Electron build step. You do **not** get Electron-only
chrome (window controls, IPC lifecycle states, `isMaximized`). Auth is bypassed
in the UI (D046), so the console opens directly.

### Option B — Electron + Vite (the real shell, still instant)

Terminal 1:

```
cd UIPROTO
npm run dev
```

Terminal 2:

```
cd clients/electron
npm run dev:app
```

`dev:app` sets `ULTRON_DEV_SERVER_URL=http://localhost:8080`, builds the main
process, and launches Electron which loads the **dev server** instead of the
static bundle (`clients/electron/src/main.ts:56`). Same HMR, inside the real
Electron shell with IPC and window chrome.

> The Vite server picks 8080 by default; if you change it, update
> `ULTRON_DEV_SERVER_URL` in both `start:dev` and `dev:app`.

## What this does *not* verify

Because no backend runs here, the following stay **unverified** until the code
runs on the author's server (§69.11): live REST/SSE/WS data, reconnect-after-
restart, auth-failure against a real server, and any agent/task runtime
behaviour. Visual and layout work is fully exercisable here.

## Production path (unchanged)

`UIPROTO` `npm run build` → publish to `server/app/static` → Electron
`npm run start` (`clients/electron/src/main.ts` loopback static server). This is
the release loop, not for iteration.

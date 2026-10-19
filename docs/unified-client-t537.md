# T537: Unified-client final verification

One honest closing gate (§69.11/§69.12). Commands and results are recorded
verbatim. "ULTRON has a unified client" is claimed **only** for the parts actually
built and tested.

## Commands run (verbatim results)

| Command | Result |
|---|---|
| `UIPROTO` → `npm run build` | **exit 0** — vite build + prerender of `/`; `✓ built in 1.35s` |
| `UIPROTO` → `.\node_modules\.bin\tsc --noEmit` | **exit 0** |
| `clients/electron` → `npm run typecheck` | **exit 0** |
| `clients/electron` → `npm run smoke` | **exit 1** — shell/IPC/security green; live-backend assertions unavailable (backend not running) |
| ESP32 compile | **not run** — no ESP32 toolchain installed (`arduino-cli`/`platformio`/`pio` absent; only host `gcc`) |

### Electron smoke detail

The smoke test launched the production bundle from `server/app/static` on a
loopback origin. Verbatim result fields:

- `title`: `Ultron — Core Command Center`
- `hasShell`: `true` — the command center rendered
- `hasNode`: `false` — no Node globals in the renderer (contextIsolation holds)
- `hasUltron`: `true` — the contextBridge surface is exposed
- `appVersion`: `0.1.0` — an IPC round-trip worked
- `navBlocked`: `true` — external navigation blocked
- `openDenied`: `true` — `window.open` denied
- `proxyStatus`: `502`, `proxyBody`: `BACKEND_UNREACHABLE` (backend down)
- `wsConnected`: `false` (backend down)
- `lcStates`: `initial:disconnected → agents:disconnected → tasks:disconnected →
  idle:disconnected → final:disconnected`

Exit 1 is caused by the **backend being unreachable**, not by a UI regression.
The earlier T522 run (with a live backend) recorded `proxyStatus: 200` and
`lcStates` `initial:connected → agent-active → task-executing → idle → idle`.
That live-path evidence is from T522; it is **not** re-confirmed in this session
because the backend was down, and starting it is a machine-restricted action.

## Status by part

### Implemented and verified (this session or T516-T528)

| Part | Verified by |
|---|---|
| Console (UIPROTO) builds | `npm run build` exit 0 |
| Console type-safe | `tsc --noEmit` exit 0 |
| Responsive PWA layout (mobile CSS) | `tsc` exit 0; **visual rendering unverified** |
| Electron type-safe | `npm run typecheck` exit 0 |
| Electron shell loads the existing UI | smoke `hasShell: true` |
| Electron IPC bridge (contextIsolation, no Node) | smoke `hasNode:false`, `hasUltron:true`, `appVersion:0.1.0` |
| Electron navigation security | smoke `navBlocked:true`, `openDenied:true` |
| Electron production packaging | T522 (`ULTRON.exe` produced) |

### Implemented but unverified

| Part | Why |
|---|---|
| Live-backend REST/WS/reconnect (T519/T520) | Backend not running this session |
| PWA install prompt + offline load (T527) | No browser driven |
| PWA mobile visual rendering (T528) | No browser driven |
| Session auth paths (login/refresh) | Gate bypassed by directive (D046) |

### Planned / blocked (not built)

| Part | Status |
|---|---|
| ESP32 firmware compilation (T534) | **unverified** — no toolchain |
| ESP32 screen rendering / touch / reconnect (T532/T533) | **unverified** — no hardware |
| ESP32 hardware pins/controllers (T529) | **blocked** — spec unknown |
| Command-center panels T511 (Schedules), T512 (Notifications), T513 (wiring) | **open** — outside the client ledger |

## Honest conclusion

The unified **desktop** client is built and verified for build/type/IPC/security
and packaging; its live-backend paths are verified historically (T522) but not
re-confirmed this session. The **PWA** shares the console and is type-checked;
its runtime behaviour is unverified. The **ESP32** client is a written skeleton
with hardware and compilation **unverified**.

"ULTRON has a unified client" is therefore claimed for: the Electron desktop
shell wrapping the existing console, the shared contract/types package, and the
responsive PWA layout at the type/build level. It is **not** claimed for the
ESP32 firmware's compilation or hardware behaviour, nor for any runtime path that
requires a live backend, browser, or device that was unavailable here.

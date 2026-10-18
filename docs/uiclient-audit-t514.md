# T514 — Client Architecture Audit & Implementation Report

Gate for `tasks.md` **T514** (§69.2/§69.12). Read-only audit: no application
code was changed by this task.

## 1. Naming reconciliation

The originating directive names `uiproto` and `ultron_arc.md`. In this
repository the actual names are:

| Directive name | Actual |
|---|---|
| `uiproto` | `UIPROTO/` (Windows is case-insensitive; a lowercase `uiproto` also resolves) |
| `ultron_arc.md` | `server_arc.md` (ledger: `tasks.md`) — already noted in §69.0 |

No separate `ultron_arc.md` exists; §69 lives inside `server_arc.md` (lines
7685–7902).

## 2. Repository structure (discovered)

```
Ultron-server/                  Git root, branch feature/phase-1-foundation
├── server_arc.md               Build specification (§69 = unified clients)
├── tasks.md                    Master task ledger (T001–T537)
├── server/                     FastAPI backend (app/, tests/)
│   └── app/static/             Published UIPROTO SPA bundle (index.html + assets/)
├── UIPROTO/                    Frontend prototype — visual source of truth
├── clients/
│   ├── protocol/               node-protocol.schema.json (computer-node WS contract)
│   └── desktop-node/           Contract README only (no executable yet)
├── docs/                       errors.md
├── deployment/ scripts/ workspaces/
└── (no firmware/ yet)
```

- No `uiproto`, `ultron_arc.md`, `firmware/`, or Electron code exists anywhere
  in the tree (searched recursively for `*electron*`: only
  `UIPROTO/node_modules/electron-to-chromium`, a browserslist data file).
- `clients/desktop-node/` is a **contract directory**: the executable reference
  for the Windows node is the Python test double in
  `server/tests/support/node_double.py`, not a Node/Electron binary.

## 3. UIPROTO — actual framework (discovered, not assumed)

| Aspect | Finding |
|---|---|
| Framework | **TanStack Start** (`@tanstack/react-start` 1.168.x) via `@lovable.dev/vite-tanstack-config`; **not** Next.js |
| Mode | **SPA** (`tanstackStart.spa.enabled = true`), `nitro: false` — client-only static build served by FastAPI |
| UI library | React 19.2 + TypeScript 5.8 (strict, `exactOptionalPropertyTypes`) |
| Build | Vite 8.1.5 (`vite build` → `dist/client/`) |
| Package manager | **npm** (lockfile `package-lock.json`; a `bun.lock` also exists from the Lovable scaffold — npm is what has been used) |
| Routing | TanStack Router file-based: `src/routes/__root.tsx`, `src/routes/index.tsx` (single command-center route) |
| Styling | Tailwind CSS 4 + custom design tokens in `src/styles.css` (`hud-panel`, `status-dot`, shell grid…) |
| Component kit | Radix UI primitives + lucide-react icons; Ultron-specific components in `src/components/ultron/` |
| State | TanStack Query (server state) + small React context (`lib/workspace.tsx`, `lib/auth.tsx`); single SSE connection store (`lib/events.ts`) |
| API client | `src/lib/api.ts` — typed REST + fetch-based SSE (Authorization header; `EventSource` cannot send it), silent token refresh |
| Fonts/assets | Local assets under `src/assets/`; logo PNG inlined by Vite |
| Env | `VITE_*` injection via lovable config; backend dev-proxy hardcoded to `http://127.0.0.1:8000` in `vite.config.ts` |
| Tests | Vitest 4 + Testing Library + jsdom — currently **2 files / 6 tests, all passing** |
| Lint/format | ESLint 9 + prettier configs exist but are **broken repo-wide** (established earlier); typecheck (`tsc --noEmit`) is the real gate |

### Commands executed (verbatim results)

```
npm test
  Test Files  2 passed (2)
       Tests  6 passed (6)

npm run build
  ✓ built (client) — dist/client/assets/{styles,index,routes,…}
  ✓ built (server assets)
  [prerender] Prerendered 1 pages: /
  (non-fatal warning: vite-tsconfig-paths plugin redundant on Vite 8)

.\node_modules\.bin\tsc --noEmit
  (exit 0, no output)
```

Pre-existing errors recorded: none blocking; only the plugin deprecation
warning above.

## 4. Backend surface the UI already uses

Root-mounted FastAPI routers (`server/app/api/routes/`):

| Router | Transport |
|---|---|
| `/auth` | REST (login/refresh/change-password; Bearer JWT) |
| `/chat` | REST + SSE stream |
| `/events` | SSE (`GET /events`, `Last-Event-ID` resumption, Authorization header) |
| `/health`, `/ready`, `/metrics` | REST probes |
| `/agents`, `/tasks`, `/tools`, `/schedules`, `/notifications` | REST CRUD |
| `/nodes` | REST + **WebSocket** `/nodes/ws` (computer-node protocol, schema in `clients/protocol/`) |
| `/devices` | REST (device/ESP32 registry, §26) |
| `/voice` | REST + WebSocket |

Same-origin design: the SPA is served at `/` by FastAPI in production; in
`vite dev` all API prefixes are proxied to `127.0.0.1:8000`. **No base-URL
configuration exists or is needed** — a critical constraint for Electron (see
verdict).

## 5. Existing Electron / firmware

- **Electron: none.** No main process, no preload, no packaging config.
- **Firmware: none.** No `firmware/` directory; ESP32 remains spec-only (§26,
  §64.9, T160–T167, T352).

## 6. Verdict — direct integration, no migration

`UIPROTO` needs **no controlled migration**. It is a Vite SPA that emits a
static `dist/client/` bundle already consumed by FastAPI
(`server/app/static/`). Electron can wrap it with zero UI changes by loading:

- **development:** the `vite dev` server URL (same relative API paths, proxy
  intact), or
- **production:** the published `server/app/static/` files via `file://` (or a
  loopback static handler), with the REST/SSE/WS calls retargeted to the
  configurable backend origin.

Smallest viable integration:

```
clients/electron/               (new; keeps UIPROTO untouched)
├── package.json                electron + electron-builder devDeps only
├── src/main.ts                 BrowserWindow → loads UIPROTO (dev URL or static bundle)
├── src/preload.ts              contextBridge, narrow typed IPC allow-list
└── (later) src/bridge/         Windows Node Bridge handlers (T521)
```

The renderer keeps using `src/lib/api.ts` / `events.ts` unchanged; only the
fetch/SSE base origin needs a single injection point (small, documented change
inside `UIPROTO/src/lib/api.ts` or an env var), which is the one controlled
touch to the prototype and must land with a T515-baseline diff note.

## 7. Constraints that shape E1

1. **Lovable branch** (`UIPROTO/AGENTS.md`): never rewrite pushed history; keep
   the branch green.
2. **Same-origin relative paths** in `api.ts`/`events.ts` — Electron must
   preserve them (proxy or origin injection), not fork the client.
3. **Authorization header SSE** — Electron's WS/IPC work must not bypass the
   existing token model.
4. **§69 security baseline** — `contextIsolation` on, Node integration off,
   narrow IPC; OS actions only in main process.
5. **Machine rule (tasks.md header)**: this laptop never runs the full ULTRON
   runtime; Electron verification here is limited to offline window/IPC/asset
   checks against the static bundle — live-backend paths are recorded
   unverified until the author's server.

## 8. Next task

**T515** — prototype preservation baseline: record the baseline commit
(`a4a10f1`), create the integration branch, inventory preserved assets, and
confirm `UIPROTO` builds byte-identical on the baseline before T516 adds
`clients/electron/`.

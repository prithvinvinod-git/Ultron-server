# ULTRON — open items and loose ends

Every known unfinished thing, in the order it should be dealt with. Nothing here
is lost in `tasks.md` or `server_arc.md`; this file is the short list of what is
*not done* and what only the author can do.

Last updated during the T023 gate pass: the §64/§65/§66 blocks are appended to
`server_arc.md` + `tasks.md` (T330–T394, D017–D031, cross-checked, uncommitted).

## Platform, in one line

The **server runs on Ubuntu Server OS** and nowhere else. The **mobile and ESP32
client is a Next.js PWA on Vercel**. A separate **Windows desktop client** is
the primary desktop platform, and it is the only surface that can ever host the
computer-control bridge. Neither client needs WSL, Docker, Python, or a
repository clone — that is §62–§63, and it is why §61 ("never run ULTRON on this
laptop") is about the *server* only, not a contradiction.

---

## A. Needs the author, on this machine (elevated PowerShell)

### A1. Rotate the PostgreSQL passwords — optional for now, do it before the next start

Both passwords are known-weak defaults and the database holds only test data, so
this is **not urgent**. It becomes worth doing before the database ever holds
anything real.

| Role | Current state | Action |
|---|---|---|
| `postgres` (superuser) | the installer's default password | change it |
| `ultron` (app role) | temporary dev password, stored in the git-ignored `.env` | regenerate in `.env` |

The role has no superuser, createdb, or createrole rights, so `ultron` alone
cannot escalate — that is already the safe shape. Rotating is hygiene, not risk
reduction.

> **A caveat worth knowing:** rotating the `postgres` password cannot be done
> non-interactively on Windows. `ALTER ROLE ... PASSWORD` runs fine from
> `psql`, but the superuser password is not stored anywhere in the usual place
> on a Windows install, so it has to go through the installer or
> `pgAdmin`. Budget a few minutes with the GUI rather than scripting it.

### A2. Confirm the loopback bind actually took effect

`postgresql.conf` now says `listen_addresses = 'localhost'` and the service was
restarted, so it should be live — but it was never observed, because verifying it
requires the service running and starting it needs elevation. Run once:

```powershell
Start-Service postgresql-x64-17; Start-Sleep 3
Get-NetTCPConnection -LocalPort 5432 -State Listen | Select-Object LocalAddress,LocalPort -Unique
Stop-Service postgresql-x64-17 -Force
```

Expect **only** `127.0.0.1` and `::1`. If `0.0.0.0` or `::` appear, say so and
it gets chased down.

Note that even before this, `pg_hba.conf` only permitted `127.0.0.1/32` and
`::1/128`, so remote *authentication* was never possible. This is about not
advertising the port on the LAN at all.

**Done already:** the service is `Stopped` / `Manual`, so it does not start at
boot and holds no memory while idle.

---

## B. Needs the author, on the server (cannot be done here at all)

Spec §61: this laptop is build-and-test only. ULTRON is never run here.

### B1. Anything requiring a listening socket or a live provider

Deliberately deferred, not skipped. Each is a real gap in local verification that
only the server can close:

| Item | Why it needs the server |
|---|---|
| `/health` returning healthy for a **real** PostgreSQL | testable here with doubles, which proves the 200/503 logic but not that it reports `true` against a live store |
| `/ready` degraded path for a **real** unreachable Redis/Ollama | no Redis or Ollama is installed, by policy |
| Uvicorn / ASGI server startup, signal handling, graceful shutdown | needs a bound port |
| `/events` SSE stream (T023) end to end — code exists (`277aac0`), never served live | needs a bound port |
| T027 "server starts", event stream works | same |
| Agent runtime, scheduler, browser tool, device bridge | each starts real workers or drivers |
| Rate limiting under real concurrency | needs a socket and real timing |

### B2. Server-side `.env` differs from this machine's

This machine's `.env` is deliberately hobbled. On the server, **enable**:

```ini
BROWSER_ENABLED=true          # or as needed
SCHEDULER_ENABLED=true
COMPUTER_NODES_ENABLED=true
DEVICES_ENABLED=true
METRICS_ENABLED=true
RESOURCE_MONITOR_ENABLED=true
TASK_RECOVER_ON_STARTUP=true
API_HOST=0.0.0.0              # behind a reverse proxy, with ALLOWED_HOSTS set
ENVIRONMENT=production        # then fix every warning startup_warnings reports
```

And **regenerate** `JWT_SECRET`, `ADMIN_PASSWORD`, `ADMIN_USERNAME`, and
`DATABASE_URL` for the server. The values in this machine's `.env` are dev-only
and were generated for tests.

Also raise `ARGON2_*` back to the OWASP baseline (this machine uses
`time_cost=2 / memory_cost=16384` so the 912-test suite stays fast — do **not**
copy that to the server).

---

## C. Code work still open

### C1. T022 — done: `/health`, `/ready`, `/metrics`

Committed. The engine (`app/observability/health.py`, T018) is complete and
tested; T022 added the HTTP surface.

The interesting part was not the routes. It was the `startup_warnings` gap:
`settings.py` promises misconfiguration is *"observable through `/health`"*, and
`/health` reported nothing. A production deploy with `ALLOW_ANONYMOUS=true` was
logging one line at boot and then reporting healthy forever. Both probes now
carry the warnings, and `/health` drops `status` to `degraded` — deliberately
**not** a non-200, because a restart cannot fix configuration and a crash loop
is the opposite of diagnosable.

Still open, and only on the server: `/ready` against a **real** PostgreSQL.
That is item B1 — see the top of this file.

### C2. Logout docstring disagrees with behaviour

`auth.logout`'s docstring claims a malformed `Authorization` header is ignored.
Only a **missing** header is; a non-empty malformed value still raises from
`tokens.extract_bearer`. Either fix the doc or handle it in the route — the doc
and the behaviour must agree.

### C3. Branch has never been merged

Everything sits on `feature/phase-1-foundation`, pushed but not merged. Repo
convention (spec §52) is slice → `feature/*` → `develop`, with `main` reserved
for releasable states. Worth doing before the branch gets long. Currently
uncommitted on top: the §64–§66 documentation and the T023 gate fixes (C12).

### C4. `.env.example` still needs a review pass

It was written before the online-first amendment. Beyond the `ENABLE_PGVECTOR`
fix already applied:

- `MODEL_DEFAULT=ollama:qwen2.5:7b-instruct` contradicts the online-first policy
  (§59.7) and should be empty or an online provider.
- The Ollama and voice blocks are written as though a local model is assumed.
- `STT_MODEL=base` / `TTS_MODEL_PATH` imply local engines.

### C5. Redis — deferred, but unconfirmed

Not installed. Nothing in Phase 1 needs it (event bridge off, no queues), and
the unit suite uses fakes. It should stay that way until a task genuinely
requires it. **Do not install it speculatively.**

### C6. Client architecture: decided — Next.js PWA on Vercel

Recorded as §63 and T315–T322. Mobile and the ESP32 control panel are **one
Next.js PWA**, deployed to Vercel from GitHub. A native React app is deferred,
not rejected — only if the PWA genuinely cannot do something.

Four things follow from Vercel hosting, and each one has server work behind it:

1. **The Ubuntu server must be HTTPS.** Blocking for all mobile clients — a
   browser refuses to let an HTTPS page call an `http://` server, so without TLS
   the PWA cannot talk to the server at all. Needs a domain + cert + reverse
   proxy (**T318**). Do this before any client work, or the client work is wasted.
2. **CORS on the API** for the Vercel origin, tightly scoped (**T319**).
3. **Do not route the live event stream through Vercel.** Vercel's WebSocket
   support is public beta, closes at the function's duration limit (300s on
   Hobby), and pins to one instance with no shared memory — bad for a 33-event
   stream, and it would drag Redis back in just to fan out. The PWA talks
   straight to the Ubuntu server instead (§63.5).
4. **Client reconnect + resync** is mandatory, not optional polish (**T320**).

### C7. What mobile cannot do — revoked, not faked

The PWA cannot do Playwright, app launch, active-window detection, keyboard or
mouse control, arbitrary screenshots, filesystem traversal, shell execution, or
Web Serial/USB (§63.3). These are **reported unavailable with a reason**, never
hidden. The Windows computer-control bridge stays **desktop-only and last**.

This means the Windows desktop client is not redundant with mobile — they are
genuinely different surfaces. The desktop client is the only place the
computer-control bridge can ever live.

### C8. ESP32 control panel: control only, no flashing

The PWA has a dedicated ESP32 mode that reads state, sends commands, and shows
telemetry — but **does not flash firmware**; Web Serial is desktop-only anyway.
The device dials *out* to the server over the existing JSON-over-WebSocket
transport (T162), so no inbound ports and no LAN discovery are needed.

**Wi-Fi provisioning is a separate local flow**, deliberately outside the PWA: an
unprovisioned device cannot be reached from Vercel, and an HTTPS page cannot
join a device's SoftAP. Provision once over the device AP or USB (**T321**).

---

## D. Documentation debt

### D1. Integration tests have never been run against a live database

T026's migration was verified by hand against PostgreSQL 17 (17 tables,
`jsonb` embedding, correct enum columns). The suite itself still uses doubles
everywhere. A single integration test that boots the app against a real
PostgreSQL would close that, and is the main thing that would justify trusting
the green suite.

### D2. Startup warnings are not covered by a test that proves the promise

Once `/health` surfaces them, the test should assert a
`ALLOW_ANONYMOUS=true` + `ENVIRONMENT=production` configuration is *visible* in
the response. Otherwise the docstring's claim regresses silently again.

---

## E. Housekeeping

- `C:\Program Files\PostgreSQL\17\data\postgresql.conf.bak-listen` — the
  pre-change backup of the `listen_addresses` line. Harmless; delete once the
  bind is confirmed (A2).
- `C:\Users\prith\AppData\Local\Temp\opencode\` holds `harden-postgres.ps1` and
  several one-shot `fix-59.py` / `annotate-tasks.py` scripts. All disposable;
  nothing in the repo depends on them.
- `tempfile.gettempdir()/ultron-tests-isolated.env` is written by `tests/conftest.py`
  at import time so collection-time settings reads are isolated. It is empty and
  re-created each run, but it does not self-delete.

---

### C9. Add a settings-override test helper — a real footgun found in T022

`Settings.security` and `Settings.observability` are **computed properties**
assembled from flat fields. So `settings.model_copy(update={"security": ...})` is
**silently discarded** — no error, no warning, the test just quietly asserts
nothing and passes.

This already cost time in T022. It will bite again, because every future test
that needs a non-default setting is exposed to it. Add one helper in
`tests/conftest.py` that overrides flat fields and asserts the override actually
took effect, so a bad override fails loudly instead of passing vacuously.

### C10. `/metrics` is unauthenticated — restrict it at the network layer

All three probe routes take no token on purpose: an orchestrator cannot present
one, and a probe that needs credentials is a probe that gets disabled. But that
makes them **publicly readable**, and `/metrics` describes the deployment —
dependency names, latency, version, warning count.

`METRICS_ENABLED` now defaults to false — **but it did not until T024.** It was
`true` in `Settings`, in `ObservabilitySettings`, and in `.env.example`, while
this note and the T022 notes both claimed otherwise. The route tests passed the
flag in explicitly, so the suite was green against an endpoint that shipped open.
The code has been aligned with the documented intent and
`test_metrics_default_to_disabled` now asserts the default itself.

Enabling it means restricting it at the network layer: reachable only from the
monitoring network or behind the reverse proxy (T318/T319). Do not expose
`/metrics` on the public interface. Note this when the reverse proxy is
configured, or it will be forgotten.

### C11. The Vercel client needs a server URL — and no secrets in its build

The PWA has to be told where the ULTRON server is, and that URL must come from
a **public** build-time variable (Vercel env var, `NEXT_PUBLIC_*`). Keep it
strictly separate from the server's secrets: the client authenticates with the
user's own opaque session token and holds no privileged credential (§63.5).

Related trap: anything prefixed `NEXT_PUBLIC_` is **inlined into the JavaScript
bundle** and is readable by anyone who opens devtools. The server URL is fine.
A JWT secret, admin password, or a device token is not.

### C12. T023 — gate GREEN, ready to mark `[x]`

Closed on this pass. The receive-only SSE endpoint (`server/app/api/routes/events.py`,
committed `277aac0`) shipped without ever running a gate. Now:

- `scripts/lint.ps1` clean over 108 files (ruff check, ruff format, mypy)
- `scripts/test.ps1` = **1021 passed in ~39s** (smoke suite 14)
- fixes along the way: ruff reformat + `asyncio.TimeoutError` → `TimeoutError`;
  mypy duplicate-`conftest` clash via `explicit_package_bases`; 26 typing errors
  across 5 test files; `ENABLE_PGVECTOR` conftest isolation (import-time env
  scrub); TestClient SSE hang (see C13).

Remaining: mark T023 `[x]` in tasks.md with this evidence and add its delivery
notes.

### C13. Errors and todos found during the T023 gate

1. **Starlette deprecation warning on every run**: starlette 1.7 deprecates
   `httpx` inside `TestClient` ("install `httpx2` instead"). Cosmetic for now;
   decide when `httpx` is next touched — not a test failure.
2. **`TestClient` cannot stream.** starlette 1.7's transport runs the app to
   completion and buffers the whole response, so any *successful* endless
   stream (`client.stream`, context manager or not) hangs forever; only finite
   responses (401/422/503) survive the round trip. The 11 open-stream tests in
   `test_event_stream_routes.py` now call `stream_events` directly through a
   typed `open_stream()` helper. Anyone adding an SSE test must use that path,
   not `client.get("/events")`, except for refusals.
3. **T026 checkbox mismatch**: the task text says `DONE` on the dev machine
   (Alembic applied, 17 tables) but the box is still `[ ]` — either mark it or
   record why the box stays open ("re-run on the server").
4. **G3 test count is stale** ("942 tests", connect check "left open"): the
   suite is now 1021 and the connect check landed with T023 (smoke:
   `/events` in the mounted-surface assertion + anonymous 401).
5. **Uncommitted work has grown** (feeds C3): gate fixes, the two test rewrites,
   the new smoke test, `server_arc.md`, `tasks.md` and this file.
6. **Next tasks**: T025 stays `DEFERRED` (no Docker on this machine), T026 as
   in (3), T027 phase gate blocked on server-side halves — nothing runnable
   here blocks on them.

---
## F. Deferred by policy — preserved, not deleted

Recorded so they are not mistaken for oversights. Full rationale in
`server_arc.md` §59, §60, §61 and the annotated task entries.

| Item | Status |
|---|---|
| Self-hosted LiveKit | `REVISED` — supported, cloud is the default (T254) |
| Local faster-whisper / Piper | `DEFERRED` — online providers first (T150, T151) |
| Fully offline voice | `FUTURE` (T255) |
| pgvector / vector retrieval | `SKIP - PHASE 1` (T124) |
| Local Ollama runtime | `FUTURE` — adapter kept as offline fallback (T061) |
| Docker anywhere | `DEFERRED` — systemd is the deployment path (T194) |
| Kubernetes, heavy observability | `FUTURE` |
| Desktop client / Orb | `FUTURE`, after the API (T240+). **Windows is the primary desktop platform** (§62.3) |
| Mobile + ESP32 control panel | **DECIDED** — one Next.js PWA on Vercel (§63, T315–T322) |
| Native React app | `DEFERRED` — only if the PWA cannot deliver something (§63.2) |
| Computer-control bridge | `DESKTOP ONLY`, and still **last** (§63.3, §59.15) |
| Windows bridge | `FUTURE`, **last** — highest blast radius (§59.15) |
| Firebase Auth as upstream IdP | `FUTURE`, gated on pricing (T300–T305) |
| Firestore copy of the database | **`SKIP`** — rejected, see §60.3 |
| Client-side offline cache + outbox | `FUTURE` (T306–T309) |
| OpenClaw as a runtime dependency | **`REJECTED`** — architectural reference only (D025) |
| Inbound phone calls | `FUTURE` (T377) — `incoming_call` webhook recorded now, deny-by-default |

## G. Decisions from the author — G1, G2, G3 all answered

### G1. Receive-only — **ANSWERED: receive-only → SSE**

The author chose **receive-only**. T023 is therefore an SSE endpoint, not
`/ws`. §27's orb and agent-window events are all server→client, which makes this
workable. Consequence to accept: with no client→server frames on this channel,
"acknowledge an event", "cancel work", "stream audio" and "live voice" have
**nowhere to go** on it. They need an explicit HTTP call, or they wait for a
bidirectional stream later. Do not silently assume they are covered.

The device transport (T162) is already WebSocket and is a separate channel
between ESP32 and server — unaffected.

### G2. Stream authentication — **RESOLVED IN CODE: `fetch()` + `Authorization` header**

First-message auth was not implementable alongside G1: a receive-only stream
has no first message, and `EventSource` cannot send an `Authorization` header
*or* a body at all. Implemented T023 follows the recommended option —
`fetch()`-based streaming with a `Bearer` header and hand-written backoff
(`server/app/api/routes/events.py`) — so the token stays out of URLs and out of
proxy logs, which was why query-string auth was rejected.

Revisit only if native `EventSource` is wanted: that path needs a same-origin
HttpOnly cookie (a BFF/proxy on the server, not a bare cross-origin API) and is
CSRF-exposed without care. `?token=` stays rejected — §21 makes these
long-lived opaque credentials.

### G3. **ANSWERED: T024 first**

Done — the Phase 1 suite closed at 942 tests, with the event-stream connect
check deliberately left open for T023 rather than faked. That check has since
landed with T023 (see C12/C13); the suite is now 1021 tests.

---

## H. Untested surfaces — remind the author whenever work resumes

Recorded at the start of the proceed-to-remaining-tasks pass, per the author's
instruction to flag these at every proceed point. None can be closed on this
machine (spec 61 forbids running ULTRON here).

1. **Server never started here** — no live end-to-end run of any endpoint.
2. **Integration tests never ran** against live PostgreSQL/Redis —
   `-m integration` is always deselected; no Docker on this machine (T025
   deferred), so only native-PostgreSQL manual checks exist (T021, T026).
3. **Redis, Ollama, agent runtime** — health checks exist and are unit-tested,
   but the services themselves are unconfirmed (Redis also C5).
4. **SSE `/events` never served on a real socket** — route tests + direct
   drive only; `TestClient` limitation documented in C13.

Closing any of these needs the server (B1) or Docker (T025).

---

## I. Stage-2 deployment planning — ideas recorded from the author (2026-10-07)

From the author's two-stage usage plan. Stage 1: Windows persona machine —
development today, and a permanent **Windows node** (§64.6) running local
tools (windows-use, playwright, PowerShell) under one Core. Stage 2: an
always-on **Ubuntu server** hosting Core + services (§64.7) behind a real
domain, reached from the phone PWA (§63). The topology already matches the
spec; these are the open questions to resolve before the deployment phase
(§37 / PHASE 13 / T025).

1. **Windows-node reachability** — a phone → Core → Windows tool request
   executes on the Windows node, which must be **on and reachable** while the
   author is away (§64.6.1's CORRECT path; §64.14 defines the offline degrade).
   Candidates: WireGuard/tailscale or a tunnel. Decide when the node gateway
   lands (T141, Phase 8) — stage 1 needs none of this.

2. **Ollama host decision** — where local inference lives in stage 2: on the
   Ubuntu server (always available, low voice latency; GPU?) versus staying on
   the Windows box (compute headroom, but a SPOF — offline when the author is
   away). Nothing decided in the repo. Verify VRAM/model-size requirements
   before choosing; cloud fallback already exists (§3, §59.7).

3. **TLS / reverse proxy for stage 2** — §63.5 makes HTTPS + a real domain
   mandatory (a Vercel-served HTTPS PWA will not call `http://`), plus a CORS
   allowlist for the Vercel origin, and the live event stream must reach ULTRON
   **directly** (never proxied through Vercel). Plan Caddy/nginx in front of the
   server; Cloudflare Tunnel if there is no public IP. §37 + Phase 13 only say
   "document it" — this is the content they need.

4. **Ops on the Ubuntu box** — PostgreSQL backups, and *enforcing*
   `event_retention_days` (officially Phase 3+ per the events model docstring;
   currently unenforced). Health/readiness/metrics are already built (T022).

5. **Voice from the phone** — voice is planned on the Windows persona; phone
   voice would use browser/cloud STT-TTS plus a TURN/cloud path. **Self-hosted
   LiveKit is already rejected** (`server_arc.md:4061`) — do not re-propose it;
   solve with a configured cloud provider when the time comes.

---

## J. Session progress report (2026-10-10)

**State at stop: 60 T-tasks `[x]` of 361 defined (301 `[ ]` remaining; T025
`DEFERRED` no Docker). Phase 2 (T030-T053) is complete and verified locally;
Phase 3 (model system) is under way — T060 (provider ABC + vocabulary), the four
adapters T061-T064 (Ollama, OpenAI, Gemini, Anthropic), the scripted fake
provider T065, the model router T066, and **model usage recording T067 done.**
Branch `feature/phase-1-foundation`, HEAD `8ed6a4b` "T033/34 not finished"
pushed to origin; **T033-T053 + T060-T067 are written, tested and marked but NOT yet
committed**, and the §67/§68 documentation addendum (appended to `server_arc.md`
and `tasks.md`, tasks T395-T491 + decisions D032-D039) is likewise uncommitted.
Working tree has uncommitted changes.

### Next up: Phase 3 — model system (T068 onward)

Phase 3 (`server_arc.md` §20-§21, tasks.md:539-551 "Phase 3 — Model system") is
under way: **T060-T067** are done (see tasks.md). The next task is **T068**
wire the model-driven tool-calling loop into the agent base, then Phase 3 tests
(T069) and the Phase 3 verification (T070).

### Done this session (continuation from 2026-10-08)

- **T067 `[x]`** — Model usage recording: the router now records every model call
  to `model_usage` when `MODEL_USAGE_TRACKING` is enabled. Captures model, provider,
  status, tokens, cost, latency, request_id, and error detail. Streaming aggregates
  the terminal chunk's usage. Container wires providers + session_factory + bus.
  Evidence: `app/models/router.py` + `app/container.py`. Gates: lint clean over
  **173 files**, mypy clean, 28 router tests pass.
- **T066 `[x]`** — `app/models/router.py`: the single model selector with
  capability→model resolution, fallback chain, `speed`/`prefer_local`/`preferred`
  reordering, availability/resource filters, per-capability defaults, bounded
  retry with injected backoff, health tracking & circuit breaking (§66.6),
  non-retryable error surfacing, and `MODEL_SELECTED`/`MODEL_FAILED` events.
  Evidence: `tests/unit/test_router.py` (**28 tests** covering selection matrix,
  fallback+retry, circuit open/close, health caching, events). Gates: lint clean
  over **173 files**, **1822 passed** (1793 + 28 + 1 lint-check).
- **T065 `[x]`** — `app/models/fake.py`: the deterministic, scripted
  `ModelProvider` for tests (FIFO `queue`/`queue_stream`/`queue_error`, request
  recording, constructor-scriptable config/availability/models/health, default
  `chat`/`stream`). Implements the same T060 contract as the real adapters.
  Evidence: `tests/unit/test_fake.py` (14). Gates: lint clean over **172 files**,
  **1793 passed**.
- **T064 `[x]`** — `app/models/anthropic.py`: the optional Anthropic adapter
  (Messages API, `x-api-key` + `anthropic-version`). Lazy key check
  (unconfigured ⇒ unavailable, not broken); maps the T060 vocabulary to/from
  top-level `system`, `content` blocks (`text`/`tool_use`/`tool_result`) and
  `input_schema` tools; defaults `max_tokens`; normalises `usage` and
  `stop_reason`; typed-SSE streaming with tool-use assembly; `/v1/models`
  discovery + health (never raises); the full §33 error map. Owned client closed,
  injected client left open. Evidence: `tests/unit/test_anthropic.py` (30,
  respx). Gates: lint clean over **170 files**, **1780 passed**.
- **T063 `[x]`** — `app/models/gemini.py`: the optional Gemini adapter
  (Generative Language REST, `x-goog-api-key`). Lazy key check (unconfigured ⇒
  unavailable, not broken); maps T060 vocabulary to/from `contents`/`parts`,
  `systemInstruction`, `functionDeclarations`, `functionCall`/`functionResponse`;
  normalises `usageMetadata` and `finishReason`; SSE streaming; `/v1beta/models`
  discovery + health (never raises); the full §33 error map. Owned client closed,
  injected client left open. Evidence: `tests/unit/test_gemini.py` (29, respx).
  Gates: lint clean over **168 files**, **1750 passed**.
- **T062 `[x]`** — `app/models/openai.py`: the optional OpenAI/OpenAI-compatible
  adapter (Chat Completions). Configurable `OPENAI_BASE_URL` (§59.7 OpenRouter),
  lazy key check (unconfigured ⇒ unavailable, not broken), chat, SSE streaming
  with index-keyed tool-fragment assembly, `/models` discovery, health (never
  raises), and the full §33 error map. Owned client closed, injected client left
  open. Evidence: `tests/unit/test_openai.py` (29, respx). Gates: lint clean over
  **166 files**, **1721 passed**.
- **T061 `[x]`** — `app/models/ollama.py`: the local provider over the Ollama
  HTTP API (httpx, not the SDK). `/api/chat` chat + NDJSON streaming, `/api/tags`
  discovery (honours the `OLLAMA_MODELS` allowlist), `is_available` (exact tag or
  base-name), `health` (connect-budget probe that never raises), configurable
  host + `auth_headers()`, and full §33 error mapping (unreachable→
  `LOCAL_MODEL_UNAVAILABLE` 503 retryable, timeout→504, 404→400, 429, 401/403→
  `ProviderNotConfiguredError`, 5xx→`ModelError`, bad JSON→`ModelResponseInvalidError`).
  Owned client closed, injected client left open. Evidence:
  `tests/unit/test_ollama.py` (26, respx). Gates: lint clean over **164 files**,
  **1692 passed**.
- **T060 `[x]`** — `app/models/base.py`: the model-layer contract (Phase 3).
  Enums `ModelCapability` (values == `ModelRouterSettings` keys, drift-pinned),
  `FinishReason`, `ProviderStatus`; frozen value objects `TokenUsage`,
  `ToolDefinition`, `ToolCall`, `ModelMessage` (reuses `MessageRole`),
  `CompletionRequest` (model pre-selected by the router), `ModelResponse`,
  `ModelStreamChunk` (`is_final`), `ProviderHealth` (`is_usable`). `ModelProvider`
  ABC: non-empty `name` + honest ClassVar defaults (`local`/`supports_tools`/
  `supports_streaming`), one required `async chat()`, and graceful defaults —
  `stream` degrades to one terminal chunk from `chat`, `health` reports
  configuration not liveness (§17), `list_models`→`[]`, `is_available` follows
  config, `aclose` no-op. Import-time guards: blank `name` or sync `chat` fails;
  abstract intermediates exempt. Evidence: `tests/unit/test_model_base.py` (17).
  Gates: lint clean over **162 files**, **1666 passed**.
- **T034 `[x]`** — `app/tools/base.py`: `BaseTool` ABC per §14/§59.6/§64.11/
  §66.14 — declarative class fields (`name`, `description`, `input_schema`,
  `permission_level`, `timeout`, `logging`, `audit`, `node_scope`,
  `output_schema`, `node_requirements`, `risk_level`, `availability`,
  `version`, `reversibility`), abstract `execute()`, `verify()` default returns
  `VerificationOutcome.UNVERIFIED` with an honest reason (§17, no magic
  success). **Zero permission logic in the ABC.** `ToolResult` typed carrier.
- **T035 `[x]`** — `app/tools/registry.py`: registry consumes the ABC's
  declarative fields, `validate`/`register`/`resolve_available_for`/
  `resolve_permitted_for` against `PermissionManager` (T033); unknown/duplicate
  tool enforced; reversible flags surface for §66.17.
- **T036 `[x]`** — `app/tools/executor.py`: §16 pipeline order (schema →
  permission → policy → target selection → execute → result → verify →
  event); `AWAITING_CONFIRM` for LEVEL 4-5/irreversible; typed `ToolError`.
- **T037 `[x]`** — `app/tools/mock/` providers: `echo`, `fail`, `sleep`,
  `write_state` implementing the ABC with declared schemas/levels.
- **T038 `[x]`** — `app/tasks/state.py`: task state machine (strata per §66.4)
  with `TaskState` StrEnum + guarded transitions.
- **T039 `[x]`** — `app/tasks/manager.py`: `TaskManager` over the container
  (`container.get_task_manager(session)`); handler callers must route lifecycle
  through the manager and publish `TASK_*` events — never the repository
  helpers directly (T041 rule).
- **T040 `[x]`** — `app/tasks/graph.py`: `StepGraph`/`StepNode` —
  construction-order validation (duplicate/missing-dep/cycle with `details`
  naming the loop), `topological_order()` (Kahn), `waves()`,
  `runnable/blocked/waiting_on` with status-key validation, `dependents_of`/
  `descendants`, `from_task_steps(rows)`, `__contains__` (UUID + str).
  T040 `[x]` evidence at tasks.md line 520.
- **T041 `[x]`** — `app/tasks/executor.py`: `TaskExecutor(session, *, events,
  run_step)` composes T038/T039/T040 on one session and routes **every** task
  write through `TaskManager.transition` (no direct repository lifecycle
  helpers). Load-boundary `StepStatus(...)` coercion (enum columns reload as
  plain strings; `StepGraph` compares with `is`); sequential `StepGraph.
  runnable()` loop (single `AsyncSession` is not concurrency-safe; parallel is
  T049/T050); fail-fast with the failing step named in `TASK_FAILED`;
  `TASK_CREATED/STARTED/COMPLETED/FAILED` persist=True + `TASK_PROGRESS`
  persist=False; `recover()` resets in-flight steps via new
  `TaskStepRepository.mark_pending` (keeps `attempt`) and requeues RUNNING→
  QUEUED; `trigger_due()` emits the one-shot `SCHEDULE_TRIGGERED`.
  `Container.get_task_executor(session, *, run_step)` wired to `self.events`.
  T041 `[x]` evidence at tasks.md line 521.
- **Gate green after T041**: ruff format/check + mypy clean over **134 source
  files**, **1338 passed** (1322 → +16 from `test_task_executor.py`).
- **T042 `[x]`** — `app/agents/base.py`: `Agent` ABC (12 §6 attributes +
  lifecycle). `LEGAL_AGENT_TRANSITIONS` + `ensure_legal_agent_transition`
  (409 naming current/target/legal_targets/agent) mirroring T038's pure
  machine; `ACTIVE_STATUSES`/`AGENT_TERMINAL_STATUSES` exactly the row
  model's is_active/is_terminal partition; WAITING doubles as §7 pause with
  resume only through RUNNING; terminal states absolute (one-shot worker,
  retry = T044 spawn); ClassVar `agent_type`/`description` required with
  definition-time guards incl. sync-`run` refusal; instance attrs for the
  other ten; abstract `async run(context)`. Event publication left to T044
  (like TASK_* is the executor's). T042 `[x]` evidence at tasks.md line 522.
- **Gate green after T042**: ruff format/check + mypy clean over **136 source
  files**, **1362 passed** (1338 → +24 from `test_agent_base.py`).
- **T043 `[x]`** — `app/agents/registry.py`: agent-type registry holding
  *classes* (agents carry lifecycle state, unlike stateless tools/instances),
  `create(agent_type, **kwargs)` as T044's spawn seam (fresh CREATED
  instance, §6 assignment kwargs), duplicate type → ConflictError 409, unknown
  type → new `AgentTypeNotFoundError` 404 (code `AGENT_NOT_FOUND`,
  distinct from `AgentNotFoundError`), abstract/non-Agent refused, sorted
  `list()`. No hard-coded docs/Windows types (wiring is T045+'s).
  T043 `[x]` evidence at tasks.md line 523.
- **Gate green after T043**: ruff format/check + mypy clean over **138 source
  files**, **1375 passed** (1362 → +13 from `test_agent_registry.py`).
- **T044 `[x]`** — `app/agents/manager.py`: `AgentManager(session, *, agents,
  events=None, max_concurrent=None)` — the agent analog of `TaskManager`
  (flush-only, caller owns commit). **Create** copies the type's declared
  `description` (§6/§40), validates name/ids, 404s a missing parent/task, copies
  `tools`/`permissions`, starts CREATED, publishes `AGENT_CREATED` persist=True.
  **One status door**: `transition` + the §7 doors `pause`/`resume`/`cancel`/
  `complete`/`fail` all route through T042's `ensure_legal_agent_transition`
  (bare string → 422; illegal move → 409 naming current/target/legal_targets).
  **Resume is the RUNNING target**, legal from WAITING *and* READY (the machine's
  only authority; the door adds no rule) — docstrings corrected, test-pinned.
  **Events**: `_AGENT_EVENTS` maps RUNNING→STARTED, WAITING→PAUSED,
  COMPLETED→COMPLETED, FAILED→FAILED, CANCELLED/TIMEOUT→STOPPED persist=True;
  CREATED/INITIALIZING/READY publish nothing (no §19 name). `cancel(reason)`/
  `fail(error)` merge into the payload only (blank failure 422). **Destroy**
  refuses live agents (409, cancel first), emits nothing. **Assignment**:
  `assign_task` 404s a missing task and 409s a repoint, `assign_model`/`tools`/
  `permissions` are validated replacements (names, not wiring). **Concurrency**
  `max_concurrent` (None = no ceiling) enforced at the moment an agent would
  *become* active; intra-active moves skip it; 409 names active/limit/target;
  `count_active()` sums the repository counts over `ACTIVE_STATUSES`. Tests
  `tests/unit/test_agent_manager.py` (36): create, the door + per-status events,
  the §7 doors (incl. resume's READY edge and refusals), destroy, assignment,
  and the ceiling. T044 `[x]` evidence at tasks.md line 524.
- **Gate green after T044**: ruff format/check + mypy clean over **140 source
  files**, **1411 passed** (1375 → +36 from `test_agent_manager.py`).
- **T045 `[x]`** — `app/agents/mock/`: three deterministic probes, one module
  each, mirroring `app/tools/mock/` (T037) — `mock.agent` (returns a fresh
  `dict(context)`), `mock.long_running` (bounded `asyncio.sleep`, default 0.05,
  `MAX_SECONDS`=5; `AgentError` for a non-number/unbounded value; cancellable
  mid-flight), `mock.failing` (raises `AgentError` 500/`AGENT_FAILED` with the
  caller's message or a default). Concrete `Agent` subclasses whose
  `agent_type`/`description` face T042's definition-time guards; spawned
  through the **real** `AgentRegistry` (no doubles), never registered by the
  app. Tests `tests/unit/test_mock_agents.py` (12): register/spawn, echo
  round-trip + fresh dict, long-running delay/default/refusals/mid-flight
  cancel, failing typed error/message/id. T045 `[x]` evidence at tasks.md
  line 525.
- **Gate green after T045**: ruff format/check + mypy clean over **145 source
  files**, **1423 passed** (1411 → +12 from `test_mock_agents.py`).
- **T046 `[x]`** — `app/core/context.py`: the one §66.3 context layer, first
  small step — a **pure, in-memory** conversation-state store scoped by §22
  namespaces (no DB/Redis; §22's Redis is permission and C5 keeps it out).
  **`Namespace`** frozen value object: `parse` is the only string→namespace door
  (422 `InvalidInputError`, never a silent bucket), bare `user` or `kind:name`,
  `project()`/`agent()` helpers, `USER_NAMESPACE`; shape enforced on
  construction (lowercase kind, no `:`/whitespace in name). **`ConversationContext`**
  — `{namespace: {key: value}}` with set/get/has/delete/snapshot/namespaces/
  clear, keyed by validated namespace, **bounded** (`max_entries` default 256:
  new key at cap → 409 `ConflictError` naming the limit; overwrite at cap
  allowed; non-positive/`bool` cap → `ValueError`); `snapshot` returns a copy
  ordered by namespace string. **`ContextManager`** — `for_conversation`
  (idempotent, adopts unowned, 422 malformed id), `get`/`require` (404
  `NotFoundError` naming `conversation`), `drop`/`clear`/`conversation_ids`, and
  the **tenancy check**: a context carries its `user_id`, a read as a different
  principal is "not found". No subsystem import (only `app.core.errors`). Tests
  `tests/unit/test_context.py` (61). T046 `[x]` evidence at tasks.md line 526.
- **Gate green after T046**: ruff format/check + mypy clean over **147 source
  files**, **1484 passed** (1423 → +61 from `test_context.py`).
- **T047 `[x]`** — `app/core/router.py`: the INTENT stage as a deterministic,
  **declared-rules** router (no hard-coded kinds — §40 applied to routing, like
  T035/T043). `Intent` StrEnum (`conversation`/`question`/`task`/`command`/
  `tool`/`unknown` — pipeline paths, not agent types); `RoutingRequest(text,
  context)`; `RouteRule(name/intent/handler/matches/priority/description)` where
  `matches=None` = always (declared catch-all); `Routing` (intent/handler/rule/
  matched/reason). `IntentRouter`: first match by **lowest priority** (stable tie
  = registration order), `register` validates eagerly (422 on bad name/handler/
  intent/priority, `TypeError` on non-callable matcher) and 409s a duplicate
  name; `unregister`/`rules`/`intents`/`__len__`/`__contains__`/`__repr__`;
  `route` propagates a matcher's exception and returns `Intent.UNKNOWN`+
  `handler=None` when nothing matches. `keyword_matcher` = the explicit Phase-2
  placeholder (`(?<!\w)…(?!\w)` boundaries, case-insensitive, refuses empty).
  Pure Core (imports only `app.core.errors`); **not wired into the container**
  (T049 declares the rules). Tests `tests/unit/test_router.py` (36). T047 `[x]`
  evidence at tasks.md line 527.
- **Gate green after T047**: ruff format/check + mypy clean over **149 source
  files**, **1520 passed** (1484 → +36 from `test_router.py`).
- **T048 `[x]`** — `app/core/planner.py`: the PLAN stage as a **pure builder of
  task graphs, not an isolated agent** (§66.4's exact wording — the design
  constraint). `PlanStep(key, name, description="", depends_on=())` frozen value
  object (plan-local key, no agent/tool field — that is T049's §66.4 *next*
  stage); `Plan(goal, steps)` validates on construction (empty goal/no steps/
  non-step/duplicate key/dangling dependency → 422) and delegates cycle
  detection to **T040's `StepGraph`** via a deterministic `uuid5` key→node-id
  bridge, so the codebase has one cycle detector and the plan cannot disagree
  with the executor. `Plan.graph()`/`topological_order()`/`waves()` read from
  that graph (waves = §66.4's parallel-where-safe batches); `keys`/`get`/
  `__contains__`/`__len__`/`__repr__`. **Declared, not invented** (no model in
  Phase 2, like T047): a `Planner` holds `PlanStrategy(name, decompose,
  matches=None, priority=100, description)`; `register` validates eagerly (422
  bad name/priority, `TypeError` non-callable `decompose`/`matches`) and 409s a
  duplicate name; `plan(PlanningRequest(goal, context))` picks the **first match
  by lowest priority** (stable tie = registration order), `matches=None` = the
  declared catch-all, a raising matcher/decomposer propagates, and **no match →
  a single-step plan of the goal**. `PlanningRequest` = goal (non-empty, 422) +
  open `context` (no router/context coupling). Deliberately absent: model
  decomposition (T066); agent/model selection (T049); retry/replan/
  verification/confirmation (T041/T050); persistence (T049 materialises through
  `TaskManager` — the planner is import-only-downward: `app.core.errors` +
  `app.tasks.graph`). Tests `tests/unit/test_planner.py` (51). T048 `[x]`
  evidence at tasks.md line 528.
- **Gate green after T048**: ruff format/check + mypy clean over **151 source
  files**, **1571 passed** (1520 → +51 from `test_planner.py`).
- **T049 `[x]`** — `app/core/orchestrator.py`: the **joint** between the stages
  (§5's diagram, §50's line, §66.4's "not a new runtime") — the one place that
  knows the order they run in and owns their effects (the `AsyncSession` and the
  `EventBus`). `Orchestrator(session, *, router, planner, agents, events,
  agent_type=None, selector=None, max_concurrent=None)` (events required, since
  the executor needs a bus; declarations come from the app — §40 keeps the kind
  on the agent). `OrchestrationRequest(goal, context={}, priority, project_id,
  conversation_id)` frozen (non-empty goal, Mapping context → 422);
  `AgentSelector = Callable[[request, routing, plan], str | None]` (selector
  authoritative incl. its `None` = "no agent" → stop with routing+plan only;
  else the default `agent_type`; blank/non-str → 422; unknown type → registry
  404); `OrchestrationResult` frozen with `executed`/`intent`/`status`/`result`/
  `error`. Pipeline in §66.4 order: route → plan → `PLAN_CREATED` (best-effort,
  the module's only self-written event) → select → materialise → spawn → walk →
  execute → finish. Materialises through the executor's `create` so §19's
  `TASK_CREATED` is announced (the manager has no bus), then `add_step` in
  `Plan.topological_order()` mapping plan-local keys → real row ids once.
  **One agent per task**: manager creates the row, registry spawns the instance
  sharing its id, walked `CREATED → INITIALIZING → READY → RUNNING` through
  `AgentManager.transition`; a `TaskExecutor` whose injected `run_step` gives
  each step's context (nested request `context`, so a caller key cannot shadow
  `task_id`). Finish: COMPLETED → agent `VERIFYING → COMPLETED`; FAILED → agent
  FAILED carrying the task error. Cancellation cancels the agent best-effort
  before re-raising; `max_concurrent` forwarded, no new policy. Deliberately
  absent: model selection (§66.6/T066), retry/replan/verification policy
  (§66.11/T050), parallel steps (one session). Container: `get_orchestrator(
  session, *, router, planner, agents)` wired to `self.events`. Tests
  `tests/unit/test_orchestrator.py` (27). T049 `[x]` evidence at tasks.md line
  529.
- **Gate green after T049**: ruff format/check + mypy clean over **153 source
  files**, **1598 passed** (1571 → +27 from `test_orchestrator.py`).
- **T050 `[x]`** — `app/core/executor.py`: the **retry/replan policy** §66.4 left
  for after T041/T049 (each runs once and fails fast). `ExecutionPolicy`
  (frozen; `max_replans: int = 0`, the §66.11 loop guard — straight retries are
  bounded by the task row's own `max_retries`, which `TaskManager.retry`
  enforces) + `RetryAction{STOP,RETRY,REPLAN}` + `decide(task, *, replans)`
  (retry preferred while the row has budget, then replan, then stop).
  `CoreExecutor(session, *, events, run_step, policy=None, replanner=None)` is
  the loop **around** `TaskExecutor`: run → on FAILED retry (reset failed steps
  to PENDING, then `manager.retry`), replan (callback while FAILED, reset, then
  `manager.transition` FAILED → PENDING), or stop. **Critical repair:** T041
  leaves a failed step FAILED and the graph only starts PENDING steps, so a
  re-entry without the reset would falsely complete the task around its
  failure. Events: a retry re-emits `TASK_STARTED` (no new name, §64.15); a
  replan publishes §66.8 `PLAN_UPDATED` (best-effort) when the callback returns
  a plan; no `TASK_CREATED` (already materialised) and no `TASK_RETRIED`.
  `ExecutionOutcome(task, attempts, retries, replans)` (`attempts == retries +
  replans + 1`). Verification/confirmation deliberately deferred (they act on
  T041's completion door; COMPLETED has no exit, §66.16). Tests
  `tests/unit/test_core_executor.py` (26). T050 `[x]` evidence at tasks.md line
  530.
- **Gate green after T050**: ruff format/check + mypy clean over **155 source
  files**, **1624 passed** (1598 → +26 from `test_core_executor.py`).
- **Documentation addendum (today)** — user brief: append two large sections
  to `server_arc.md` + roadmap blocks to `tasks.md`:
  - **§67 ULTRON Spatial Interface (USI)** 67.0-67.25: interface modes,
    SpatialUIEngine's 12 modules, strict `SpatialScene` schema (Draft
    2020-12, `additionalProperties:false`, no executable content, closed
    type set), **one ComponentSpec protocol with COMPONENT/SPATIAL renderer
    namespaces** (honest audit — this repo has no "Dynamic UI" today),
    renderer tech gate (three.js default), glTF/GLB assets, spatial panels/
    images/graphs/flowcharts/dashboards/timelines, gesture pipeline
    consuming §68, interaction resolver rule table + priority order,
    local-vs-AI split, voice+gesture fusion via context engine (T380),
    `spatial.*` action protocol, bounded/interruptible/reduced-motion
    animation, mandatory 3D→2D→text fallback, security pipeline
    (validation → §15 permission → confirmation → tool → node → verify →
    audit, Electron hardening), performance/privacy/flags, roadmap S1-S9.
  - **§68 ULTRON Perception Layer** 68.0-68.24: perception sources
    (camera/screen/voice/ESP32), camera service, **local Python vision
    service** (localhost WebSocket transport, never a second bus; tech gate
    OpenCV/MediaPipe/ONNX), capability modules (hand tracking, gesture
    recognition, object detection/tracking with session-stable ids, scene +
    spatial relationships with 3D-ready coords, OCR/document, screen
    understanding reusing T143/T262/T345), event model with throttling +
    confidence bands, **perception never acts** (§68.12), privacy + vision
    modes (OFF/GESTURE_ONLY/.../SPATIAL_MODE), fusion through T380,
    VisionProvider ABC + capability discovery, service lifecycle/status,
    node architecture, flags default-off, fallback hierarchy, fixture-only
    testing (no camera hardware ever), roadmap V1-V10.
  - Existing §50/§66.10/§66.21 amended with additive blockquotes (nothing
    deleted); cross-ref typo fixes in §67/§68.
  - **tasks.md**: appended two blocks — spatial S1-S9 **T395-T451** (57
    tasks) + decisions **D032-D035**; perception V1-V10 **T452-T491** (40
    tasks) + decisions **D036-D039**. Every task carries
    objective/deps/impl/test/accept labels. S4 blocks on V2-V4; none of the
    new tasks are `[x]` (documentation only, no faked features). Encoding
    verified: both files BOM-less, 0 U+FFFD, 0 mojibake; IDs contiguous.

### What Phase 2 delivered (context for the next phase)

T051, T052 and T053 are done and marked (see tasks.md). The T051-plan facts
below are kept for context; the ones that still apply are about the gate, the
routes now mounted, and the uncommitted state.

Facts that carried the Core/REST work (now delivered):

- T051 is `app/api/routes/{agents,tasks,tools,events}.py` — the **REST surface
  for the Core** over the seam T022 already laid (`app/api/` app factory, router
  inclusion, dependencies, error handlers). It wraps services that are all built
  and tested: `Orchestrator` (T049) + `CoreExecutor` (T050) for requests,
  `TaskManager` (T039) for task records, `AgentRegistry`/`AgentManager`
  (T043/T044) for agents, the tool registry/router (T032/T030) for tools, and
  the `EventBus` (T031) for events.
- **Thin HTTP only**: parse/validate/serialise and delegate — no business logic,
  no direct repository access, no `session.commit()` in a route. Build/reuse a
  service per route and let the session dependency own the transaction boundary.
- **Reuse the container** `app/container.py` (`get_orchestrator` is already
  wired; add getters for whatever the routes need rather than building services
  inline).
- **Agent-run entry**: run a goal via `Orchestrator.run(OrchestrationRequest(...))`
  (T049) — blank goal → 422; unknown agent type → 404 (the registry's).
- **Errors → status codes** through the existing `UltronError` hierarchy
  (`InvalidInputError` 422, `NotFoundError` 404, `ConflictError` 409, denied from
  T033); the app-level handlers already map them — raise the domain error, do
  not hand-roll responses.
- **Events**: the bus is `async` fan-out (T031); prefer a list/recent endpoint
  over a blocking stream unless a websocket/SSE layer already exists in
  `app/api/` (check before inventing transport).
- **Verification/confirmation stay out of scope** (T050 deferred them, too):
  they act on T041's completion door and are not part of a thin REST wrapper.
- After T051 come **T052** (Phase 2 tests: event bus, permissions, registry,
  executor, task lifecycle, agent lifecycle, concurrency, E2E request→result)
  and **T053** (**PHASE 2 verification**).
- CRITICAL enum gotcha (empirically verified): freshly loaded ORM rows carry
  plain lowercase enum strings, not members — coerce with `TaskStatus(...)` /
  `StepStatus(...)` / `AgentStatus(...)` at the load boundary.
- Gate order: `scripts/lint.ps1` then `scripts/test.ps1` from repo root
  (`lint.ps1` runs with `PYTHONIOENCODING=utf-8`; mypy checks `tests`
  too — every `# type: ignore[...]` must suppress a real error,
  `warn_unused_ignores`; RUF022 sorts `__all__`; line-length 100; W292).
  Tests only via `uv run pytest …` from `server/`.
- Completed-task marking rule: `[x]` only with passing gate; evidence line
  in tasks.md uses em-dashes, `§` refs, `→` arrows; update the sidebar
  `todowrite` after every completion.
- Commit nothing unless the user asks (T033-T050 + doc addendum are all
  uncommitted; user commits manually).

### Reminders

- Gate commands: `powershell -File scripts/lint.ps1` (ruff format+check, mypy)
  and `powershell -File scripts/test.ps1` (pytest, non-integration). Run from
  repo root with `$env:PYTHONIOENCODING='utf-8'`; tests only via `uv run
  pytest …` from `server/`.
- Never run ULTRON on this Windows machine (no uvicorn/startup/clients).
- Encoding: always write files with the edit/write tools, never PowerShell
  `-replace` (it mangled UTF-8 `§`/dashes once already).
- The doc addendum added 97 roadmap tasks (T395-T491) + decisions; none are
  implemented — §67/§68 are documentation-only, and no camera/vision/gesture/
  spatial code exists until those tasks go `[x]`.

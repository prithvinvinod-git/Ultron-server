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

## J. Session progress report (2026-10-08)

**State at stop: 32 of 264 tasks `[x]`.** T025 remains `DEFERRED` (no Docker).
Branch `feature/phase-1-foundation`, last commit `3c243ec` (T030-T032) pushed to
origin; **T033's changes are written and gated but NOT yet committed** — working
tree has uncommitted changes (see below).

### Done this session

- **T030 `[x]`** — `app/events/types.py`: canonical 71-name `EventType` StrEnum
  (spec §19 + §27/§28/§64.9/§64.15/§65.15/§66.8 additions under the no-rename
  rule), `topic_for`/`KNOWN_TOPICS` moved out of `bus.py`, SSE route 422 gate now
  reads the catalog. 36 tests; 1058 total at the time.
- **T031 `[x]`** — `app/events/bus.py` assessed as already delivered under T023;
  50 tests cover wildcard/backpressure/replay/cap. Redis bridge deliberately
  absent (project-wide Redis deferral, `todo.md` C5).
- **T032 `[x]`** — `app/events/handlers.py` (`PersistEvents` durable
  write-through for the `events` table), `EventEnvelope.persist` flag +
  `EventBus.publish(persist=...)`, container wiring behind
  `settings.observability.events_persist`. Found and fixed a real deadlock:
  `Subscription.close()`'s wake-up `None` sentinel was suppressed on a full
  queue, so a draining consumer could wait forever — close now evicts the
  oldest event to make room (counted as a drop). 10 deterministic tests.
  Committed as `3c243ec`.
- **T033 `[x]`** (this stop) — permission engine, split in two:
  - `app/security/permissions.py` — pure policy: `PermissionDecision`
    (ALLOWED/DENIED/CONFIRM_REQUIRED, values identical to `AuditOutcome`),
    frozen `PermissionRequest` (§64.12 dimensions), `scope_of()`,
    `confirmation_required()`, `evaluate()`. Rule table: 0-1 never ask; 2
    unless workspace pre-auth; 3 unless operation pre-auth; 4-5 every
    invocation; irreversible always (§66.17); risk follows the operation, not
    the caller; **denial outranks confirmation**; ungranted scope = denial.
  - `app/core/permissions.py` — `PermissionManager`: `GrantStore` Protocol +
    `default_level` fallback, `check()` (returns decision), `enforce()` (raises
    `PermissionDeniedError` 403 / `ConfirmationRequiredError` 409 with
    node/client/tool/operation/scope in `error.details`), every decision audited
    with §64.13's full tuple; refusals `durable=True` (outlive the rollback of
    the raise), allows transactional. No container wiring (no caller until
    T036); no event emission (pipeline owns `permission.*` events).
  - `tests/unit/test_permissions.py` — 25 tests.
  - **Gate green**: ruff format/check + mypy clean over **115 source files**,
    **1093 tests passed**. Marked `[x]` in `tasks.md` with full evidence.

### In progress when stopped: T034 `app/tools/base.py` (research only, NO code)

Spec read; design not yet written to disk. Facts to carry forward:

- §14 defines the ABC: `name`, `description`, `input_schema`,
  `permission_level`, `execute()`, `verify()`. §59.6 makes `timeout` mandatory
  ("always declared") and adds `logging`/`audit`/typed-errors; §66.14 adds
  `output_schema`, `node_requirements`, `risk_level`, `availability`,
  `version`, `reversibility` (§66.17: `reversible | partially_reversible |
  irreversible`); §64.11 adds `node_scope`. "Tools declare, the pipeline
  decides" (§66.7) — the ABC must contain **zero permission logic**.
- §16 pipeline order: schema → permission → policy → target selection → execute
  → result → verify → event (target-selection stage added by §64.11; tools
  that declare no node run on the cloud server).
- Already available: `PermissionLevel`, `VerificationOutcome`
  (SUCCESS/PARTIAL/FAILED/UNVERIFIED, verbatim §17), `ToolExecutionStatus`
  (incl. `AWAITING_CONFIRM`) in `app/database/models/enums.py`; errors module
  has no `Tool*` error yet (T036 will need one); `app/tools/__init__.py` exists
  (docstring only); scaffold subdirs `browser/ computer/ filesystem/ git/ mock/
  notifications/ python/ system/ terminal/ web/` are empty (`__init__.py` only,
  from T002); `app/verification/` empty too.
- Design leaning (not final): ABC with abstract `execute()`, `verify()` default
  honest-but-not-magic (§17 "never assume success" argues against a silent pass
  — candidate: default returns `VerificationOutcome.UNVERIFIED` with a reason),
  declarative class attributes for §59.6/§66.14 fields, and a small typed
  result carrier for `execute()` so T036's executor has one shape to move
  through the pipeline. Registry (`T035`) consumes these fields as data.
- Tests will go in `tests/unit/test_base.py` or `test_tool_base.py`; follow
  `pytestmark = pytest.mark.unit`.

### Reminders

- Gate commands: `powershell -File scripts/lint.ps1` (ruff format+check, mypy)
  and `powershell -File scripts/test.ps1` (pytest, non-integration). Run from
  repo root; tests only via `uv run pytest …` from `server/`.
- Never run ULTRON on this Windows machine (no uvicorn/startup/clients).
- **Commit T033 before starting T034** — it is gated, marked, and otherwise
  complete; only the commit is missing.
- Encoding: always write files with the edit/write tools, never PowerShell
  `-replace` (it mangled UTF-8 `§`/dashes once already).

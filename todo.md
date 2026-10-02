# ULTRON — open items and loose ends

Every known unfinished thing, in the order it should be dealt with. Nothing here
is lost in `tasks.md` or `server_arc.md`; this file is the short list of what is
*not done* and what only the author can do.

Last updated after T021 (committed `0cadd45`, pushed `a5e7914`).

## Platform, in one line

The **server runs on Ubuntu Server OS** and nowhere else. The **application is
installed and operated from Windows** (primary desktop client) and is **also
used on mobile** (Android + iOS). Neither client needs WSL, Docker, Python, or a
repository clone — that is §62, and it is why §61 ("never run ULTRON on this
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
| WebSocket `/ws` (T023) end to end | needs a bound port |
| T027 "server starts", "WebSocket works" | same |
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

### C1. T022 — `/health`, `/ready`, `/metrics`

The next task. The engine (`app/observability/health.py`, T018) is complete and
tested; only the HTTP surface is missing.

Also in scope for T022, and the real item worth doing:

- `app/config/settings.py:940` emits `startup_warnings` for problems like
  `ALLOW_ANONYMOUS=true` in production, and the docstring promises these are
  *"observable through `/health`"*. **`/health` does not report them yet**, so a
  misconfigured production deploy logs one line at boot and then looks healthy
  forever. Surfacing them is what makes the existing design honest.
- `.env.example` says `ALLOW_ANONYMOUS` "Must be false in production" and
  nothing enforces it. The deliberate design is *warn, don't refuse to boot*, so
  the fix is to make the warning visible in `/health` and `/ready`, not to add a
  boot failure.
- `/metrics` needs mounting. `prometheus-client` is already a core dependency and
  installed, so no new package is needed.

### C2. Logout docstring disagrees with behaviour

`auth.logout`'s docstring claims a malformed `Authorization` header is ignored.
Only a **missing** header is; a non-empty malformed value still raises from
`tokens.extract_bearer`. Either fix the doc or handle it in the route — the doc
and the behaviour must agree.

### C3. Branch has never been merged

Everything sits on `feature/phase-1-foundation`, pushed but not merged. Repo
convention (spec §52) is slice → `feature/*` → `develop`, with `main` reserved
for releasable states. Worth doing before the branch gets long.

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

### C6. Client framework is undecided — and it gates the client, not the server

Windows (primary) + mobile from one codebase is the target (spec §62). The
framework is still an open decision (**T310**): Tauri, Flutter, or React Native
for one codebase, versus native per platform.

This is cheap to decide now and expensive to decide late, because it determines
the installer story (T311), the mobile story (T312), and whether the client fits
in memory alongside the server on a 4 GB VM. **No server work is blocked on it**
— it gates T261 and the client tasks, nothing in the API.

Pick it before starting T261. If unsure, the constraint that matters most is
*one codebase for Windows + Android + iOS*, which rules native out.

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
| Mobile client (Android + iOS) | `FUTURE`, a first-class target not an afterthought (§62.4, T312) |
| Client framework choice | **OPEN** — blocks T261, not the API (§62.6, T310) |
| Windows bridge | `FUTURE`, **last** — highest blast radius (§59.15) |
| Firebase Auth as upstream IdP | `FUTURE`, gated on pricing (T300–T305) |
| Firestore copy of the database | **`SKIP`** — rejected, see §60.3 |
| Client-side offline cache + outbox | `FUTURE` (T306–T309) |
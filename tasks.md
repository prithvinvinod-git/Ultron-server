# ULTRON SERVER — MASTER BUILD TASK LOG

Source of truth for the build described in [`server_arc.md`](server_arc.md).

> ## ⚠ DO NOT RUN ULTRON ON THIS WINDOWS MACHINE
>
> **This laptop is a build-and-test machine only. ULTRON is never run here.**
>
> Once the build is complete, ULTRON is **cloned to the author's own server and
> run there**. Nothing on this machine is a deployment target.
>
> This is a deliberate safety boundary, not a limitation to work around:
>
> - **Do not** start the API server (`uvicorn`, `fastapi run`, `python -m app`, a
>   packaged executable, or a Windows service).
> - **Do not** run the web/desktop client, the Orb, a voice session, a device
>   bridge, the scheduler, or any background worker that runs the real runtime.
> - **Do not** enable `BROWSER_ENABLED`, `SCHEDULER_ENABLED`, `COMPUTER_NODES_ENABLED`,
>   `DEVICES_ENABLED`, `VOICE_ENABLED` or `REDIS_EVENT_BRIDGE` on this host.
> - **Do not** point the API at anything outside this machine.
> - **Do not** bind `API_HOST` to anything other than `127.0.0.1`, even locally.
>
> **What *is* allowed here:** writing code, `ruff`, `mypy`, `pytest`, Alembic
> migrations against the local PostgreSQL, and reading files. The local
> PostgreSQL 17 service exists for tests and migrations only — it holds no real
> user data and is loopback-only.
>
> Rationale: agent code executes tools, shells, and browsers (§14–§16). Running
> it on the machine that also holds the author's own files, credentials, and
> development environment risks that environment for no benefit, since the real
> target is a server the author controls end to end. See spec §61.

## How to read this file

| Marker | Meaning |
|---|---|
| `[ ]` | pending |
| `[~]` | in progress |
| `[x]` | **done** — implemented, formatted, linted, type-checked, tests pass |
| `[!]` | blocked — reason recorded inline |

Status annotations for the online-first amendment (§59, §60): `DEFERRED`,
`SKIP - PHASE 1`, `OPTIONAL`, `FUTURE`. These are **not** deletions — a deferred
capability keeps its task and its spec section; only its timing changes.

Rules (from spec §52, §53, §54, §57):

1. A task is only `[x]` when it has a passing test, or is an honest documented
   stub **with** a test **and** a "Known limitations" entry.
2. Never fake availability. If a subsystem is not implemented, ship the
   interface, a safe stub, documentation, and a test.
3. Before declaring a feature complete run:
   `format → lint → typecheck → unit tests → integration tests → build → health check`.
   **Exception:** the health-check and run steps are performed on the author's
   server, never on this machine (§61).
4. Branch per slice: `feature/*` off `develop`, conventional commits, merge to
   `develop`, `main` reserved for releasable states.
5. This file is updated after **every** task, never batched at the end of a phase.
6. Deviations from `server_arc.md` are recorded in the Decision Log at the bottom.

---

## Environment

| Item | Value |
|---|---|
| Dev OS | Windows 10/11, VS Code, PowerShell — **build and test only, never run ULTRON here (§61)** |
| Target OS | Ubuntu Server 26.x, headless, Ethernet — the actual runtime host |
| Python | 3.12+ (installed via winget) |
| Package manager | `uv` |
| Local infra | PostgreSQL 17 as a native Windows service — **tests and migrations only**; no Docker, no Redis |
| Local models | **none** — online providers used for real calls (§59.7); no Ollama, Whisper, Piper, embeddings or vector store on this machine |
| Runtime models | Online providers on the server; Ollama remains an optional offline fallback |
| Repo name | `Ultron-server` (existing remote) |
| Package root | `server/app` |

### Resource discipline (explicit user constraint)

Do **not** run all of the following on the Windows laptop at once:
PostgreSQL + Redis + Ollama + large model + Playwright + several ULTRON agents
+ OpenCode + Chrome with many tabs. Local dev runs
`VS Code + OpenCode CLI + Docker Desktop(postgres, redis, ultron-api)`.
Ollama, Playwright browsers and multi-agent fan-out are enabled only in the
phases that need them, and permanently in production on Ubuntu.

---

## Phase 0 — Prerequisites & repository foundation

- [x] **T001** Install Python 3.12 + `uv` via winget; create `server/.venv`; verify `python -V`, `uv -V`
- [x] **T002** Create full monorepo tree: `server/app`, `server/tests`, `server/migrations`, `clients/{desktop-node,orb,protocol}`, `deployment/{docker,compose,systemd,scripts}`, `docs`, `scripts`
- [x] **T003** `server/pyproject.toml`: deps, optional extras (`browser`, `voice`, `iot`, `providers`), ruff / mypy / pytest config
- [x] **T004** Root hygiene: `.gitignore`, `.editorconfig`, `.dockerignore`, `.gitattributes` (updated with per-type EOL rules)
- [x] **T005** `.env.example` with every placeholder from spec §34 (no real secrets)
- [x] **T006** `scripts/{setup,dev,test,lint,format,start}.ps1` + `.sh` counterparts (spec §36), pathlib-based, no hard-coded Windows paths
- [x] **T007** `README.md` (real content), `server/README.md`, `LICENSE`, `develop` branch, `.gitkeep` for empty dirs
- [x] **T008** Verify Phase 0: `uv sync` succeeds, `scripts/lint` clean, `.env.example` complete

**Phase 0 gate:** `uv sync` succeeds · lint clean · tree matches spec §4 — **PASSED**

> Issues found and fixed during Phase 0:
> - `readme = "../README.md"` is illegal for a build backend → added `server/README.md`.
> - PowerShell 5.1 promotes native stderr to terminating errors under
>   `$ErrorActionPreference = 'Stop'`. The bootstrap now relaxes it around native
>   calls and takes the result from the exit code.
> - `Invoke-UltronNative` returned stdout and the exit code as one array, so
>   callers could not read the code. It now streams output through the pipeline
>   and records the code in `$script:UltronExitCode`.
> - `Set-Content -Encoding utf8` on PowerShell 5.1 emits a UTF-8 **BOM**, which
>   ruff rejected, and whose diagnostic renderer panicked on Windows paths. All
>   generated Python files are written as UTF-8 without BOM.
> - `--all-extras` would have pulled the voice runtimes and Playwright onto the
>   development machine. Sync is now core + `dev` by default; heavy stacks are
>   opt-in via `-Extras` / `ULTRON_SYNC_EXTRAS`.

**Environment as installed**

| Tool | Version |
|---|---|
| Python | 3.12.10 |
| uv | 0.12.21 |
| ruff | 0.16.9 |
| mypy | 2.3.1 |
| pytest | 9.1.1 |
| FastAPI | 0.142.2 |
| SQLAlchemy | 2.1.1 (async) |
| Pydantic | 2.13.5 |


---

## Phase 1 — Foundation

- [x] **T010** `app/config/settings.py` — Pydantic v2 settings, nested sections, env-file resolution, no secrets defaults
- [x] **T011** `app/observability/logging.py` — JSON structured logs, `request_id`/`task_id`/`agent_id` contextvars, redaction
- [x] **T012** `app/core/errors.py` — typed exception hierarchy + error codes (incl. `LOCAL_MODEL_UNAVAILABLE`)
- [x] **T013** `app/database/session.py` — SQLAlchemy 2.0 async engine, session factory, declarative `Base`
- [x] **T014** `app/database/models/` — 16 tables from spec §23 (`users`, `sessions`, `agents`, `tasks`, `task_steps`, `tool_executions`, `events`, `conversations`, `messages`, `memories`, `projects`, `devices`, `device_events`, `agent_logs`, `model_usage`, `audit_logs`) + `schedules`
- [x] **T015** `app/database/repositories/` — repository pattern per aggregate
- [x] **T016** `server/migrations/` — Alembic init + async template, pgvector-aware, first revision
- [x] **T017** `app/database/redis_client.py` — Redis wrapper (cache, locks, pubsub, transient state only)
- [x] **T018** `app/observability/health.py` — health checks: PostgreSQL, Redis, Ollama, filesystem, agent runtime, event bus
- [x] **T019** `app/container.py` — hand-rolled DI composition root
- [x] **T020** `app/main.py` — FastAPI factory + lifespan, exception handlers, router mounting
- [x] **T021** `app/api/dependencies.py` — container access, correlation IDs, auth dependency stub. Written: `app/security/{passwords,tokens,audit,authentication}.py`, `app/api/dependencies.py`, `app/api/routes/auth.py`, correlation-ID + access-log middleware in `app/main.py`, and unit tests for each. Route-level tests over `TestClient` are in place (35 tests) and the full gate passes. Also verified against a live PostgreSQL 17 instance with `ENABLE_PGVECTOR=false`: login, token authentication, refresh rotation and replay refusal all behave, and the audit trail carries `auth.login` / `auth.token_refresh` / `auth.token_reuse_detected`. See delivery notes below.
- [x] **T022** `app/api/routes/health.py` — `/health`, `/ready`, `/metrics` — **done.** Written: `app/api/routes/health.py`, `app/observability/metrics.py`, plus `ContainerProtocol.health`. `/health` is liveness via `HealthService.liveness()` and is deliberately **dependency-free** — it never opens a database connection, because a liveness probe that consulted PostgreSQL would restart a healthy process on every blip and turn one dependency's hiccup into an outage of every replica. `/ready` returns `503` unless a **required** check is `OK` (PostgreSQL only, §23); an optional failure degrades without removing the instance (§33). Both now carry `startup_warnings`, which keeps the promise in `Settings.startup_warnings` ("observable through `/health`") — before this those warnings were logged once at boot and then invisible. Warnings drop `status` to `degraded` but never fail the probe, because a restart cannot fix configuration and a crash loop is not diagnosable. `/metrics` uses `prometheus_client` (already a core dependency, no new package) behind a dedicated `CollectorRegistry` rather than the process-global default, which any import could collide with; status is **one-hot** (`{check,status}` label set to 1) rather than collapsed onto magic numbers. `404` when `METRICS_ENABLED=false`, because an empty body reads as "healthy, nothing to report". 16 new tests, 928 total.
- [x] **T023** event stream endpoint — **done.** The original `app/api/websocket/manager.py` + `/ws` was replaced by an **authenticated, receive-only SSE** endpoint (`app/api/routes/events.py`, `GET /events`) per the T311 decision; first-frame auth was impossible (a receive-only stream has no first message), so the resolution is `fetch()`-based streaming with an `Authorization: Bearer` header and hand-written client backoff — `EventSource` cannot send headers, so it stays out of the contract and the token never enters a URL or proxy log. Code landed in `277aac0` but no gate was ever run; this entry closes that debt: `tests/unit/test_event_stream_routes.py` (28 tests — auth refusals, streaming headers, topic filter, resume via `Last-Event-ID`, capacity 503, disconnect cleanup) plus the T024-carried stream-connect check in `test_api_smoke.py` (`/events` in the mounted-surface assertion, anonymous 401 against the real `create_app()` app). Gate: ruff/format/mypy clean over 108 files, **1021 tests pass**. Two structural fixes found while gating — inherited `ENABLE_PGVECTOR` env isolation in `tests/conftest.py`, and `TestClient`'s inability to buffer an endless SSE response (the 11 open-stream tests now drive the route directly via `open_stream()`) — are recorded in the delivery notes below and `todo.md` C12/C13.
- [x] **T024** `server/tests/` Phase 1 suite — config, logging, session, health, API smoke — **done.** Most of the listed areas were already covered by unit tests (config 40, logging 51, session 32, health 71, repositories 132). The genuine gap was **API smoke**: no test exercised the *assembled* app, so nothing caught route-table drift. Added `tests/unit/test_api_smoke.py` (13 tests, now 14) covering the mounted surface, OpenAPI generation, the error envelope on 404/405, unauthenticated 401, correlation-ID middleware, and real-container construction. 942 total at the time. The event-stream connect check carried below landed with T023.
- [ ] **T025** `deployment/docker/Dockerfile.dev` + root `docker-compose.yml` dev stack (`postgres`, `redis`, `ultron-api`) - `DEFERRED` - not a Phase 1 blocker. No Docker on the dev machine (spec 61); native PostgreSQL 17 serves tests and migrations instead.
- [x] **T026** Migrate real PostgreSQL + create schema via Alembic - `DONE` on the dev machine - Alembic applied to native PostgreSQL 17 with `ENABLE_PGVECTOR=false`, 17 tables + `alembic_version`, `memories.embedding` is `jsonb`. Evidence: revision chain applied end-to-end on loopback PG17, schema verified (T021 live checks ran against it). Re-run on the server — tracked in `todo.md` B1/H, not here.
- [x] **T027** **PHASE 1 verification** — server starts, PostgreSQL connects, Redis connects, `/health` works, WebSocket works - `REVISED` - 'server starts' and 'WebSocket works' cannot be checked here (spec 61 forbids running ULTRON on this machine); they move to the server. Remaining local half: `/health` and `/ready` over `TestClient`, in T022 — **done** (16 tests, live-probe semantics covered). Local half closed; server halves tracked in `todo.md` B1/H and must be run before the phase gate is claimed.

**Phase 1 gate (spec §51):** server starts successfully

**T010 delivered**

`server/app/config/settings.py` (24 sections) + `server/tests/conftest.py` +
`server/tests/unit/test_config_settings.py` (46 tests).

Design decisions taken here, so later phases build on them rather than re-decide:

- Flat env vars are the ingestion layer; 24 typed section models hold every
  range and vocabulary rule. `Settings.sections()` is called from a
  `model_validator`, so an out-of-range value fails at construction instead of
  at first use.
- A subsystem depends on the slice it needs (`settings.database`), not the whole
  object. `settings.startup_warnings()` reports misconfiguration at boot
  without refusing to start, so a bad deployment is still observable on
  `/health`.
- `ULTRON_ENV_FILE` names an env file explicitly and **wins outright**; a named
  file that does not exist is an error, not a silent fall back to defaults.
- `is_disallowed_host()` is the SSRF guard from spec §31 and **fails closed**:
  a host that cannot be proven public is refused.
- Secrets are `SecretStr`; absent provider keys mean *unconfigured*, never
  *working*. Nothing is faked to make a path appear available.
- Terminal commands ship with an empty allow-list, so no shell execution is
  possible until an operator opts in.

> Issues found and fixed during T010:
> - `LOG_LEVEL=debug` crashed the process: the normalising validator existed
>   only on the section model, not on the flat field that reads the variable.
>   `.env.example` shows uppercase, so this would have bitten an operator, not
>   the tests. Now normalised on both.
> - `API_PORT=70000` was accepted: the `ge`/`le` bounds were declared on
>   `AppSettings` but omitted from the flat field. Same class of bug, and the
>   reason `Settings.sections()` now runs on every construction.
> - The test teardown rebuilt settings while the deliberately invalid variable
>   was still set, turning expected failures into errors. Teardown now only
>   clears the cache.
> - `scripts/lint` ran `mypy app` only, so no test file had ever been type
>   checked despite the `tests.*` override in `pyproject.toml`. Both `lint.ps1`
>   and `lint.sh` now check `app` and `tests`: 49 files instead of 47.

**T011 delivered**

`server/app/observability/logging.py` + `server/app/observability/__init__.py` +
`server/tests/unit/test_observability_logging.py`.

- `JsonFormatter` emits one object per line; `ConsoleFormatter` is the same data
  for a terminal. Both agree on attribution: a field set via `extra` wins over
  the ambient `contextvars`, so a caller can override precisely.
- Redaction is **key-based and recursive**, so a credential inside a nested
  request body goes. `redact_keys` comes from `LoggingSettings`, so an operator
  can extend the list without a code change.
- `log_operation()` emits paired `start`/`end` records, uses `exc_info` so the
  formatter decides how much to show, and stamps the context on both sides so a
  failure is traceable to the operation that caused it.
- `Timer` is the sync/`async` `contextmanager` used where a `log_operation` call
  would be noisy; both honour the same context.

> Issues found and fixed during T011:
> - The console formatter did `getattr(record, field)` for correlation ids with
>   no default and **raised `AttributeError` on any record that did not carry
>   one** — meaning every console log line outside an active request crashed the
>   handler. It now falls back to the ambient context.
> - The console formatter mutated a clone's extras using the **unredacted**
>   values, so `logger.info("x", extra={"api_key": ...})` leaked the secret to
>   the terminal while the JSON output was clean. Extras are now redacted.
> - `apiKey` and `x-api-key` were **not** matched: matching was a substring test
>   against the raw key, so separators defeated it. Keys and configured names are
>   now normalised to bare alphanumerics first.
> - A secret interpolated into the **message** was never redacted, because
>   key-based redaction has no key to look at. Added `scrub_text()` as a
>   deliberately narrow second layer.
> - The traceback was appended **after** redaction, so a driver exception
>   carrying a DSN leaked `postgres://user:password@host`. A driver exception
>   routinely contains the connection string it failed on. The traceback is now
>   scrubbed, and a test pins it.
> - `Authorization: Bearer <token>` was only half-redacted: the general
>   `key=value` rule stopped at the first space, leaving the token's tail in the
>   log. Pattern order is now most-specific-first so a bearer token is consumed
>   whole.

> On redaction breadth: an earlier version of the text patterns matched any
> `sk-`-prefixed run of 6+ characters, which passed the tests and would have
> mangled ordinary output containing a hyphenated word. The threshold is now 16
> characters, matching real key formats, and `test_ordinary_hyphenated_text_is_not_mangled`
> and `test_a_short_hyphenated_word_is_not_scrubbed` pin the narrowness. A test
> fixture that used a 7-character fake token was corrected rather than the
> pattern widened — a 7-character value is not a credential, and over-broad
> scrubbing trains operators to ignore the markers.

**T012 delivered**

`server/app/core/errors.py` + `server/app/core/__init__.py` +
`server/tests/unit/test_core_errors.py` + `docs/errors.md`.

45 error classes over 43 codes, every one carrying a stable `ErrorCode`, an
`http_status`, and a `retryable` verdict.

- Retryability is stated **twice** — the class flag read by `is_retryable()`, and
  `RETRYABLE_CODES` read by the model router's fallback chain. A test enforces
  agreement in both directions across every concrete class, so neither can drift
  from the other.
- `MigrationRequiredError` subclasses `DatabaseError` but is **not** retryable:
  a pending migration must be applied, not waited out.
- `to_dict()` reports a cause's **type only**, never its text, because a driver
  exception can embed a connection string with a password in it.
- `degrade()` absorbs a `UltronError` and returns the fallback, but **re-raises**
  a programming error, so a `TypeError` can never be disguised as a missing
  service. The loss is logged as a warning; spec §33 forbids silently swallowing
  an exception.
- `docs/errors.md` catalogues every class and is verified against the running
  code rather than hand-maintained.

> Issues found and fixed during T012:
> - `HEALTH_CHECK_FAILED` was **missing from `RETRYABLE_CODES`** while
>   `HealthCheckFailedError` was marked retryable. The two sources disagreed, so
>   the model router would have treated a startup health failure as permanent.
>   Caught by adding the two-directional agreement test.
> - `ModelTimeoutError` could not record the timeout budget, so a provider
>   timeout lost the evidence an operation timeout kept. `timeout` is now a
>   `ModelError` keyword argument.
> - `NotFoundError` **required** an identifier, so a lookup that matched no row
>   had nothing honest to pass. The id is now optional and omitted from
>   `details` when unknown.
> - The original contract test asserted every error was constructible from a
>   single message. That was simply the wrong invariant: most errors derive
>   their message from a resource name, provider, or tool, precisely because
>   that is what makes it actionable. Replaced with one factory per type, plus a
>   test that the sample list cannot drift from the type list.
> - The first version of the retryable-code test ended in `or code in
>   RETRYABLE_CODES`, which made it a tautology that could never fail. Rewritten
>   to walk the real subclass tree, so it covers all 45 classes rather than the
>   22 sampled ones.
> - `is_retryable()` had a duplicated `CancelledError` branch, and a `TypeVar`
>   left over from converting `degrade` to PEP 695 syntax.

**T013 delivered**

`server/app/database/session.py` + `server/app/database/__init__.py` +
`server/tests/unit/test_database_session.py`.

- Nothing connects at import or construction, so the layer is testable before
  PostgreSQL exists. `create_async_engine` is lazy and the driver check happens
  at construction.
- **No module-level engine.** Construction belongs to the composition root
  (`app/container.py`, T019), so an import can never open a socket and a test
  cannot mutate global state.
- `expire_on_commit=False` is set deliberately. The default expires every
  attribute at commit, so the next access triggers a lazy load, which in async
  SQLAlchemy raises `MissingGreenlet` unless it happens to land in greenlet
  context. `test_attributes_survive_a_commit` is that regression, pinned.
- `autoflush=False`: a flush is a write, and a read that silently writes pending
  changes is a surprise. Repositories flush explicitly.
- `session_scope` is the unit of work for a write — commit on success, roll back
  on *any* failure, so a handler that commits halfway cannot leave a partial
  aggregate. `read_session_scope` always rolls back, so a read can never persist
  even by accident.
- **SQLAlchemy exceptions are deliberately not translated here.** A repository
  has to tell `IntegrityError` (duplicate key) from `OperationalError` (server
  went away) to decide whether to retry; a blanket translation destroys exactly
  that. Wrapping happens in the adapter that understands the failure. `ping()`
  is the exception, because nothing else is in the way and a bare
  `OperationalError` would leak the DSN.
- `aiosqlite` added as a **dev** dependency so transaction semantics are proved
  against a real database on a bare checkout. It is a test double only;
  PostgreSQL types (JSONB, pgvector) cannot be created there, so these tests
  declare their own minimal models rather than reusing T014 ones.
- `masked_url()` exists because a DSN reaches error messages constantly and a
  DSN carries the password.

> Issues found and fixed during T013:
> - The `ck` naming convention used `%(constraint_name)s` while the comment
>   claimed the opposite. Switching to `%(column_0_N_name)s` to match the comment
>   was **also wrong**, and the test proved it: a check written as a string
>   literal has no associated columns, so it produced `ck_unnamed_check_` — a
>   trailing-underscore name that would collide for two unnamed checks on one
>   table. Reverted to the explicit-name form, which fails loudly at import
>   instead of shipping a constraint no migration can target. The trade-off is
>   now documented in the code and pinned by
>   `test_an_unnamed_check_fails_loudly`.
> - A synchronous URL such as `postgresql://` reached SQLAlchemy as an opaque
>   dialect error. Now refused with a `ConfigError` naming the fix.
> - SQLite rejects `pool_size` and `max_overflow`, so the pool arguments are
>   backend-dependent, and in-memory SQLite needs `StaticPool` or each connection
>   gets its own empty database.
> - Two test-only mistakes caught by the tests rather than assumed: a `unique`
>   column produces a constraint and not an index, and an index lives in
>   `table.indexes` rather than `table.constraints`.

**T014 delivered**

`server/app/database/models/` (14 modules) + `server/tests/unit/test_database_models.py`.

17 tables. The two `§23` lists disagree on `device_events`, `agent_logs`, and
`schedules`; `devices` is the only table the spec gives two different field lists
for.

- **The spec specifies no columns for 14 of the 17 tables.** It names
  `agents` (12 attributes, §6 L443-454), `tasks` (12 fields, §18 L917-928), and
  `devices` (§26 L1205-1213). Everything else — every type, every primary key,
  every index, every foreign key — is an engineering decision, marked as such in
  each module docstring so it can be told apart from the three places the spec
  actually spoke. Nothing was invented and presented as a requirement.
- **UUID primary keys everywhere.** The spec exposes `/agents/{id}` (L1314) and
  names ids `<entity>_id`, but never says the type. UUID because an id has to
  exist before the first flush: an agent can be live in the runtime, or an event
  emitted, before its row is written.
- **`agents.task_id` rather than `tasks.agent_id`.** The spec lists `agent_id`
  as a task field (L924), which reads as a contradiction. It is not: §7 requires
  an agent to be paused and resumed, so a task outlives any single assignment.
  The reverse reference keeps the history; a column on `tasks` would lose the
  assignment the moment the agent was destroyed. `Task.current_agent_id` exposes
  it for reads.
- **`tasks.steps` is a relationship, not a column.** Spec §18 lists `steps`
  among a task's fields (L926). Modelling them as rows in `task_steps` is what
  makes the restart recovery §18 asks for (L949) possible — a JSON array on the
  task dies with the process.
- **`tool_executions.permission_level` and `.decision` are NOT NULL.** §15
  requires every execution to be permission-checked and logged (L817-821).
  Mandatory columns make an unchecked execution *unrepresentable* rather than
  merely discouraged.
- **`events.event_type` is a plain string, not an enum.** The spec's own event
  lists disagree (`DEVICE_OFFLINE` at L2957 vs `DEVICE_DISCONNECTED` at L985),
  and T030 has not yet fixed the canonical set. A database enum would reject
  types the bus must carry. This is the one place the package deliberately does
  *not* use its own enum convention.
- **`PermissionLevel` carries its digit as a `StrEnum` value with a `.level`
  property.** A member must be a string, but L797-814 makes the number
  meaningful, so `allows()` compares `level` rather than a lookup table.
- **`memories.embedding` follows `ENABLE_PGVECTOR`** — `Vector(768)` when on,
  JSONB when off, so disabling the extension degrades semantic retrieval
  instead of making the package unimportable. `vector_dimensions` is stored
  redundantly so a wrong-width embedding is *detectable* rather than silently
  unsearchable. `CREATE EXTENSION` is issued by the T016 revision, not the
  application; the application role should not hold that privilege.
- **`model_usage.cost_usd` is `Numeric(12,8)`, never float.** Money must not
  accumulate binary rounding error. `total_tokens` is computed, not stored, and
  is `None` when only one side was recorded — a failed call can burn prompt
  tokens, and reporting that as 0 would understate spend.
- **`sessions` keeps only token hashes**, with refresh rotation and
  `rotated_from` linking a replacement to what it supersedes. The old row is
  revoked, never deleted: if a revoked token reappears the chain is the evidence
  of reuse, and deleting it destroys exactly that.
- **Collection relationships use `lazy="raise_on_sql"`**, so touching an
  unloaded collection raises instead of silently issuing a query whose cost grows
  with the table.
- 100% line coverage on the package; ruff and mypy clean.

> Issues found and fixed during T014:
> - `lazy="noload"` is **deprecated in SQLAlchemy 2.1** and, meanwhile, returns
>   `None` for related items — a relationship that silently reads as empty.
>   `raise_on_sql` is the replacement, and 13 deprecation warnings became 0.
> - `Memory.embedding_as_json` decoded a `memoryview` as **UTF-8**. That is
>   wrong: an embedding provider returns a numpy array, so the buffer is flat
>   float32, and decoding it as text cannot work. Now unpacked as little-endian
>   float32 and checked against `vector_dimensions`; a buffer that is not a whole
>   number of float32s is rejected rather than truncated, because a shortened
>   embedding is unsearchable rather than obviously wrong.
> - `DeviceEvent.is_offline_signal` matched `device_offline` and `disconnected`
>   but missed `DEVICE_DISCONNECTED` — the name §19 actually uses (L985). The
>   spec gives three spellings for one fact, so all three are recognised.
> - `check_name_shape` is the guard for enum member names (SQLAlchemy persists
>   the *name*), and its own test could not be written as a class body — a
>   non-identifier member name is a `SyntaxError`. It is built through the
>   functional `StrEnum(...)` API instead.
> - `_embedding_type` was called with `None` "because the settings are fixed at
>   import", which quietly hard-coded 768 and ignored `EMBEDDING_DIMENSIONS`
>   entirely. Now resolves real settings with a documented fallback.
> - `Schedule.is_due` returned `False` for a correctly-configured schedule,
>   because column `default`s are applied by the database on INSERT, so an
>   unflushed object has `status is None`. The test now sets `status`
>   explicitly and says why; the predicate was left alone because a row read
>   from the database always has it.
> - Two test bugs caught by the tests: `unique=True` compiles to
>   `CREATE UNIQUE INDEX`, not `CREATE INDEX`; and `tasks.error` is `Text`, not a
>   JSON payload column, so it does not belong in the JSONB assertion table.

**T016 delivered**

`server/alembic.ini`, `server/migrations/env.py`, `server/migrations/script.py.mako`,
`server/migrations/versions/0001_initial.py` +
`server/tests/unit/test_migrations.py` (24 tests).

One revision, all 17 tables, 94 indexes, written out explicitly rather than
autogenerated — autogenerate emits what the models happen to say today and gives
no account of *why*, and this is the revision every later one is compared against.

- **`env.py` imports `app.database.models` explicitly.** SQLAlchemy does not scan
  for models, so a metadata object with no imports in it is simply empty. The
  failure is silent rather than loud: Alembic connects, compares an empty
  registry against a populated database, and reports success while proposing to
  drop all 17 tables. `test_empty_metadata_proposes_no_drops` guards it.
- **The DSN comes from `get_settings().database.url`, not from `alembic.ini`.**
  A migration cannot then be pointed at a different database than the application
  by editing a config file, and no password is ever written to a tracked file.
  The URL is masked in the log line that records it.
- **Enum columns are `VARCHAR` holding `StrEnum` *names*, with no `CHECK`
  constraint.** The names appear in API payloads and event types, and a native
  enum cannot be altered without locking a table. The database will accept a
  non-member; the guard is that the value is a `StrEnum` member on read, is typed
  at every repository call site, and its membership is asserted in the model
  tests. A `CHECK` per enum would need its own migration per added member.
- **The revision matches the models to the character.** `test_revision_ddl_matches_models`
  compares compiled PostgreSQL DDL, not introspected attributes, because two
  declarations can be different objects and identical statements — `JSONB()` and
  `JSON().with_variant(JSONB(), "postgresql")` are such a pair, as are
  `sa.text("'{}'")` and `server_default="{}"`. Only the rendered statement
  settles it. Under `compare_server_default=True` the difference would otherwise
  have been permanent phantom drift on 11 columns.
- **`tool_executions.permission_level` and `.decision` are `NOT NULL` with no
  server default**, and `audit_logs.occurred_at` likewise: an audit row records
  when the operation was *attempted*, and a row that stamped itself at flush time
  would misreport a decision made earlier.
- **pgvector is created by the migration, never by the application.**
  `CREATE EXTENSION IF NOT EXISTS vector` runs first so the migration needs a
  role permitted to create extensions, and the application role does not.
- **The `ENABLE_PGVECTOR` branch lives in the migration, not the model.** A model
  that inferred its own column type from an environment variable would mean the
  schema depends on an env var, and a database created with the flag off could not
  be read by a process with it on.
- Offline mode (`alembic upgrade head --sql`) is supported and needs no driver
  installed, which is what makes a migration reviewable without a database.
  Rendering currently needs `url` passed to `context.configure` explicitly;
  without it there is no engine to read one from and nothing is emitted.

> Issues found and fixed during T016:
> - `tasks.parent_task_id` had **two indexes over the same single column** —
>   `index=True` on the column and an explicit `Index("ix_tasks_parent", ...)` in
>   `__table_args__`. An exact duplicate buys nothing and doubles write
>   amplification on the hot task table. Removed, and
>   `test_no_duplicate_indexes_per_table` now fails on any recurrence.
> - The first draft of the revision wrote JSON defaults as `'{}'::jsonb` against
>   the models' `"{}"`. Both are valid PostgreSQL, but the *quoted* form renders
>   as an empty `DEFAULT ''` on a directly-typed JSONB column, and it would also
>   break `create_all` on SQLite, which the models must support. All 11 columns
>   now use the models' plain-string form.
> - `context.configure` was missing `url` in offline mode, so
>   `alembic upgrade --sql` raised *"Connection, url, or dialect_name is
>   required"* and emitted nothing. It now passes the settings DSN.
> - `env.py` promised *"the URL is masked in any log line"* while logging
>   nothing at all. Added the log line, masked, so the docstring is kept rather
>   than merely asserted.
> - `mypy` on the revision: `Returning Any from function declared to return
>   "TypeEngine[object]"`. The model's own `_embedding_type` returns `Any` for the
>   same reason — pgvector is imported lazily so a deployment without the package
>   still works when the extension is off, and an unimportable module cannot be
>   type-checked. Matched it rather than annotating around it.

> **Not yet verified against a live PostgreSQL.** The DDL is proven identical to
> the models' and renders cleanly, but no migration has actually been applied to
> a real server — that is T026, and it needs Docker Desktop, which is unavailable
> here. The pgvector branch in particular is verified only as generated SQL.

---

## Phase 2 — Core

- [x] **T030** `app/events/types.py` — **done.** The task's "33 event types from §19 + orb/agent-window events (§27, §28)" is §19's 30 names plus §27's three `ORB_*` ones; the delivered catalog is the full **71-name canonical set**, because each later section declares itself an addition to *this* set under the no-rename rule: §27 (5), §64.9 node/orb/agent states (11), §64.15 `ESP32_` forms (2), §65.15 call lifecycle (8), §66.8 planner/permission/voice/model-fallback names (15). `EventType` is a `StrEnum` grouped by provenance, with `CANONICAL_EVENT_TYPES` for plain-string membership and `is_canonical()`. Topic derivation (`topic_for`, ordered prefix table, exact orb-state match) moved here from `bus.py` so one module owns names *and* routing; `bus` re-exports the same objects (identity-tested), and `KNOWN_TOPICS` is derived from the tables — the SSE route's 422 gate and its `Available:` description now read it, so a future prefix cannot be unsubscribable. Deliberately absent: `stream.*` transport signals (stay in `bus`) and `DEVICE_OFFLINE` (the §19/§58 spelling conflict stays unresolved until T335 — one spelling, not two guesses). Topic order is load-bearing (`VOICE_CALL_` before `VOICE_` or calls land on the voice-pipeline panel). Evidence: `tests/unit/test_event_types.py` (36 tests, spec tables transcribed from `server_arc.md` so drift fails), +1 stream-route test for the new topics; gate green, **1058 tests total**.
- [x] **T031** `app/events/bus.py` — async pub/sub, wildcards, queue backpressure, optional Redis bridge — **done (written under T023, assessed here).** `EventBus`/`Subscription`/`EventEnvelope`: async publish, topic fan-out with full wildcard (`*`), bounded per-subscriber queues that drop the oldest and hand the subscriber a `stream.lagged` resync signal instead of blocking the publisher, bounded replay for `Last-Event-ID`, subscriber cap with honest refusal (`503`), health snapshot for readiness. Topic derivation moved to `app/events/types.py` in T030 with identity-verified re-exports. The **Redis bridge is the optional half and is deliberately absent**: Redis is deferred project-wide (`todo.md` C5) and the bus docstring records the trade — single-process delivery now, swap point is the narrow `publish`/`subscribe` surface later, no rewrite. Evidence: `test_event_bus.py` 50 tests (wildcard, filter, backpressure drop order, lag visibility, replay + eviction, close/limit/counter semantics), gate green, **1058 tests total**.
- [x] **T032** `app/events/handlers.py` — built-in subscribers (logger, memory, WS fan-out) — **done, delivered as the durable write-through the `events` table promises.** Of the three named subscribers, only one has a source today and the other two would be net-negative scaffolding: the **logger** is already implied by `EventBus.publish`'s per-event transport log (a subscriber re-logging would double every line), the **WS fan-out** was replaced by T023's per-client SSE subscriptions (each client owns a bus subscription), and **memory** has no producer until Phase 7 (`app/memory/` is empty). Delivered instead: **`PersistEvents`**, the §19 async subscriber for the `events` table — wildcard subscription, private worker task over a bounded bus queue (drop-don't-block, same rule as SSE tabs), filters `EventEnvelope.persist` (new per-event opt-in flag wired through `EventBus.publish(persist=...)` and deliberately excluded from `to_dict`: a routing instruction, not wire data — high-volume `STT_PARTIAL`-class events stay live-only by default), injectable writer (tests use a recorder; production appends via `EventRepository` with `sequence`=envelope.id and UUID-coercing `task_id`/`agent_id`/`device_id`), failure isolation (a dropped database increments `failed` and the worker survives — never kills the publisher), `written`/`failed`/`dropped` counters + `snapshot()` for a health subscriber, idempotent start/stop with stop draining the queue (engineered around a real bug: `Subscription.close()`'s wake-up sentinel was suppressed on a full queue, so a draining consumer could wait forever — close now makes room by sacrificing the oldest event, counted as a drop). Container wiring: attached in `startup()` behind `settings.observability.events_persist` (default true), stopped before the bus closes in `shutdown()`. Retention (`event_retention_days`) stays Phase 3+ work as the model already documents. Evidence: `tests/unit/test_event_handlers.py` (10 deterministic tests — flag plumbing + wire exclusions, skip-non-persist, failure isolation, stop-drains, exact backpressure counts, container attach/off paths), lint+mypy clean over 112 files, gate green, **1068 tests total**. Root `.env.example` already carries `EVENTS_PERSIST=true`, `REDIS_EVENT_BRIDGE=false`.
- [ ] **T033** `app/security/permissions.py` + `app/core/permissions.py` — LEVEL 0–5, policy, allow/deny/confirm, audit every decision
- [ ] **T034** `app/tools/base.py` — `Tool` ABC: name, description, `input_schema`, `permission_level`, `execute()`, `verify()`
- [ ] **T035** `app/tools/registry.py` — register/lookup/list/describe, schema validation
- [ ] **T036** `app/tools/executor.py` — pipeline: schema → permission → policy → execute → verify → event → result
- [ ] **T037** Mock tools for tests (`mock.echo`, `mock.fail`, `mock.sleep`, `mock.write_state`)
- [ ] **T038** `app/tasks/state.py` — task status/priority state machine with legal transitions
- [ ] **T039** `app/tasks/manager.py` — persistent task CRUD, task fields per spec §18
- [ ] **T040** `app/tasks/graph.py` — hand-rolled DAG (nodes, edges, topological order, dependency waiting, cycle detection)
- [ ] **T041** `app/tasks/executor.py` — graph execution, restart recovery, `SCHEDULE_TRIGGERED`-style events
- [ ] **T042** `app/agents/base.py` — `Agent` ABC with the 12 attributes from spec §6 + lifecycle state machine
- [ ] **T043** `app/agents/registry.py` — agent-type registry, no hard-coded OpenCode/Windows deps
- [ ] **T044** `app/agents/manager.py` — create/destroy/pause/resume/cancel/inspect/assign task|model|tools|permissions, concurrency limits
- [ ] **T045** Mock agents (mock agent, long-running agent, failing agent) for tests
- [ ] **T046** `app/core/context.py` — context manager (conversation state, namespaces)
- [ ] **T047** `app/core/router.py` — intent classification / routing
- [ ] **T048** `app/core/planner.py` — plan decomposition into task graph
- [ ] **T049** `app/core/orchestrator.py` — `REQUEST → UNDERSTAND → PLAN → TASK → AGENT → MODEL → EXECUTE → OBSERVE → VERIFY → RETRY/REPLAN → COMPLETE → RESPOND`
- [ ] **T050** `app/core/executor.py` — Core-level execution helpers, retry/replan policy
- [ ] **T051** `app/api/routes/{agents,tasks,tools,events}.py` — REST surface for Core
- [ ] **T052** Phase 2 tests — event bus, permissions, registry, executor, task lifecycle, agent lifecycle, concurrency, E2E request→result
- [ ] **T053** **PHASE 2 verification**

**Phase 2 gate:** multiple agents run concurrently; one agent failure does not affect others

---

## Phase 3 — Model system

- [ ] **T060** `app/models/base.py` — `ModelProvider` ABC, message/response/tool-call types, streaming contract, capability model
- [ ] **T061** `app/models/ollama.py` — configurable host, discovery, health, chat, NDJSON streaming, availability check, timeout, error mapping - `FUTURE` - adapter still wanted as an offline fallback, but **no local model runtime is installed or assumed** (spec 59). Not a Phase 1 dependency.
- [ ] **T062** `app/models/openai.py` — optional adapter, lazy key check, unavailable when unconfigured
- [ ] **T063** `app/models/gemini.py` — optional adapter
- [ ] **T064** `app/models/anthropic.py` — optional adapter
- [ ] **T065** `app/models/fake.py` — deterministic scripted provider for tests
- [ ] **T066** `app/models/router.py` — `select(capability=, speed=)`, fallback chain, availability, resource awareness, user preference, no hard-coded model names
- [ ] **T067** Model usage recording → `model_usage` table
- [ ] **T068** Wire model-driven tool-calling loop into the agent base
- [ ] **T069** Phase 3 tests — adapter contract tests via `respx`, router selection matrix, streaming, `LOCAL_MODEL_UNAVAILABLE` path
- [ ] **T070** **PHASE 3 verification** — system functions with only Ollama configured - `REVISED` - verification no longer requires a local model. It must pass with an online provider configured and `OLLAMA_URL` unreachable, proving spec 33 degradation.

**Phase 3 gate:** works with only Ollama configured; no crash when unavailable

---

## Phase 4 — Coding agent

- [ ] **T080** `app/workspaces/manager.py` — register projects, locate repos, create isolated workspaces, branches/worktrees, status, diffs, cleanup
- [ ] **T081** `app/agents/coding/engine.py` — `CodingEngine` ABC + task/event types
- [ ] **T082** `app/agents/coding/opencode_engine.py` — configurable exe path, model/provider, cwd, input, streamed output, exit code, timeout, cancellation, logs, process cleanup, never inherits server dirs
- [ ] **T083** `app/agents/coding/agent.py` — CodingAgent (inspect, plan, edit, test, build, git, report)
- [ ] **T084** Coding verification — edit → run tests → exit code → verify files → SUCCESS/PARTIAL/FAILED/UNVERIFIED
- [ ] **T085** `server/tests/fixtures/sample_repo/` — small sample repository for real tests
- [ ] **T086** Phase 4 tests — engine contract, timeout, cancel, cleanup, workspace isolation, end-to-end coding task
- [ ] **T087** **PHASE 4 verification**

**Phase 4 gate:** coding task runs against a real sample repo and verifies

---

## Phase 5 — Tool runtime

- [ ] **T090** `app/tools/filesystem/` — read, write, list, search, delete; sandboxed to allow-listed roots
- [ ] **T091** `app/tools/terminal/` — allow-listed commands, permission gate, timeout, capture, no raw LLM shell
- [ ] **T092** `app/tools/git/` — status, diff, branch, log, worktree
- [ ] **T093** `app/tools/python/` — restricted subprocess execution, timeout, no arbitrary import side effects
- [ ] **T094** `app/tools/web/` — fetch with SSRF guard, read, extract
- [ ] **T095** `app/tools/system/` — CPU, memory, disk, GPU, processes, network, services, Ollama, Docker
- [ ] **T096** `app/notifications/` — `NotificationProvider` ABC + log/webhook implementations (spec §45)
- [ ] **T097** Register all tools in the container with declared permission levels
- [ ] **T098** Phase 5 tests — each tool + permission denial + sandbox escape attempt
- [ ] **T099** **PHASE 5 verification**

**Phase 5 gate:** no tool bypasses schema/permission/policy/verification

---

## Phase 6 — Research + browser agents

- [ ] **T110** Web tool abstraction — pluggable search providers, no direct LLM HTTP
- [ ] **T111** `app/tools/browser/` session manager — Playwright, per-agent isolated context
- [ ] **T112** Browser tool set — open, click, type, select, inspect, extract, screenshot, download, cookies, close
- [ ] **T113** `app/agents/browser/agent.py` — BrowserAgent
- [ ] **T114** `app/agents/research/agent.py` — ResearchAgent: search, retrieve, extract, compare, summarize, cite, preserve URLs
- [ ] **T115** Research artifact persistence
- [ ] **T116** Phase 6 tests — session isolation, navigation, extraction; Playwright tests marked/skipped when not installed
- [ ] **T117** **PHASE 6 verification**

**Phase 6 gate:** sessions isolated per agent; research output cites sources

---

## Phase 7 — Memory

- [ ] **T120** Memory base types + namespace rules (`user`, `project:*`, `agent:*`)
- [ ] **T121** `short_term.py` — conversation/context memory, Redis TTL + PG
- [ ] **T122** `long_term.py` — persistent user/project facts
- [ ] **T123** `episodic.py` — what ULTRON did previously
- [ ] **T124** `semantic.py` — embeddings + vector retrieval (pgvector) - `SKIP - PHASE 1` - pgvector is excluded by policy and absent from the stock Windows PostgreSQL build. Spec section 22 retained; semantic retrieval is Phase 7.
- [ ] **T125** `project.py` — project-scoped memory
- [ ] **T126** `manager.py` — facade, cross-layer retrieval
- [ ] **T127** Embedding provider abstraction (Ollama embeddings, cloud, honest unavailable path) - `REVISED` - keep the honest-unavailable path, drop the assumption that a local embedding provider exists. `EMBEDDING_PROVIDER=none` is valid and must degrade, not fail.
- [ ] **T128** Wire memory into Core (context read/write, episode record on task completion)
- [ ] **T129** Phase 7 tests — namespace isolation, retrieval ranking, persistence
- [ ] **T130** **PHASE 7 verification**

**Phase 7 gate:** unrelated project memories never mix

---

## Phase 8 — Computer node

- [ ] **T140** `clients/protocol/` — shared node protocol JSON schemas (commands, results, capabilities, auth)
- [ ] **T141** `app/agents/computer/gateway.py` — node registration, heartbeat, command dispatch, result correlation
- [ ] **T142** Node authentication (device tokens, mTLS-ready)
- [ ] **T143** `app/tools/computer/` — screenshot, click, type, keys, window list, app control (structured commands only)
- [ ] **T144** `app/agents/computer/agent.py` — ComputerAgent
- [ ] **T145** Capability fallback chain — API → DOM → UIA → shortcuts → mouse → vision
- [ ] **T146** `clients/desktop-node/` — contract, test double node, API contract tests
- [ ] **T147** Phase 8 tests — gateway, auth, command/result correlation, fallback selection
- [ ] **T148** **PHASE 8 verification**

**Phase 8 gate:** Core has zero direct Windows-Use dependency; no raw shell from server

---

## Phase 9 — Voice

- [ ] **T150** `app/voice/stt/` — `STTEngine` ABC + faster-whisper adapter + availability probe - `DEFERRED` - voice phase. Cloud STT provider first; a local faster-whisper adapter is optional and never assumed (spec 59.17).
- [ ] **T151** `app/voice/tts/` — `TTSEngine` ABC + Piper adapter + availability probe - `DEFERRED` - voice phase. Cloud TTS provider first; a local Piper adapter is optional and never assumed (spec 59.18).
- [ ] **T152** `app/voice/wake/` — wake-word event handling from ESP32 → `WAKE_DETECTED`
- [ ] **T153** `app/voice/manager.py` — voice sessions, STT→Core→Agent→TTS pipeline
- [ ] **T154** Streaming/partial transcripts + interruption (barge-in) handling
- [ ] **T155** Phase 9 tests — session lifecycle, partial events, interruption; adapters skipped when binaries absent
- [ ] **T156** **PHASE 9 verification**

**Phase 9 gate:** missing STT/TTS engines degrade honestly, never fake

---

## Phase 10 — ESP32 / IoT

- [ ] **T160** `app/devices/protocol.py` — device message envelope, device fields per spec §26
- [ ] **T161** `app/devices/manager.py` — registry, status, `last_seen`, capabilities, auth
- [ ] **T162** `app/devices/esp32/` — JSON-over-WebSocket transport, command/response, keepalive
- [ ] **T163** Optional MQTT transport adapter
- [ ] **T164** `app/agents/iot/agent.py` — IoT agent
- [ ] **T165** Device state persistence + `device_events`
- [ ] **T166** Phase 10 tests — device lifecycle, event flow, auth, wake-word path
- [ ] **T167** **PHASE 10 verification**

**Phase 10 gate:** no specific ESP32 board hard-coded in Core

---

## Phase 11 — Automation

- [ ] **T170** `app/scheduler/manager.py` — APScheduler async, one-time / recurring / delayed jobs, persistent job store
- [ ] **T171** Event-triggered automation — subscribe to bus, evaluate rules, create tasks
- [ ] **T172** Persistent workflows
- [ ] **T173** Notification dispatch wiring (Core → providers)
- [ ] **T174** Phase 11 tests — one-time, recurring, delayed, event-triggered
- [ ] **T175** **PHASE 11 verification**

**Phase 11 gate:** `SCHEDULE_TRIGGERED` fires and produces a real task

---

## Phase 12 — Observability

- [ ] **T180** `app/observability/metrics.py` — Prometheus registry, metric definitions per subsystem
- [ ] **T181** `/metrics` endpoint + counters/gauges/histograms for agents, tasks, tools, models, bus
- [ ] **T182** Resource monitor → `CPU_HIGH`, `RAM_HIGH`, `DISK_LOW`, `GPU_HIGH` with configurable thresholds
- [ ] **T183** Agent/task monitoring + model-usage metrics
- [ ] **T184** `deployment/compose/{prometheus,grafana}` + dashboard JSON (optional compose profile)
- [ ] **T185** Phase 12 tests — metric registration, threshold events
- [ ] **T186** **PHASE 12 verification**

**Phase 12 gate:** `/metrics` scrapes clean; thresholds emit events

---

## Phase 13 — Production deployment

- [ ] **T190** `deployment/docker/Dockerfile` — multi-stage, non-root user, no dev deps - `DEFERRED` - no Docker. The server path is non-Docker systemd (T194); keep this as a documented alternative.
- [ ] **T191** Root `docker-compose.yml` — `ultron-api`, `postgres`, `redis`, `ollama`; optional `prometheus`, `grafana`; explicit `depends_on` + healthchecks - `DEFERRED` - no Docker Compose. Notably it also assumed `ollama` on the host, which the online-first policy excludes.
- [ ] **T192** `deployment/systemd/ultron-api.service`, `ultron-worker.service` - `FUTURE` - the actual deployment path for the author's server. Needed before ULTRON runs anywhere.
- [ ] **T193** Ubuntu deployment docs — clone → prerequisites → `.env` → `docker compose up -d` → migrations → health check - `REVISED` - the documented path is non-Docker systemd, not `docker compose up`. Written for the server, not this machine.
- [ ] **T194** Non-Docker / systemd deployment path - `FUTURE` - **this is the deployment path**, superseding T190/T191/T193.
- [ ] **T195** `deployment/scripts/` — backup, restore, migrate, health check
- [ ] **T196** Security hardening — no public PostgreSQL/Redis/Ollama, non-root, secret handling
- [ ] **T197** `/opt/ultron` server directory layout (spec §47) with persistent volumes
- [ ] **T198** **PHASE 13 verification**

**Phase 13 gate:** `docker compose up -d` on a clean machine reaches healthy

---

## Phase X — Documentation

- [ ] **T200** `README.md` — what ULTRON is, quickstart, status table
- [ ] **T201** `docs/architecture.md` — layers, Mermaid diagrams
- [ ] **T202** `docs/development.md` — Windows workflow, scripts, branching
- [ ] **T203** `docs/deployment.md` — Ubuntu + Docker + systemd
- [ ] **T204** `docs/configuration.md` — every env var
- [ ] **T205** `docs/agents.md`
- [ ] **T206** `docs/tools.md`
- [ ] **T207** `docs/models.md`
- [ ] **T208** `docs/memory.md`
- [ ] **T209** `docs/security.md`
- [ ] **T210** `docs/computer-nodes.md`
- [ ] **T211** `docs/voice.md`
- [ ] **T212** `docs/esp32.md`
- [ ] **T213** `docs/troubleshooting.md`
- [ ] **T214** `docs/api.md`
- [ ] **T215** **FINAL §56 DEFINITION-OF-DONE verification** — every checkbox, with evidence

**Final gate:** spec §56 checklist complete, with honest notes for anything unverified

---

## Definition of Done (spec §56) — live status

| # | Check | Status |
|---|---|---|
| 1 | Server starts | pending |
| 2 | PostgreSQL connects | pending |
| 3 | Redis connects | pending |
| 4 | API works | pending |
| 5 | WebSocket works | pending |
| 6 | Health endpoint works | pending |
| 7 | Agent Manager works | pending |
| 8 | Multiple agents run concurrently | pending |
| 9 | Task persistence works | pending |
| 10 | Event bus works | pending |
| 11 | Permissions work | pending |
| 12 | Tool registry works | pending |
| 13 | Ollama integration works | pending (mocked locally; real run on Ubuntu) |
| 14 | Model Router works | pending |
| 15 | Coding Agent works | pending |
| 16 | OpenCode integration works | pending |
| 17 | Browser Agent works | pending |
| 18 | Research Agent works | pending |
| 19 | Memory works | pending |
| 20 | Verification works | pending |
| 21 | Logs work | partial — T011 structured logging delivered; not yet exercised by a running server |
| 22 | Configuration works | done (T010) |
| 23 | Tests pass | pending |
| 24 | Docker deployment works | pending |
| 25 | Ubuntu deployment documented | pending |

---

## Decision log

| # | Decision | Rationale |
|---|---|---|
| D001 | Dev stack = Docker Desktop with `postgres`, `redis`, `ultron-api` only | 8 GB laptop; Ollama/Playwright/multi-agent reserved for production or on-demand |
| D002 | No local model during development; cloud providers for real calls | Avoids large model downloads on the laptop; Ollama is a production/host concern |
| D003 | Full monorepo skeleton in Phase 0 | Matches spec §4; clients/ and deployment/ are first-class, not afterthoughts |
| D004 | Hand-rolled DI container in `app/container.py` | Spec §57: prefer boring infrastructure over clever abstractions; avoids a DI framework dependency |
| D005 | Extra package `app/workspaces/` beyond spec §4 tree | Required by spec §46; §4's tree is structural, not exhaustive |
| D005a | Notifications live in `app/tools/notifications/`, not `app/notifications/` | Notification delivery is a tool invoked through the tool pipeline (spec §45 with §16), so it belongs with its sibling tools rather than in a top-level package |
| D006 | `clients/protocol/` holds shared node protocol schemas | Single source of truth for the Windows node ↔ server contract (spec §13, §28, §42) |
| D007 | `server/pyproject.toml` per spec §4; tool config lives there | Keeps root free of Python packaging concerns |
| D008 | One package added to §4 tree marked as a deviation | See D005; recorded so the deviation is intentional and traceable |
| D009 | Integration tests skip cleanly when PostgreSQL/Redis/Docker absent | Test suite must pass on a bare checkout without paid APIs or services |
| D010 | Task DAG hand-rolled instead of `networkx` | Avoids a heavyweight dependency for a small, well-understood algorithm |

---

## Phase status log

Appended after each phase, per spec §51/§58.

<!-- PHASE STATUS BLOCKS BELOW -->

### Phase 0 — complete

| Item | Result |
|---|---|
| Branch | `develop` @ `d6fcb94` (`main` untouched at `dd50040`, not pushed) |
| `uv sync` | Succeeded, core + `dev` only |
| `scripts/lint` | Ruff clean, 46 files formatted, mypy clean |
| Delivered | Monorepo tree, `pyproject.toml`, `.env.example` (spec §34 complete), Windows + Linux scripts, READMEs, LICENSE, `develop` branch |
| Deliberately absent | No `app/main.py` yet, so `scripts/dev` and `scripts/start` cannot run. Expected: that is T020. |
| Blocked elsewhere | No Docker Desktop, so PostgreSQL/Redis integration waits for T025/T026 |

### Phase 1 — in progress

| Item | Result |
|---|---|
| Branch | `feature/phase-1-foundation` (from `develop` @ `d6fcb94`) |
| Done | T010–T020 foundation, T021 auth stack, T022 probes, T023 SSE event stream, T024 test suite, T026 Alembic migration, T027 local verification — **17 of 18** (only T025 remains, `DEFERRED`: no Docker) |
| `scripts/lint` | Ruff check + format clean; mypy clean over `app`, `tests` and `migrations/env.py` — 108 files (fixed: `explicit_package_bases` for the duplicate-`conftest` clash) |
| `scripts/test` | 1021 passed, integration and e2e deselected (was 807) |
| `docs/errors.md` | Catalogue generated from the running code and diffed against it, so it cannot drift; now covers `LockUnavailableError` |
| Next | Phase 2 begins at T030. Uncommitted: gate fixes, T023 delivery notes, §64–§66 docs (`server_arc.md`, `tasks.md`, `todo.md`). |
| Still blocked | Server halves of T027 (live start, Redis/Ollama connect, SSE on a real socket) need the server (`todo.md` B1/H); Docker-dependent integration tests need T025. The full phase gate ("server starts successfully") cannot be claimed on this machine — spec 61. |

#### T017 delivery notes

Scope is the four uses the task line names — cache, locks, pub/sub, transient
state. Spec §24 lists six, and the other three arrive with their consumers rather
than landing here unasked: queues with the event bus (T031), rate limiting with
the security work, and event-stream coordination (T023, since built as SSE).
All three are built on the primitives in this module.

Four decisions were confirmed before implementation, because the spec is silent on
each and every one is expensive to reverse later:

- **Locks are token + compare-and-delete.** `SET NX PX` then `DEL` is wrong: a
  holder that stalls past its lease frees the *next* holder's lock. Release and
  extend are both Lua compare-and-act. No implicit renewal — a background renewal
  task outlives the work that wanted the lock, so `extend` is the caller's call.
- **A cache read raises by default.** `get_json` raises `RedisError`;
  `get_or_none` is the separate, named cache-aside path that treats an outage as a
  miss and logs it. Collapsing the two would turn a Redis outage into a silent
  cold cache.
- **Values are JSON, JSON-native or nothing.** A `datetime`, `UUID`, `Decimal`, or
  `set` is refused at the write rather than coerced via `default=str`, because a
  coerced value reads back as a string with no way to tell it was ever a datetime.
  `tuple` → `list` is the one silent change, and it is pinned by a test.
- **Keys and channels are namespaced under `ultron:`**, in separate segments, so a
  cache key and a pub/sub channel of the same logical name cannot collide and a
  shared instance can be swept safely.

Two notes on the delivery:

- `LockUnavailableError` was added to `app/core/errors.py`. It reuses
  `ErrorCode.CONFLICT` rather than adding a code — to a client, "someone else got
  there first" is the same answer — but is a distinct type, and is deliberately
  **not** retryable: a blanket "retry on `RedisError`" is right for an outage and
  a thundering herd for contention.
- The dev dependency is now `fakeredis[lua]`, not `fakeredis`. The `lua` extra is
  required, not optional: without it `fakeredis` answers `EVAL` with "unknown
  command", leaving the lock's safety-critical path as the one thing in the module
  the unit suite cannot exercise. A test asserts the capability is present so the
  failure names the missing dependency instead of surfacing inside whichever lock
  test runs first.

Unit coverage is 86 tests. The lock tests deliberately let a lease expire
underneath its holder and then assert the loser's release leaves the winner's lock
intact — the failure mode a "acquire, release, key gone" test cannot see. Reverting
the compare-and-delete to a bare `DEL` was confirmed to fail that test.

`LockUnavailableError` and `RedisError` are now sampled by
`tests/unit/test_core_errors.py` as well, so the T012 contract suite covers the
retry/status/secret rules the two share. A cached-value detail is the reason: the
retryable-code agreement test and the HTTP-status table are module-wide, so a new
error class is checked automatically for *self-consistency* but nothing else had
asserted the verdict that actually matters here — that a Redis outage is retryable
while lock contention is not, and that the contention error is not a `RedisError`
subclass that a blanket "retry on `RedisError`" would swallow.

#### T018 delivery notes

All six checks from the task line are present, but only four can be real today:
`agent_runtime` (T044) and `event_bus` (T031) have no implementation to probe. They
are registered as absent and reported as `skipped` rather than dropped, because a
check that silently vanishes from the report is indistinguishable from one that
passed. The report always carries the six names, so `/ready` can be diffed against
a known list; T019 registers the four real ones at startup and the last two appear
on their own when their subsystems land, with no edit here.

Decisions worth writing down, because each is a judgement the spec does not make:

- **Only PostgreSQL gates readiness.** Redis, Ollama and the filesystem are
  optional and degrade. The authority chain already says this (PostgreSQL is the
  store; Redis is cache, locks and pub/sub), and a readiness gate that restarts a
  container because the cache is down trades a degraded service for no service.
- **Liveness never touches a dependency.** `/health` answers "this process is
  alive" and nothing else. A liveness probe that consulted PostgreSQL would restart
  every replica during a database blip, turning one dependency's outage into an
  outage of the whole fleet. That is why the two endpoints are separate methods
  rather than one probe with a flag.
- **The probes raise; the wrapper classifies.** `check_postgresql`, `check_redis`
  and `check_ollama` let `DatabaseError`, `RedisError` and
  `LocalModelUnavailableError` propagate instead of catching them into a status.
  Those types carry a retry verdict and an HTTP status that a health check would
  flatten away, and a caller using a check directly would otherwise get a
  healthy-looking result from a failed database. Status assignment is the
  wrapper's job, where required-vs-optional is known.
- **`TimeoutError` is caught separately.** It is not a subclass of `OSError`, so
  the `except OSError` net that turns an unreachable host into `failed` does not
  see it. A silently unbounded probe is the failure mode that takes the whole
  `/ready` endpoint down with it, so the budget is enforced per check and a
  timed-out optional check degrades rather than failing.
- **The filesystem check writes a real file.** `os.access(W_OK)` returns `True`
  on paths that a read-only mount or a restrictive ACL still refuses, so it would
  report a filesystem as writable that cannot accept the uploads the API takes.
  The probe creates and deletes a temp file instead, and its leftovers are the
  bug it was written to prevent.

On the hosted Ollama: `OLLAMA_URL` was already configurable and the default stays
`http://localhost:11434`. What was missing is that a hosted instance needs a
credential, so `OLLAMA_API_KEY` was added (as a `SecretStr`, alongside the existing
provider keys) and `OllamaSettings.auth_headers()` omits the header entirely when
no key is set rather than sending an empty one. Local and hosted are then the same
code path — `GET /api/tags` with an optional bearer — instead of two code paths
that drift. No inference code was touched: T018 only answers whether the provider
is reachable.

Unit coverage is 55 tests, and the suite injects every dependency (fake engine,
`Redis` double, fake HTTP client, `tmp_path`) so it runs on a bare checkout with
no PostgreSQL, no Redis and no network. Four mutations were applied and reverted to
confirm the tests actually bite, since a health report that is wrong in the
permissive direction is worse than no report:

| Mutation | Caught by |
|---|---|
| required set emptied, so PostgreSQL becomes optional | 6 tests, incl. a timed-out required check going ready |
| expected checks omitted instead of reported `skipped` | 5 tests |
| per-run lock removed | the concurrent-callers test (one run, many waiters) |
| `asyncio.timeout` removed | the hung-check test, which then took 60s to fail |

The last two are the ones a happy-path suite usually misses, and the lock in
particular is the difference between one probe per TTL window and a thundering
herd of probes against the pool that is already struggling.


#### T019 delivery notes

The container holds the application's singletons: settings, engine, session factory, Redis client, and health service. It does not hold repository instances because they are bound to a session. Instead, it provides factory methods to create repositories given a session.

Construction is lazy: the engine, session factory, and Redis client are built on first access. This allows the container to be instantiated without connecting to external services, making it testable on a bare checkout.

The container registers the four health checks that are available today (PostgreSQL, Redis, Ollama, filesystem) using lambdas that capture the container's dependencies. The agent_runtime and event_bus checks are omitted and will be registered by their respective subsystems when implemented (T031, T044).

The container provides async context managers for session scopes: `session_scope` for transactional writes and `read_session_scope` for read-only operations that always roll back.

The container also includes startup and shutdown lifespan methods that ping the database and Redis, and dispose of resources.


#### T024 delivery notes — Phase 1 test suite

The listed areas were already covered by unit tests, so the work was to find the
gaps and to test the one thing no unit file could: the application *as wired*.

`tests/unit/test_api_smoke.py` (13 tests) is the only test that builds the real
app through `create_app()` and walks the whole route table. It found four real
problems, three of which no existing test could see.

**1. Two error shapes on one API.** A 404 from an unmatched path and a 405 from
a wrong method are raised by Starlette's router, not by ULTRON code, so they
never became an `UltronError` and bypassed the handler entirely. They answered
with FastAPI's bare `{"detail": "Not Found"}` while every other failure answered
`{"error": {...}}`. Every client would have needed a special case. Fixed with a
`StarletteHTTPException` handler plus a `_HTTP_ERROR_CODES` table, and a new
`ErrorCode.METHOD_NOT_ALLOWED` backed by `MethodNotAllowedError` (405 is
permanent, not retryable — repeating the identical request cannot help).

**2. `/health` and `/ready` were declared twice.** `create_app` mounted the
health router *and* defined both endpoints inline, after the mount. The inline
copies lost the match and were unreachable, while still reading as the real
handlers — a maintenance trap pointing at dead code. The duplicates are removed;
the router versions are authoritative. The OpenAPI schema **cannot** catch this:
duplicate path/method pairs collapse into one entry. The smoke test detects it
by asserting on the *shape of the live response* instead.

**3. `METRICS_ENABLED` defaulted to `true` in three places** (`Settings`,
`ObservabilitySettings`, `.env.example`) while the spec, `todo.md` C10 and the
T022 notes all said metrics are off unless enabled. The route tests passed the
flag in explicitly, so nothing ever exercised the shipped default — the tests
were green and the endpoint was open. Aligned the code to the documented intent
(`false`), and added `test_metrics_default_to_disabled` to assert the *default*
rather than a fixture-forced value.

**4. A latent test bug.** `settings_warned` in `test_health_routes.py` scraped
`/metrics` while relying on the default being `true`. Changing the default broke
it, which is the correct outcome — it had been asserting against a default
rather than the behaviour it meant to test.

Two things this file deliberately does not do: it does not enter the
`TestClient` context, so the container lifespan never runs and no database or
socket is opened; and it does not touch `/ready`, because readiness runs the
health engine, which takes seconds to fail against a real PostgreSQL and Redis.
`/health` is dependency-free and answers immediately.

**Closed with T023:** the event-stream connect check now lives in this file
(`test_event_stream_refuses_an_anonymous_caller`, plus `/events` in the
mounted-surface assertion) and runs against the real `create_app()` app. The
happy path cannot travel over `TestClient` — starlette 1.7 buffers an endless
stream to completion — so it is covered by the T023 route tests via direct
drive instead.

#### T020 delivery notes

The application factory creates a FastAPI instance with:
- The dependency injection container wired into a `lifespan` context manager
  (startup pings DB+Redis, shutdown disposes resources)
- Exception handling driven by each error's own `http_status` and `code`, so the
  wire contract lives in exactly one place (`app/core/errors.py`)
- Middleware configured from settings, not hardcoded: CORS origins and allowed
  hosts both come from configuration and both default to *closed*
- Health endpoints: `/health` (liveness) and `/ready` (readiness, answered by
  `HealthService` and 503 when a required check fails). Both are served by the
  health router mounted in `_include_routers`; T024 removed the duplicate inline
  definitions that used to sit alongside it in `create_app`
- Router mounting framework ready for T022–T024 (auth, connection manager, WS routes) and T036–T039 (agents, tasks, memories, conversations)

The application is importable without external services: `from app.main import create_app` works on a bare checkout, enabling testing and Docker/CI usage without running PostgreSQL or Redis.

#### T020 security hardening (spec §30, §31)

A security audit against §1, §15–17, §29–31 and §40 found the factory itself
was the weakest part of the code written so far. Six defects were real, not
hypothetical, and four of them were in this task's own output:

| Defect | Was | Now |
|---|---|---|
| CORS | `allow_origins=["*"]` with `allow_credentials=True` — the combination browsers reject, so it granted nothing while reading as permissive | Reads `CORS_ORIGINS`; empty means *disabled*; credentials only with explicit origins; mixing `*` with explicit origins is a startup `ConfigError` |
| TrustedHost | `allowed_hosts=["*"]`, with no setting to change it | Reads the new `ALLOWED_HOSTS`; middleware is only installed when configured |
| Error codes | `exc.error_code` — an attribute that does not exist, so every response was labelled `INTERNAL_ERROR` | `exc.code.value` |
| Error statuses | A hand-written `isinstance` ladder that had drifted from `errors.py`: permission denials and `CommandNotAllowedError` returned **500** instead of 403, `SerializationError` returned 400 instead of 500, and `DatabaseError` had no branch at all | `exc.http_status`, asserted against the contract by a parametrized test |
| Validation errors | `exc.errors()` straight into `JSONResponse`: `ctx` holds exception objects, so a client mistake became a 500 — and `input` would have reflected an attempted password back from `/auth` | Reduced to `loc`/`msg`/`type`, and JSON-encoded defensively |
| Logging | `configure_logging()` was never called, so the redaction layer was inert and `logging.lastResort` printed raw records to stderr | Called first in `create_app`, before anything logs |
| `/ready` | Hardcoded `{"status": "ready"}` — reported health the process did not have | Delegates to `HealthService`; 503 when a required check fails |
| `/docs` | Served in every environment, cataloguing the whole route surface | Disabled when `ENVIRONMENT=production` |

Two further defects were found in T019 code by the new tests:

- `container.py` referenced `settings.workspace`; the property is `workspaces`.
  The filesystem health check would have raised `AttributeError` at runtime, so
  the workspace boundary was never actually being probed.
- `Container.read_session_scope` had lost its `@asynccontextmanager` decorator,
  so `async with container.read_session_scope()` failed at runtime.

`ALLOWED_HOSTS` was added to `.env.example`, and `startup_warnings()` now also
warns in production about a `0.0.0.0` bind and an empty `CORS_ORIGINS`. That
function existed and was never called, so a deployment could run for weeks with
a placeholder admin password and nothing would say so.

Ruff now selects the `S` (flake8-bandit) rules, which is the family that flags
`allow_origins=["*"]` combined with `allow_credentials=True` — the pair of
checks that should have caught all of this the first time. Test files are
exempt from the hardcoded-secret rules because their fixtures exist to prove
redaction works.

`tests/unit/test_main.py` adds 40 tests. The three security-critical
behaviours were mutation-checked: restoring wildcard CORS fails 4 of them,
reintroducing the `error_code` typo fails 5, and making `/ready` always answer
200 fails 1.

Still missing, and tracked as later tasks rather than fixed here: permission
enforcement (T033), rate limiting (T035) and the tool execution pipeline
(T036). Event-stream authentication has since closed with T023 (`fetch()` +
`Authorization` header; `todo.md` §G2).

#### Security audit: remaining gaps against §15/§16/§17/§30/§31

The same audit confirmed that the *design* for these requirements is already in
place; what is missing is the code that uses it. Nothing below is a surprise to
the spec — it is a list of schemas, settings and error types with no caller.

| Requirement | Ready | Missing | Task |
|---|---|---|---|
| Secure password handling | `users.password_hash` (Argon2id), `SecuritySettings` cost params | No hashing, no verification; `argon2-cffi` never imported | T022 |
| Session / token architecture | `sessions` with `token_hash`, `refresh_token_hash`, rotation chain, reuse detection | No issuance or verification; `PyJWT` never imported | T022 |
| API keys for machine clients | `users.is_service_account`, `api_key_hash` | No issue/revoke path | T022 |
| Device authentication | `devices.auth_token_hash`, `ESP32_REQUIRE_AUTH` | Never read or checked | T024 |
| Role / permission checks | `PermissionLevel` L0–L5 with `allows()` | Nothing consumes it; no role enum | T033 |
| Audit log for sensitive operations | `AuditLogEntry`, `AuditOutcome`, `AuditLogRepository` | No row is ever written | T033 |
| Rate limiting | `rate_limit_enabled`, `rate_limit_requests` | No middleware | T035 |
| Schema validation (pipeline stage 1) | — | — | T035/T036 |
| SSRF guard | `is_disallowed_host`, fails closed on unresolvable names and metadata IPs | Only stubbed; no caller | T036 |
| Shell allow-list | `TerminalSettings.allowed_commands=[]` denies everything | No matcher | T036 |
| Filesystem boundary | `WorkspaceSettings.allowed_paths()` | Unenforced outside the health check | T036 |
| `/metrics` | `prometheus-client`, `metrics_path` | Never mounted | T022 |

Two settings are also unused scaffolding and are worth wiring before Phase 1 is
called done: `security.require_auth` (defaults to `True`, so the fail-closed
intent is already there) and `startup_warnings()`'s output (now called).

#### T023 delivery notes — SSE event stream

`277aac0` wrote the endpoint and its tests but never ran a gate; this entry
closes that debt and records what the gate found.

**What exists.** `app/api/routes/events.py`: `GET /events`, authenticated by
the standard `Authorization: Bearer` dependency, subscribes to the real event
bus and sends named server-sent events until the client disconnects. Topic
selection validates against the known vocabulary (422 naming what is allowed);
`Last-Event-ID` / `lastEventId` resume a dropped stream from the event store;
`Retry:` hints are emitted while the backoff itself lives client-side
(`fetch()`, never `EventSource`, so the token never enters a URL or proxy log
— `todo.md` §G2). 503 on subscription-capacity exhaustion, heartbeats keep
proxies from idling the connection, and every exit path (client disconnect,
capacity refusal, bus error) unsubscribes so nothing leaks.

**The gate found three structural problems, none visible before it:**

1. **`TestClient` cannot stream.** starlette 1.7's transport runs the app to
   completion and buffers the whole response, so a *successful* endless SSE
   request hangs forever — `client.stream` included, because headers do not
   return until the app finishes. The 11 open-stream tests now drive
   `stream_events` directly through a typed `open_stream()` helper;
   `TestClient` remains correct for the finite paths (401/422/503).
2. **Ambient environment leaked into the test suite.** This machine's shell
   exports `ENABLE_PGVECTOR=false` (user scope), so model import picked `JSONB`
   for `memories.embedding` while `clean_settings` fixtures claimed the default
   `True`. `tests/conftest.py` now scrubs every settings env var at import
   time, before models load.
3. **mypy had never seen the test tree.** `explicit_package_bases` plus
   `mypy_path` resolved the `conftest` vs `tests.conftest` duplicate-module
   clash, exposing 26 real typing errors across 5 test files (untyped route
   locals, `Response | Awaitable[Response]`, dead `type: ignore`s).

**Evidence:** ruff check + format + mypy clean over 108 files;
`scripts/test.ps1` = 1021 passed (smoke 14); `test_event_stream_routes.py`
28/28 in 2s.

---

## Capability extensions (spec §59, added after T020)

Spec §59 is additive: it extends sections 1-58 rather than replacing them, and
§59.1 maps every requested capability to the section it extends so nothing is
built twice. Tasks are numbered T220+ to avoid colliding with the original
sequence.

**These are not next.** The build continues through Phase 1 (T022+) in order;
this block records the work so it is not rediscovered later. Nothing here may be
started before its dependency in §59.28.

### Phase 3 additions — model system (§59.7-§59.9)

- [ ] **T220** Model capability registry (§59.8) — per-model record: id, provider, context length, tool/reasoning/coding/vision support, resource class, discovered availability, `free`/`paid`. Dependency: T060. Blocks T221, T223.
- [ ] **T221** `app/models/openrouter.py` (§59.7) — OpenRouter adapter, **free models only**. Refuses a non-free configured model at settings load with `ConfigError`; optional key; 429 mapped to `ModelRateLimited`, not an outage. Dependency: T220.
- [ ] **T222** Model capability probe — refresh `availability` from the provider so a delisted free model degrades instead of failing. Dependency: T221.
- [ ] **T223** Fallback chain + failure taxonomy (§59.9) — per-capability chain, bounded retries with exponential backoff, failures classified into the existing typed errors, **context overflow is not retried**, each hop recorded in `model_usage` (T067). Dependency: T221.
- [ ] **T224** Degradation is visible — a fallback to Ollama is surfaced to the user and to observability, never a silent weaker answer. Dependency: T223.

### Phase 5 additions — tool runtime (§59.3-§59.6)

- [ ] **T230** Git tools as structured tools (§59.3) — one tool per operation, three tiers: SAFE (status/diff/log/branch/show), CONTROLLED (create/switch branch, commit, pull, stash), EXPLICIT AUTHORIZATION (push, force-push, `reset --hard`, `clean -fd`, `branch -D`, rebase, `filter-branch`). Level declared on the tool so §16 enforces it; **no shell fallback**. Dependency: T036.
- [ ] **T231** Filesystem layer (§59.4) — real-path resolution (symlinks followed, `..` collapsed) **before** the `WorkspaceSettings.allowed_paths()` test; read/write/list/stat/search/move/copy/delete; delete and out-of-workspace write at least CONTROLLED. Dependency: T036.
- [ ] **T232** Server management tools (§59.5) — cpu/ram/disk/temperature/processes/services/logs/ports/network/uptime/docker status; one structured tool per operation; every tool time-bounded; restart/stop at EXPLICIT AUTHORIZATION; `deploy` acts on an approved definition only. Dependency: T036.
- [ ] **T233** Tool registry completeness (§59.6) — assert every registered tool declares name, description, `input_schema`, `permission_level`, `execute`, `verify`, timeout, logging, error mapping, audit flag; categories `filesystem git terminal process network server docker browser search database http notifications voice audio display automation`. Dependency: T035.
- [ ] **T234** Audit an agent cannot bypass — a test proving a direct tool import (bypassing the Tool Router) fails the permission check. Dependency: T036.

### Agent additions (§59.10-§59.12, §59.22)

- [ ] **T240** Research citations (§59.10) — per-source extraction before synthesis; every claim bound to a collected source; unsupported claims omitted or marked; **model output is never a source**. Dependency: T036.
- [ ] **T241** Decision-support agent (§59.11) — options / criteria / documented facts / tradeoffs / unknowns / missing constraints; presents rather than silently decides; recommendations labelled with reasoning and uncertainty; asks for missing constraints instead of assuming. Dependency: T047 (routing). Needs no tools, so it can be built early.
- [ ] **T242** Health-information agent (§59.12) — informational only. **Boundaries enforced in the agent/tool layer, not by prompt**: no self-presentation as a doctor, no diagnosis with certainty, no dangerous treatment instructions, no autonomous high-stakes decisions. Informational framing only; routes to professional care; **not related to `HealthService`**. Dependency: T033.
- [ ] **T243** Scoped context policy (§59.23) — context assembly is an explicit, auditable, testable step; narrowest-satisfying scope by default; cross-project memory reads are a boundary violation; voice session state cannot leak across sessions. Dependency: T046, T107.

### Voice additions (§59.16-§59.20)

- [ ] **T250** Local STT on the stated hardware (§59.17) — smallest viable `faster-whisper`, **no GPU assumption**, chunked/streaming where practical, partial vs final results, VAD and silence detection. Dependency: Phase 9 STT.
- [ ] **T251** **Utterance-boundary rule** (§59.17) — a pause must not cause the preceding audio to be retransmitted as a new utterance; explicit boundaries + overlap policy, deduplicated, testable in isolation. Fixture: a mid-sentence pause yields one utterance with no duplicated text. Dependency: T250. **Design this before the STT adapter, not after.**
- [ ] **T252** Local TTS interface + Piper (§59.18) — engine behind an interface, Piper default, priority order latency > RAM > CPU > naturalness > offline > chunked; sentence-level synthesis; voice configurable. Dependency: Phase 9 TTS.
- [ ] **T253** Barge-in and cancellation (§59.19) — stop TTS immediately on user speech, cancel the in-flight response, discard queued synthesis; **a cancelled synthesis must actually stop filling its buffer**; interrupted partial turn marked, not silently dropped. Dependency: T250, T252.
- [ ] **T254** LiveKit self-hosted transport (§59.16) — room/session orchestration for mic in and audio out; budgeted in the §48 RAM plan; **a room token is not a ULTRON credential** and a voice session still resolves to an authenticated principal before any agent runs. Dependency: T253. - `REVISED` - cloud LiveKit is the default (spec 59.16). Self-hosting stays supported and stays deferred; no LiveKit server runs on the 4 GB VM or the 8 GB laptop.
- [ ] **T255** Offline voice path (§59.20) — local STT + Ollama + Piper with no transport at all; capabilities the local model lacks are reported unavailable rather than answered more weakly. Dependency: T254. - `FUTURE` - fully offline voice remains supported but is explicitly out of scope until hardware allows it.

### Desktop additions (§59.13-§59.15)

- [ ] **T260** Orb visual states (§59.13) — IDLE / LISTENING / THINKING / SPEAKING / PROCESSING / ERROR / NOTIFICATION, driven by server events over §28, **never predicted client-side**; ERROR and NOTIFICATION distinct from IDLE; gestures per §59.13; quick action configurable. Dependency: T028.
- [ ] **T261** Desktop client shell (§59.14) — orb + voice + notifications + local tools; renders state and forwards intent only; **no agent logic and no model access on the client**, so §15 stays server-authoritative. Dependency: T260.
- [ ] **T262** Windows tool bridge (§59.15) — launch app, active window, keyboard/mouse, screenshot, clipboard, browser automation, process management. **Outbound client connection only** (no inbound LAN listener, consistent with §31); every capability separately enable-able; per-request authorisation; **no unrestricted control exposed to any agent**. Dependency: T261, T033. **Highest blast radius — last.**

### Cross-cutting

- [ ] **T270** Observability additions (§59.24) — `authorization_level` (evaluated and granted; denials record the level refused at) and `token_usage` where reported, **absent rather than estimated**; correlatable with §15 audit rows and the request correlation id; never log §30 tokens. Dependency: T032.
- [ ] **T271** Load-shedding order (§59.25) — unload local model -> drop LiveKit -> stop background agents/schedules -> reduce agent fan-out -> report degradation; observable; **correctness is never shedding material**, so pressure must never reduce a permission evaluation. Dependency: T031, T044.

### Not yet decomposed

- [ ] **T290** Extension test matrix (§59.26 phase G) — agent routing, tool permissions, Git safety, filesystem sandbox escape, server management safety, model fallback, OpenRouter free-model routing, Ollama fallback, STT accuracy, TTS latency, LiveKit latency, voice interruption, desktop/server communication, RAM usage.
- [ ] **T291** Update §56 Definition of Done with the extension items, with honest notes for anything unverified.

### Extension decisions

| # | Decision | Rationale |
|---|---|---|
| D011 | OpenRouter is integrated free-models-only, and a non-free configured model is a startup `ConfigError` | The design must not depend on paid inference. Failing at startup is the only outcome that gets noticed, and free-tier 429s are handled as a routing condition (§59.9) rather than an outage |
| D012 | Extension sections append to `server_arc.md` as §59 and map to the existing sections | Avoids a second source of truth. §59.1 records, per capability, whether it is new or an extension, so a reader can tell real work from restatement |
| D013 | Extension tasks are T220+, appended as a separate block rather than renumbered into Phases 3/5/9 | The existing IDs are referenced from `tasks.md`, the Decision log and the phase status log. Renumbering would break those references for no benefit |
| D014 | Extensions are recorded but **not scheduled ahead of Phase 1** | §53 and §57 require finishing the current phase in order. T025-T027 are still blocked on Docker, but skipping ahead would abandon the phase gate rather than work around it |
| D015 | Utterance-boundary STT requirement is specified in §59.17, not left to the adapter | It is a named defect in the current voice behaviour ("every pause retranscribes the previous sentence as a new utterance"). A requirement stated at implementation time reads as a nicety; stated now it is testable |
| D016 | Health-information boundaries are enforced in the agent/tool layer, not by prompt | A prompt is a preference that a capable model can be talked out of. A refusal at the layer holds regardless of what was asked, which is the only acceptable standard for this category |

#### T021 delivery notes (in progress — not yet committed)

Authentication is the last untouched piece of §30, and the gap table above says
the *design* was already in place with no caller. This closes four of those rows.

**Delivered so far**

| File | Contents |
|---|---|
| `app/security/passwords.py` | Argon2id only; verify, `needs_rehash`, dummy-verify equaliser |
| `app/security/tokens.py` | Opaque 256-bit tokens, SHA-256 digests, bearer extraction, prefixes |
| `app/security/audit.py` | `AuditLogger` with `allowed`/`denied`/`confirm_required` |
| `app/security/authentication.py` | login, token auth, refresh+rotation, logout, API keys, device auth, superuser guard |
| `app/api/dependencies.py` | container access, per-request session, authenticator, `require_principal`, `require_superuser`, correlation id |
| `app/api/routes/auth.py` | `POST /auth/login`, `/refresh`, `/logout`, `GET /auth/me`, `POST /auth/password` |
| `app/main.py` | `RequestContextMiddleware` — correlation id + one access log line per request |

**Decisions worth recording**

- **Opaque tokens, not JWT.** The schema already models sessions with
  `token_hash`, `refresh_token_hash`, a rotation chain and revocation, which is a
  revocable-session design. A JWT would have to be reconciled against those
  rows to be revocable at all, at which point it has bought nothing. `PyJWT`
  stays unused. Tokens are 256 bits of `secrets` output; digests are SHA-256 so
  the indexed `token_hash` lookup works.
- **SHA-256 for tokens and API keys, Argon2id only for passwords.** An API key is
  256 bits of random, so there is no dictionary to search and a slow KDF would
  only add latency while making the indexed `api_key_hash` unusable. Argon2 is
  reserved for the one credential that is low-entropy and human-chosen.
- **One transaction per request.** `get_db_session` opens a `session_scope` for
  the whole request and the repositories and audit logger are built from that one
  session. Repositories never commit (§15/T013), so a login that creates a
  session row and an audit row lands together or not at all — and a route cannot
  commit while its audit entry rolls back.
- **`get_container` returns a `Protocol`, not `object`.** `object` plus
  `type: ignore` on every attribute access was hiding the wiring. The protocol is
  structurally checked against the real `Container` at type-check time, so
  changing the container shape now fails `mypy` instead of failing on the first
  request that touches the changed member.
- **The container is accessed via a protocol, not imported**, so the dependency
  module has no import-time dependency on the wiring graph and tests can supply
  a stub.
- **A refusal that must change state uses its own transaction.** Denials end in a
  raise, and the raise rolls the request transaction back. Audit rows therefore
  go through `DurableAuditSink`, and reuse-detection's family revocation through
  `DurableFamilyRevoker`. The rule is not "audit durably" but "anything whose
  loss would leave a security decision unenforced writes durably" — an audit row
  that vanishes is a blind spot, but a revoked session that comes back is a
  compromise.

**Defects the tests caught in this task's own code**

These are recorded because each was a real bug, not a test artefact, and each is
the kind that unit tests written alongside the code tend to assume away.

| Defect | Why it mattered |
|---|---|
| `AuditLogger` caught only `DatabaseError` | Its contract says it never raises unless `strict`. A failure inside the append itself escaped and turned an audit write into an outage. Caught by a test using a non-`DatabaseError` failure |
| `dummy_verify` cached one hash globally | Verified against whatever Argon2 parameters were configured *now*, so raising the cost at runtime reopened the timing gap it exists to close. Now keyed by the parameter tuple |
| Refresh reuse detection was unreachable, and had a false positive | The revoked check was folded into `is_refreshable`, so a replayed token looked like an ordinary dead token — the exact case that must not be silent. The replacement test on `rotated_from` was worse: it is set on the *new* session, so it would have flagged the **second legitimate refresh** as reuse and revoked the family. Reuse now rests on the structural fact that `SessionRepository.get_by_refresh_token_hash`'s docstring already specifies: a consumed token resolves to an already-revoked session |
| `session.revoke()` ignored the injected clock | Three call sites omitted `now=`, so revocation timestamps came from `datetime.now()` while everything else used the injected clock. A clock that is honoured everywhere except one path is worse than none, because tests cannot observe it |
| `PermissionDeniedError(msg, details=...)` raised `TypeError` | The class builds its own `details`, so passing another one is a duplicate keyword. Found by the superuser-guard tests |
| **Reuse detection revoked the session family in the transaction it then rolled back** | The one path where a *refusal* must still change durable state. It wrote the denial, revoked the family, then raised `AuthError` — and `session_scope` rolls back that raise, so the revocation was discarded while the audit row survived. Net effect: an alert with no effect, and the attacker kept a usable rotated-out session. Fixed with a `DurableFamilyRevoker` that revokes on its own connection (`RevokeFamily` protocol), the same treatment the audit denial already had |
| **Test isolation did not cover import-time settings reads** | `memories.embedding`'s column type is chosen while the model module is *imported*, i.e. during collection, which happens before any `autouse` fixture runs. The developer's real `.env` therefore decided the schema while every test body observed default settings. Green on a machine with no `.env`, wrong on a machine with one. `ULTRON_ENV_FILE` is now installed at conftest *import* time, which is early enough for collection |

**Closed on T021**

- Route-level tests (`/auth/*` over `TestClient`, correlation-id echo and
  sanitisation, anonymous-mode switch): **35 tests, passing.**
- The full gate: **ruff clean, mypy clean (84 source files), 912 tests passing.**
- Verified against a **live PostgreSQL 17** instance with
  `ENABLE_PGVECTOR=false` (17 tables migrated, `memories.embedding` is `jsonb`):
  login, token authentication, refresh rotation and replay refusal all behave,
  and the audit trail carries `auth.login` / `auth.token_refresh` /
  `auth.token_reuse_detected`. This is a scratch schema for tests and migrations;
  ULTRON is never run on this machine (§61).
- Committed and pushed.

**Still open on T021**

- ~~`security.require_auth` is not consulted by `require_principal`~~ —
  **withdrawn, it was wrong.** There is no `security.require_auth`. The only
  `require_auth` in settings is `devices.esp32_require_auth`
  (`ESP32_REQUIRE_AUTH`, `settings.py:459`), a device-transport setting that has
  nothing to do with HTTP auth. `require_principal` branches on
  `security.allow_anonymous` (default `False`), which is the correct and only
  guard, so there was no bypass to close. Adding a `security.require_auth` now
  would be inventing a second switch for a decision one flag already makes.
- The genuine related gap, tracked under T022: `ALLOW_ANONYMOUS` only produces a
    `startup_warnings` entry, and `.env.example` says it "must be false in
    production" without anything enforcing it. **Closed by T022** — not by
    failing the boot, but by making the warning visible: `/health` now reports
    `status: degraded` and lists the warnings, so a misconfigured production
    deploy is diagnosable instead of silently healthy. That is what the settings
    docstring always intended by choosing warnings over boot failures.
- Logout's docstring claims a malformed `Authorization` header is ignored, but
  only a *missing* header is; a non-empty malformed value still raises from
  `tokens.extract_bearer`. Low severity, but the doc and the behaviour should
  agree.

---

## Managed identity and offline continuity (spec §60, added after T021)

Raised after T021 to get ULTRON off self-hosted credentials and to keep the
product useful when the server is off. Recorded as specified in `server_arc.md`
§60, including the part that was **rejected** -- a Firestore copy of the whole
database -- so the reasoning survives and the idea is not re-proposed later.
Nothing here is Phase 1 work and no dependency or package follows from it.

T300+ are numbered here to avoid colliding with the original plan and T220+.

### Managed identity (spec §60.2) - FUTURE

- [ ] **T300** Verify Firebase Auth pricing and quota against the free-only constraint (§59.7) - confirm the free tier is sufficient for the intended scale and identify the exact pay-as-you-go boundary. **Gates T301**: adopting an identity provider whose cost is unknown is adopting a bill.
- [ ] **T301** `app/auth/providers/firebase.py` - verify a Firebase ID token against Google's cached JWKS: signature, `iss`, `aud`, `exp`, and clock skew. **Exchange only**: the ID token proves identity, it never becomes a ULTRON session, because an RS256 token cannot be revoked before it expires. Dependency: T300.
- [ ] **T302** `POST /auth/firebase/exchange` - on a verified ID token, resolve the local `users` row and mint ULTRON's own opaque access + refresh pair through the existing §30 path. Reuses `Authenticator`; adds no new session type. Dependency: T301.
- [ ] **T303** Account linking rules - one human arriving by password and by Google resolves to one `users` row. Specify `uid` uniqueness, whether an unverified email may link, and what happens to the local password hash on merge. **Must be written before T302 ships**; a guessed merge rule is how an account takeover gets in.
- [ ] **T304** Firebase identity events are audited like any other - `auth.login` with the upstream UID recorded as the subject; login, refresh, logout and denial all produce their §31 rows. Dependency: T302.
- [ ] **T305** Identity-provider outage behaviour - a JWKS refresh failure, an unreachable provider, and a stale cache are each given defined behaviour. A login path that fails when Google is down is a new availability dependency in the one place the product cannot afford one. Dependency: T301.

### Offline continuity (spec §60.4) - FUTURE

- [ ] **T306** Client-owned local cache - recent conversations, messages and project metadata in SQLite or IndexedDB on the client. **A cache, not a mirror**: overwritten rather than merged, so there is nothing to reconcile. Lives on the client, so it costs nothing on the 4 GB server and works when the server is down.
- [ ] **T307** Bounded one-way outbox - client-to-server only, so there is no merge problem. Queue bounded by count and age, entries expire rather than grow, and every entry carries a client-generated idempotency key so a replay after an ambiguous failure cannot double-apply. **Optional**: skipped entirely if cross-device hand-off of queued work is not wanted, at no cost to the offline goal.
- [ ] **T308** Offline is read-mostly and honest - cached data shows its age; a queued command is visibly pending, never optimistically reported as done (§59.25). Operations needing server authority - permissions, models, devices, anything destructive - queue or refuse offline rather than pretending to have succeeded.
- [ ] **T309** Firestore security rules, reviewed as carefully as §31 - owner-scoped, deny-by-default, with the rule set treated as the authorisation layer for everything a client can write. A permissive rule set leaks the dataset regardless of what the server enforces. Dependency: T307.

### Rejected - retained so it is not re-proposed

- [x] **SKIP - Firestore as a full copy of the PostgreSQL database** (§60.3). Rejected because a mirror of the authoritative store is a permanent second source of truth with no specified reconciliation, and its failure modes are worse than having no mirror: a **revoked** session or consumed refresh token stays usable in the copy until it catches up, so the audit log shows the denial while the credential keeps working; `tasks`/`task_steps` replayed from a stale copy can double-execute; conflict resolution on `memories`/`messages` is undefined; client-writable data makes security rules the real authorisation layer; and it doubles write cost at the exact tier that needs the free tier. PostgreSQL stays the single source of truth (§23).

### Runtime platform matrix (spec §62) - recorded, client work mostly FUTURE

The platform split is now explicit: **the server runs on Ubuntu Server OS only;
the application is installed and operated from Windows as the primary desktop
client, and is also used on mobile.** Neither client requires Windows-specific
server operations - no WSL, no Docker, no Python, no clone (§62.5).

- [ ] **T311** Windows installer and first-run experience - ordinary application install, no admin rights, no WSL, no Docker, no Python, no repository clone (§62.5). Verify on a clean Windows machine, not a machine that already has the dev toolchain
  *Numbering note (D019): `server_arc.md` §29 and §63.5 cite "the T311 decision" as the authority for the receive-only SSE stream. That decision is `todo.md` §G1, which this file tracks as **T320**. This T311 is the Windows installer. The spec references are left as written and the mismatch is recorded rather than renumbered (D013/D019).*
- [ ] **T312** Mobile client target - same API and same event stream as desktop (§27, §43); anything unsupported is reported unavailable per §33, never hidden
- [ ] **T313** Pin the deployment target to Ubuntu Server OS in the deployment docs (§37) and publish the platform matrix (server / desktop / mobile) so it is not re-derived from this file
- [ ] **T314** Client-to-server connectivity over a network - TLS, token refresh from the client, reconnect after sleep/network loss, and a clear "server unreachable" state rather than a spinner (§60.4 offline continuity assumes this exists)

### Client architecture (spec §63) - DECIDED: Next.js PWA on Vercel

Mobile client and ESP32 control panel are **one Next.js PWA**, deployed to
**Vercel** from GitHub. Installable, OS-independent, no app store. A native
React app is **deferred**, not rejected - an additional client if the PWA ever
genuinely cannot do something.

The PWA takes its API calls **and its live event stream directly from the Ubuntu
server**, not through Vercel. Vercel hosts the app shell only (§63.5).

- [x] **T310** ~~Choose the client technology~~ - **DECIDED** (§63.1): Next.js PWA on Vercel. Superseded by T315-T320 below
- [ ] **T315** `clients/web/` - Next.js PWA scaffold: installable manifest, service worker, HTTPS, app-shell caching. No ULTRON secrets in the client build. Vercel project wired to the GitHub repo so a merge deploys
- [ ] **T316** Capability matrix on the client - server-driven, not hardcoded. Report the §63.3 revocations (Playwright, app launch, active window, keyboard/mouse, arbitrary screenshots, filesystem, shell, Web Serial/USB) as unavailable **with a reason**, greying out the UI. Never silently hide, never fail confusingly (§33, §15)
- [ ] **T317** Separate ESP32 control-panel mode - own route/section in the same PWA. Read state, send commands, view telemetry, configure settings. **No firmware flashing.** Reaches the device through the server's existing JSON-over-WebSocket transport (T162); the device dials out, so no inbound ports and no LAN discovery are needed
- [ ] **T318** HTTPS on the ULTRON server - a real domain and certificate behind a reverse proxy. **Blocking for every mobile client**: a browser refuses to let an HTTPS page call an `http://` server, so without this the PWA cannot talk to the server at all (§63.5)
- [ ] **T319** CORS policy on the API - allow the production client origin, allow `Authorization` and `Content-Type`, deny everything else, and decide credentials deliberately. Do not open the API to arbitrary origins
- [ ] **T320** Client reconnect and resync - the stream comes from the Ubuntu server as receive-only **SSE** (T023), not `/ws`. The client must reconnect with backoff, re-subscribe, and re-fetch state after any disconnect. Note that `EventSource` reconnects on its own but cannot send an `Authorization` header, so if T023 lands on the `fetch()`-stream shape this reconnect logic is hand-written instead. Never treat a dropped stream as an idle system
- [ ] **T321** ESP32 Wi-Fi provisioning as a **separate local flow**, explicitly out of the PWA's scope - an unprovisioned device cannot be reached from Vercel, and an HTTPS page cannot join a device SoftAP. Provision over the device access point or USB, then the control panel works (§63.4)
- [ ] **T322** PWA offline behaviour per §60.4 - local cache for recent conversations, bounded one-way outbox, cached data shown with its age, queued commands visibly pending and never reported as completed

---

## Distributed node architecture (spec §64, added after T322)

`server_arc.md` §64 is appended as an addendum after §63, following the D012/D013 precedent: existing sections, task IDs, phases and decision numbers are untouched, and nothing here is marked complete on the strength of a specification. §64.19 reconciles every apparent conflict with §1-§63; §64.21 maps the work onto the IDs below.

### Requested → existing → verdict

| Requested | Already covered by | Verdict |
|---|---|---|
| Core, Windows node, Ubuntu server, ESP32, mobile as one architecture | §5, §13, §26, §42, §62.1 (as separate pieces) | **Extended** - unified in §64.3-§64.9 |
| Capability model | §59.6 tool fields, §63.3 revocation table | **Extended** - §64.10 adds node capabilities; T331 |
| Node-targeted tool routing | §16 pipeline, T036 executor (single machine assumed) | **Extended** - a target-selection stage, §64.11; T334 |
| Permissions | §15 LEVEL 0-5, T033 | **Unchanged scale**, scoped to (principal, node, tool, operation), §64.12; T353 |
| Availability / offline | §26 `last_seen` for devices only | **New** for every node kind, §64.14; T333, T336 |
| Events across nodes | §19 event bus, §27 orb events | **Additive** - §64.15 names; §19 not rewritten; T335 |
| Windows-local execution | T140-T148, T261/T262, §59.15 Path B | **Extended** - Path A (local runtime) added alongside Path B; T340-T347 |
| Server as a node | §62.2 (runs there, but is not registered) | **New** - advertised through the same registry; T348 |
| Mobile directing node work | T315-T322, T316 capability matrix | **Extended** - targets authorised capabilities, holds none; T350-T351 |
| ESP32 showing ULTRON states | T160-T167, T162 transport | **Extended** - §64.9/§64.15 states; T352 |

### Architecture amendment (spec §64) - spec + server groundwork

- [x] **T330** §64 written into `server_arc.md` - **spec-only, no code.** Core/client/node distinction (§64.3-§64.4), node inventory (§64.5), Windows/server/mobile/ESP32 nodes (§64.6-§64.9), capability model (§64.10), node-targeted execution (§64.11), permission scope (§64.12), identity (§64.13), availability (§64.14), events (§64.15), conflicts reconciled (§64.19), roadmap (§64.21)
- [ ] **T331** Capability vocabulary in `clients/protocol/` and the tool registry - capability IDs exactly as listed in §64.10, a `targeted` flag extending the §59.6 tool fields, and one shared JSON schema for `/nodes` payloads, so server, Windows node and tests read the same definition
- [ ] **T332** Node registry - **one table unifying T141 (computer-node gateway) and T161 (device registry)**: `node_id`, `name`, `kind` (`windows`/`server`/`esp32`), `capabilities`, `status`, `last_seen`, `protocol_version`. Endpoints `GET /nodes`, `GET /nodes/{id}`. Existing ESP32 device rows become `kind=esp32` node rows (D021) - no third registry
- [ ] **T333** Heartbeat and availability - every node heartbeats on a fixed interval; server expires by TTL into `online` / `stale` / `offline` (§64.14); transitions emit `NODE_ONLINE` / `NODE_OFFLINE` (+ per-kind forms)
- [ ] **T334** Node-targeted tool execution - a tool call carries a `node` target; the router dispatches over the existing transport after the unchanged §16 stages (schema validation, permission check, policy check), and the result returns through verification and events. Tools with no target run in the cloud server as today
- [ ] **T335** Cross-node event protocol - add `NODE_*`, `LISTENING`/`THINKING`/`SPEAKING`, `ORB_SHOW`/`ORB_HIDE`, `AGENT_STOPPED` alongside §19's existing set; one event, one spelling (the §19 `DEVICE_DISCONNECTED` vs the §58 checklist `DEVICE_OFFLINE` mismatch is named in §64.9, not silently renamed)
- [ ] **T336** Availability reporting to clients - expose per-node capability and state (`GET /capabilities` or equivalent) so clients grey out unavailable actions **with a reason** (§33 honesty rule); T316 consumes this instead of shipping its own list

### Windows node (spec §64.6)

- [ ] **T340** `clients/desktop-node/` Electron shell - main / preload / renderer split, no secrets and no tool execution in the renderer, lightweight per §59.13's rule. Dual role recorded as D019: client UI (§59.14) plus node runtime (§64.6.4)
- [ ] **T341** Windows local runtime - authenticated listener accepting node-targeted tool calls from the server and executing them on Windows (**Path A**). §59.15's server-side bridge (**Path B**) is preserved; both use the same tool contract and permissions (§64.6.3)
- [ ] **T342** Filesystem and Git tools on the node - workspace-scoped per §59.4; no arbitrary path traversal; server-side sandbox rules apply unchanged
- [ ] **T343** PowerShell tool on the node - structured commands (never blind strings), timeouts, bounded output capture, required §15 level checked on the node before execution
- [ ] **T344** Application and browser control on the node - launch/activate applications and Windows-local browser automation from §64.6.2's list, each as its own declared capability
- [ ] **T345** Process and screenshot tools - bounded process list/termination and screen capture, both permission-checked; screenshots follow §59.15's consent rules
- [ ] **T346** Registration and heartbeat from the Windows node - registers against T332 with per-node credentials (T353), heartbeats per T333, reconnects with backoff and re-registers after sleep or network loss
- [ ] **T347** Windows node test suite - unit and contract tests against the T146 test double; **no live Windows node on the development laptop** (§61 unchanged, D017)

### Server node (spec §64.7)

- [ ] **T348** Advertise the server's own capabilities as `kind=server` - browser, filesystem, Git, terminal, system tools registered through T332 so routing is uniform: mobile and desktop target server work exactly the way they target Windows work

### Mobile client (spec §64.8)

- [ ] **T350** Mobile chat and agent UI in the PWA - consumes the T320 stream, shows node-targeted activity (§64.16), and directs **authorised** server/Windows capabilities while holding none itself (D022)
- [ ] **T351** Mobile server-control actions - launch/stop and file/Git operations on the server node through T334, with availability-greying fed by T336

### ESP32 node (spec §64.9)

- [ ] **T352** Real-time ULTRON states on the ESP32 - `LISTENING`/`THINKING`/`SPEAKING` and node presence pushed over the existing T162 JSON WebSocket, driven by §64.15 events; device firmware stays a T160-T167 concern

### Security (spec §64.12-§64.13)

- [ ] **T353** Node identity and capability authorisation - per-node credentials issued at registration, server-side grants evaluated as (principal, node, tool, operation), and mandatory local re-validation on the executing node; the node enforces, it never decides policy (§64.12)

### Testing (spec §64.21)

- [ ] **T354** Cross-node test matrix - for each node kind: happy path, node offline mid-call, unknown capability, revoked permission, stale heartbeat, result delivery to every subscribed client. Extends T290's list rather than replacing it

### Extension decisions

| # | Decision | Rationale |
|---|---|---|
| D017 | §64 is appended to `server_arc.md` after §63; new tasks are T330+, appended as a separate block | Same precedent as D012/D013. Existing IDs are referenced from the phase status log, `todo.md` and the spec itself; renumbering would break those references for no benefit |
| D018 | Windows operations execute **on Windows** (Path A) - the server is not in the execution path for Windows-local tools, only in the authorisation path | Keeps §61 true (the server never needs Windows), keeps latency out of the loop, and keeps the permission decision where it is auditable: server decides, node re-validates and executes |
| D019 | The Electron app is **dual role**: client UI (§59.14) plus Windows node runtime (§64.6.4) - and records that `server_arc.md` §29/§63.5's "T311 decision" actually refers to `todo.md` §G1 (T320 here) | The single-process pairing is the point of Electron; the numbering mismatch is recorded rather than renumbered so both references stay resolvable |
| D020 | §15's LEVEL 0-5 stays the **only** permission scale; nodes add a scope, not a scale | A second scale would create grants that map to each other ambiguously; (principal, node, tool, operation) makes the same level precise per node (§64.12) |
| D021 | **One node registry** - T141 and T161 are the same table (T332), not a third registry beside them | ESP32 devices already model `capabilities`, `status` and `last_seen`; a parallel node registry would duplicate rows, double the heartbeat code, and guarantee drift |
| D022 | The mobile client is granted **no Windows-local capability of its own** | §63.3's revocations describe the phone's sandbox and stay revoked (§63.3, unchanged); mobile may *direct* authorised node capabilities (§64.18.1) but never *holds* them - a second copy of a Windows tool on a phone is a security bug, not a feature |

---

## Telephony (spec §65, added after the §64 block)

Phone calls as another ULTRON interface: a provider-abstracted telephony
service under `server/app/voice/telephony/`, reusing the existing voice session
manager, STT/TTS ABCs, agent runtime, event bus, permissions and scheduler.
OpenClaw is architectural inspiration only - not a dependency (D025). One
provider first; mocks only in tests. §65.1 records the audit result (what was
found and what is reused).

- [x] **T360** Audit the existing voice architecture before any change - **done during §65 authorship, no code written**: voice manager sessions (T153), `STTEngine`/`TTSEngine` ABCs (T150/T151), wake (T152), streaming/barge-in (T154), §59.16-§59.20, event bus (T030/T031), API conventions (§29), auth (T021/T022), permissions (T033), scheduler (T170-T175), memory (T120-T129), nodes (T330-T354). Findings tabulated in §65.1
- [ ] **T361** Telephony service boundary - `app/voice/telephony/{service,provider,sessions,webhook}.py` + `providers/`; `service.py` is the only module that may import `providers/`; the service attaches calls to `voice/manager.py` sessions instead of forking them (D026)
- [ ] **T362** `TelephonyProvider` ABC + configuration - `initiate_call`, `hangup`, `get_status`, `open_media_stream`, `parse_webhook`, `health`; `TELEPHONY_*` keys in `config/settings.py` and `.env.example`; fail-fast validation at startup including **rejecting a non-public `TELEPHONY_WEBHOOK_BASE_URL`** (D011 philosophy); `TELEPHONY_PROVIDER=off` disables the feature by default
- [ ] **T363** Call session model - `call_sessions` table + Alembic migration (call_id, provider, direction, destination, status, created/connected/ended, session_id link, task_id link, agent_id, metadata); guarded monotonic state machine CREATING→RINGING→CONNECTED→{LISTENING,THINKING,SPEAKING}→ENDING→ENDED, any→FAILED; statuses mirror §59.13 orb states
- [ ] **T364** Outbound call API - `POST /voice/calls`, `GET /voice/calls/{id}`, `POST /voice/calls/{id}/end` (§29 plural convention); existing auth + permission dependencies; returns ids/status only; no provider secrets in any response
- [ ] **T365** First provider adapter (`providers/twilio.py`) - implements the ABC only; vendor types never cross into service/sessions/agents; Telnyx/Plivo remain future adapters that must not touch callers
- [ ] **T366** Webhook intake + verification - `POST /voice/webhooks/{provider}`: provider signature check, timestamp/replay window, forged-callback rejection with audit row, provider event → call state transition mapping; unauthenticated route but never unverified
- [ ] **T367** Call lifecycle and termination - transition guards, idempotent end, dial/answer timeouts, mid-call disconnect → ENDED/FAILED with reason, orphaned sessions after restart recovered to ENDED
- [ ] **T368** Realtime audio bridge - provider media stream (WebSocket) ↔ ULTRON audio forwarding on the server node; lightweight, no local models; barge-in/partial transcripts follow §59.19/T154
- [ ] **T369** STT/TTS/realtime providers for calls - cloud adapters through the existing `STTEngine`/`TTSEngine` ABCs (T150/T151) plus a swappable realtime-provider abstraction (OpenAI realtime / Gemini Live / others) selected by config; local engines optional (§59.20), never required (D027)
- [ ] **T370** Voice session → agent runtime - call audio resolves to a normal voice session (T153) → orchestrator (T049) → existing router/agents/tools; **no Telephony Agent**; permissions and confirmations unchanged, call session recorded on the decision (§64.12 scope)
- [ ] **T371** `VOICE_CALL_*` events - CREATED/RINGING/CONNECTED/LISTENING/THINKING/SPEAKING/ENDED/FAILED on the existing bus (T030/T031), additive names only (§65.15), fanned out over SSE (T023/T320) to Electron, mobile and ESP32
- [ ] **T372** Call finalization + memory - on ENDED: metadata always; transcript/summary/actions per `TELEPHONY_TRANSCRIPT_POLICY` (`store_transcript`/`summary_only`/`none`); episode via T126/T128; no audio stored by default; no second memory store
- [ ] **T373** Client integrations - mobile PWA call button + active-call card (T315+ block), Electron state (T261), ESP32 `CALLING` display over T162; all trigger/observe through Core, never carry the call (D024)
- [ ] **T374** Telephony security hardening - authenticated + permissioned initiation, rate limiting on `/voice/calls` and webhook intake, numbers masked in logs/events/exceptions, secrets config-only, audit rows for start/end/permission/tool-under-call
- [ ] **T375** Test suite with `MockTelephonyProvider` - ABC conformance, config validation (local URL rejected), creation, every state transition, webhook accept/reject/replay, provider failure, hangup, permission rejection, event emission, session cleanup. **No real calls in automated tests, ever; CI has no credentials**
- [ ] **T376** Deployment - public HTTPS webhook ingress documented (reuses T318 + §63.5 reverse proxy; no hard-coded tunnel vendor), production secrets outside the repo, telephony readiness/health check, provider metrics in the existing Prometheus setup (T180/T181)
- [ ] **T377** Inbound call architecture - **FUTURE**: webhook `incoming_call` event recorded now; full flow (validate → allow-listed numbers → create inbound session → agent) specified in §65.14; deny-by-default, no auto-answer to arbitrary callers

### Telephony decisions

| # | Decision | Rationale |
|---|---|---|
| D023 | Telephony is a service under `server/app/voice/telephony/` with a provider ABC; **one provider (Twilio) implemented first**, others later | Vendor isolation is the point of the abstraction; three adapters at once would add complexity with nothing to prove them against |
| D024 | Calls are placed and held **server-side only**, never routed through the Windows node | The always-on Ubuntu server must call with Electron closed (§62.2, §64.7, §65.19); routing call media through a laptop would make the feature depend on a client being awake |
| D025 | OpenClaw is **architectural reference only** - no install, import, gateway, service, or copied source; absent from the dependency graph | The requirement is a clean ULTRON-native shape (plugin + session + gateway + agent loop); any runtime coupling would import an unowned system into a security-sensitive path |
| D026 | A call **is a ULTRON voice session** - telephony reuses `voice/manager.py`, the STT/TTS ABCs, events, tasks, memory; no parallel voice or session system | Two voice stacks would drift and double every future fix; §25's provider-independent rule is exactly what lets a second audio source attach |
| D027 | **Cloud-first**: no local STT/TTS/realtime model is required to make calls work; realtime providers swap behind config; local stays optional | 4 GB server (§59.25); requiring Whisper/Piper on a call path would violate the resource model and make telephony undevelopable on current hardware |

---

## Capability integration (spec §66, added after the telephony block)

Twenty requested capabilities mapped onto the architecture that exists
(§66.1): five already delivered by §62-§65/§64, twelve extensions of a named
subsystem, one formalized lifecycle, two genuinely new contracts (rollback,
agent-to-agent). No V2 of anything (D029). New IDs are T380+; every other capability points at its existing tasks.

- [ ] **T380** Context engine - extend T046 `app/core/context.py` to `collect/resolve/rank/compress/build_prompt_context/clear_expired_context` over conversation, task, agent, node, project, recent actions/tool calls, device state, preferences, memory, knowledge, background tasks, session, voice/call session, effective permissions; permission-filtered before ranking; one interface for agents, planner, voice, tools, UI (§66.3)
- [ ] **T381** Execution lifecycle - implement §66.4 on T048 (planner), T049 (orchestrator), T050 (executor): goal→understand→context→plan→permission→agent→node→tool→observe→verify with retry/replan/escalate, confirmation, cancellation, pause/resume as graph operations on T040/T041 - not a separate planner runtime
- [ ] **T382** Structured agent-to-agent messages - `AgentMessage` (task_id, sender, receiver, objective, input, result, evidence, confidence, errors, requested_next_action) passed through the orchestrator; chain executions (planner→research→coding→testing→review) stay under permissions and history; no free-form agent chat loops (§66.20)
- [ ] **T383** Model router hardening - provider health tracking + temporary circuit breaking on the §59.9 fallback chains (T223/T224); selection inputs extended per §66.6 (task/agent type, capability, latency, cost, context size, availability, privacy, local/cloud); still one router (§20), no provider hard-coded
- [ ] **T384** Event catalog additions - `CONTEXT_UPDATED`, `PLAN_CREATED/PLAN_UPDATED`, `NODE_CAPABILITIES_UPDATED` (`NODE_ONLINE/OFFLINE` already exist - T335), `VOICE_*`, `VOICE_CALL_*` (T371), `MODEL_SELECTED/MODEL_FAILED`, `PERMISSION_REQUESTED/GRANTED/DENIED`, `SYSTEM_ERROR` on the existing bus; existing names keep their spellings (with §64.15/T335); no broker (§66.8)
- [ ] **T385** Capability-aware node selection - planner/executor resolve target node from the T332 registry + T336 availability at run time (which node can do this, online?, permitted?, better alternative?, offline fallback?) - no hard-coded node assumptions (§66.9)
- [ ] **T386** Computer-use verification loop - act → screenshot again → confirm expected state → retry/escalate, on the Windows node tool set (T143-T145, T344/T345); closes the §17 gap for "click/type/window" actions (§66.10)
- [ ] **T387** Background agent lifecycle - long-running agents on T170-T172 scheduler + T044 manager: pause/resume/cancel/timeout/resource limits/logs/results/notifications, bounded by max-iteration and §66.4 plan shape; runaway detected via events and stopped by T391 (§66.11)
- [ ] **T388** Memory type expansion - preference/task/device/agent memory as **scopes on T120-T126** (not new stores); ranking, dedupe, importance, decay, source tracking, confidence; extraction stays intentional, conversation messages not memorized by default (§66.12)
- [ ] **T389** Knowledge ingestion - source→ingest→parse→chunk→embed→index into the **existing** pgvector store (`memories.embedding`, `ENABLE_PGVECTOR`, D030); permission-scoped retrieval and reranking feeding the context engine; extends T124/T127 (§66.13)
- [ ] **T390** Tool registry hardening - add `output_schema`, `node_requirements`, `risk_level`, `availability`, `version`, `reversibility` to the §59.6 fields (T230-T232); plugins stay isolated behind the tool interface; telephony is just another registry entry (§66.14)
- [ ] **T391** Self-diagnostics and bounded recovery - health matrix (server, agents, nodes, SSE/WS, API, DB, AI providers, tool providers, voice, telephony, ESP32, jobs) on T180-T183; recovery menu: reconnect, retry+backoff, provider switch (§59.9), worker restart, interrupted-task resume - each emitting an event + audit row, none granting new capabilities (§66.15)
- [ ] **T392** Queryable action history - view joining user request → plan → agents → tools → nodes → permission decisions → results → errors → timestamps → model/provider → verification, correlated by request id over existing `tool_executions`/audit/T041 events; a view, not a new logger (§66.16)
- [ ] **T393** Reversibility and rollback - tools declare `reversible/partially_reversible/irreversible` (T390 field); snapshot-before-execute for file and Git operations; irreversible → stronger confirmation path; **no universal undo claimed** (§66.17)
- [ ] **T394** Cross-capability integration test - one goal→plan→execute→verify journey spanning two nodes with permission checks, event assertions, context injection and history rows; extends T290/T354 lists rather than replacing them (§66.24)

### Capability-integration decisions

| # | Decision | Rationale |
|---|---|---|
| D028 | §66 appends after §65; new tasks are T380+, appended as a separate block | D012/D013/D017 precedent - existing IDs, phases and status logs stay intact |
| D029 | **No V2 systems**: each of the twenty features extends the subsystem named in §66.1, and §66.1 is the authority when a later reader wonders whether something is new or extended | The request's central constraint; a duplicate subsystem would guarantee drift between two memories/two buses/two runtimes, which is how a single system becomes a collection of unrelated applications |
| D030 | Knowledge/RAG uses the **existing** PostgreSQL/pgvector store (`memories.embedding`, `ENABLE_PGVECTOR`) - no second vector database | Retrieval already exists (T124); only ingestion is missing; a second store would duplicate embeddings, permissions and cost for no capability gain |
| D031 | The requested PHASE 0-21 ordering is **mapped onto** the existing Phase 1-13 structure plus the T220+/T300+/T330+/T360+/T380+ blocks, not adopted as new phase numbers | tasks.md's phases are referenced by the phase status log, Definition of Done and completed-task history; renumbering would break those references while expressing nothing the dependency notes in §66.24 do not |

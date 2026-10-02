# ULTRON SERVER — MASTER BUILD TASK LOG

Source of truth for the build described in [`server_arc.md`](server_arc.md).

## How to read this file

| Marker | Meaning |
|---|---|
| `[ ]` | pending |
| `[~]` | in progress |
| `[x]` | **done** — implemented, formatted, linted, type-checked, tests pass |
| `[!]` | blocked — reason recorded inline |

Rules (from spec §52, §53, §54, §57):

1. A task is only `[x]` when it has a passing test, or is an honest documented
   stub **with** a test **and** a "Known limitations" entry.
2. Never fake availability. If a subsystem is not implemented, ship the
   interface, a safe stub, documentation, and a test.
3. Before declaring a feature complete run:
   `format → lint → typecheck → unit tests → integration tests → build → health check`.
4. Branch per slice: `feature/*` off `develop`, conventional commits, merge to
   `develop`, `main` reserved for releasable states.
5. This file is updated after **every** task, never batched at the end of a phase.
6. Deviations from `server_arc.md` are recorded in the Decision Log at the bottom.

---

## Environment

| Item | Value |
|---|---|
| Dev OS | Windows 10/11, VS Code, PowerShell |
| Target OS | Ubuntu Server 26.x, headless, Ethernet, optional NVIDIA GPU |
| Python | 3.12+ (installed via winget) |
| Package manager | `uv` |
| Local infra | Docker Desktop → `postgres`, `redis`, `ultron-api` only (8 GB budget) |
| Local models | **none** — cloud providers used for real calls; Ollama adapter unit-tested |
| Prod models | Ollama on the Ubuntu host |
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
- [ ] **T021** `app/api/dependencies.py` — container access, correlation IDs, auth dependency stub
- [ ] **T022** `app/api/routes/health.py` — `/health`, `/ready`, `/metrics`
- [ ] **T023** `app/api/websocket/manager.py` + `/ws` — connection manager, topic subscription, heartbeat
- [ ] **T024** `server/tests/` Phase 1 suite — config, logging, session, health, API smoke, WS connect
- [ ] **T025** `deployment/docker/Dockerfile.dev` + root `docker-compose.yml` dev stack (`postgres`, `redis`, `ultron-api`)
- [ ] **T026** Migrate real PostgreSQL + create schema via Alembic
- [ ] **T027** **PHASE 1 verification** — server starts, PostgreSQL connects, Redis connects, `/health` works, WebSocket works

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

- [ ] **T030** `app/events/types.py` — 33 event types from spec §19 + orb/agent-window events (§27, §28)
- [ ] **T031** `app/events/bus.py` — async pub/sub, wildcards, queue backpressure, optional Redis bridge
- [ ] **T032** `app/events/handlers.py` — built-in subscribers (logger, memory, WS fan-out)
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
- [ ] **T061** `app/models/ollama.py` — configurable host, discovery, health, chat, NDJSON streaming, availability check, timeout, error mapping
- [ ] **T062** `app/models/openai.py` — optional adapter, lazy key check, unavailable when unconfigured
- [ ] **T063** `app/models/gemini.py` — optional adapter
- [ ] **T064** `app/models/anthropic.py` — optional adapter
- [ ] **T065** `app/models/fake.py` — deterministic scripted provider for tests
- [ ] **T066** `app/models/router.py` — `select(capability=, speed=)`, fallback chain, availability, resource awareness, user preference, no hard-coded model names
- [ ] **T067** Model usage recording → `model_usage` table
- [ ] **T068** Wire model-driven tool-calling loop into the agent base
- [ ] **T069** Phase 3 tests — adapter contract tests via `respx`, router selection matrix, streaming, `LOCAL_MODEL_UNAVAILABLE` path
- [ ] **T070** **PHASE 3 verification** — system functions with only Ollama configured

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
- [ ] **T124** `semantic.py` — embeddings + vector retrieval (pgvector)
- [ ] **T125** `project.py` — project-scoped memory
- [ ] **T126** `manager.py` — facade, cross-layer retrieval
- [ ] **T127** Embedding provider abstraction (Ollama embeddings, cloud, honest unavailable path)
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

- [ ] **T150** `app/voice/stt/` — `STTEngine` ABC + faster-whisper adapter + availability probe
- [ ] **T151** `app/voice/tts/` — `TTSEngine` ABC + Piper adapter + availability probe
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

- [ ] **T190** `deployment/docker/Dockerfile` — multi-stage, non-root user, no dev deps
- [ ] **T191** Root `docker-compose.yml` — `ultron-api`, `postgres`, `redis`, `ollama`; optional `prometheus`, `grafana`; explicit `depends_on` + healthchecks
- [ ] **T192** `deployment/systemd/ultron-api.service`, `ultron-worker.service`
- [ ] **T193** Ubuntu deployment docs — clone → prerequisites → `.env` → `docker compose up -d` → migrations → health check
- [ ] **T194** Non-Docker / systemd deployment path
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
| Done | T010 configuration, T011 structured logging, T012 typed errors, T013 async session layer, T014 models, T015 repositories, T016 migrations, T017 Redis wrapper, T018 health checks, T019 DI composition root, T020 FastAPI factory — 19 of 18 |
| `scripts/lint` | Ruff clean over `app`, `tests` and `migrations`; mypy clean over `app` and `tests`, 88 files |
| `scripts/test` | 767 passed, integration and e2e deselected |
| `docs/errors.md` | Catalogue generated from the running code and diffed against it, so it cannot drift; now covers `LockUnavailableError` |
| Next | T020 FastAPI factory + lifespan | 
| Still blocked | T025/T026/T027 need Docker Desktop: no PostgreSQL, no Redis, no real migration yet. The phase gate cannot be claimed. |

#### T017 delivery notes

Scope is the four uses the task line names — cache, locks, pub/sub, transient
state. Spec §24 lists six, and the other three arrive with their consumers rather
than landing here unasked: queues with the event bus (T031), rate limiting with
the security work, and WebSocket coordination with the connection manager (T023).
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


#### T020 delivery notes

The application factory creates a FastAPI instance with:
- The dependency injection container wired into the lifespan events (startup pings DB+Redis, shutdown disposes resources)
- Comprehensive exception handling for all Ultron error types, mapping them to appropriate HTTP status codes:
  * 400 Bad Request: InvalidInputError, ValidationError, SerializationError, SsrfBlockedError
  * 401 Unauthorized: AuthError, InvalidCredentialsError
  * 403 Forbidden: PermissionDeniedError
  * 404 Not Found: NotFoundError, TaskNotFoundError, AgentNotFoundError, etc.
  * 409 Conflict: ConflictError, LockUnavailableError
  * 429 Too Many Requests: RateLimitedError
  * 501 Not Implemented: CapabilityNotImplementedError
  * 503 Service Unavailable: DependencyUnavailableError, OperationTimeoutError, HealthCheckFailedError, LocalModelUnavailableError, ProviderNotConfiguredError, ShuttingDownError
  * 500 Internal Server Error: All other errors (default)
- Middleware: CORS (allowing all origins in development) and TrustedHost (allowing all hosts in development)
- Health endpoints: `/health` (liveness) and `/ready` (readiness placeholder - to be wired by T021)
- Router mounting framework ready for T022–T024 (auth, connection manager, WS routes) and T036–T039 (agents, tasks, memories, conversations)

The application is importable without external services: `from app.main import create_app` works on a bare checkout, enabling testing and Docker/CI usage without running PostgreSQL or Redis.

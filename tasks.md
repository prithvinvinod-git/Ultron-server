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

- [ ] **T001** Install Python 3.12 + `uv` via winget; create `server/.venv`; verify `python -V`, `uv -V`
- [ ] **T002** Create full monorepo tree: `server/app`, `server/tests`, `server/migrations`, `clients/{desktop-node,orb,protocol}`, `deployment/{docker,compose,systemd,scripts}`, `docs`, `scripts`
- [ ] **T003** `server/pyproject.toml`: deps, optional extras (`browser`, `voice`, `iot`, `providers`), ruff / mypy / pytest config
- [ ] **T004** Root hygiene: `.gitignore`, `.editorconfig`, `.dockerignore`, `.gitattributes` (keep)
- [ ] **T005** `.env.example` with every placeholder from spec §34 (no real secrets)
- [ ] **T006** `scripts/{dev,test,lint,start,format}.ps1` + `.sh` counterparts (spec §36), pathlib-based, no hard-coded Windows paths
- [ ] **T007** `README.md` (real content), `LICENSE`, `develop` branch, `.gitkeep` for empty dirs
- [ ] **T008** Verify Phase 0: `uv sync` succeeds, `scripts/lint` clean, `.env.example` complete

**Phase 0 gate:** `uv sync` succeeds · lint clean · tree matches spec §4

---

## Phase 1 — Foundation

- [ ] **T010** `app/config/settings.py` — Pydantic v2 settings, nested sections, env-file resolution, no secrets defaults
- [ ] **T011** `app/observability/logging.py` — JSON structured logs, `request_id`/`task_id`/`agent_id` contextvars, redaction
- [ ] **T012** `app/core/errors.py` — typed exception hierarchy + error codes (incl. `LOCAL_MODEL_UNAVAILABLE`)
- [ ] **T013** `app/database/session.py` — SQLAlchemy 2.0 async engine, session factory, declarative `Base`
- [ ] **T014** `app/database/models/` — 16 tables from spec §23 (`users`, `sessions`, `agents`, `tasks`, `task_steps`, `tool_executions`, `events`, `conversations`, `messages`, `memories`, `projects`, `devices`, `device_events`, `agent_logs`, `model_usage`, `audit_logs`) + `schedules`
- [ ] **T015** `app/database/repositories/` — repository pattern per aggregate
- [ ] **T016** `server/migrations/` — Alembic init + async template, pgvector-aware, first revision
- [ ] **T017** `app/database/redis_client.py` — Redis wrapper (cache, locks, pubsub, transient state only)
- [ ] **T018** `app/observability/health.py` — health checks: PostgreSQL, Redis, Ollama, filesystem, agent runtime, event bus
- [ ] **T019** `app/container.py` — hand-rolled DI composition root
- [ ] **T020** `app/main.py` — FastAPI factory + lifespan, exception handlers, router mounting
- [ ] **T021** `app/api/dependencies.py` — container access, correlation IDs, auth dependency stub
- [ ] **T022** `app/api/routes/health.py` — `/health`, `/ready`, `/metrics`
- [ ] **T023** `app/api/websocket/manager.py` + `/ws` — connection manager, topic subscription, heartbeat
- [ ] **T024** `server/tests/` Phase 1 suite — config, logging, session, health, API smoke, WS connect
- [ ] **T025** `deployment/docker/Dockerfile.dev` + root `docker-compose.yml` dev stack (`postgres`, `redis`, `ultron-api`)
- [ ] **T026** Migrate real PostgreSQL + create schema via Alembic
- [ ] **T027** **PHASE 1 verification** — server starts, PostgreSQL connects, Redis connects, `/health` works, WebSocket works

**Phase 1 gate (spec §51):** server starts successfully

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
| 21 | Logs work | pending |
| 22 | Configuration works | pending |
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
| D005 | Extra packages `app/workspaces/` and `app/notifications/` beyond spec §4 tree | Required by spec §46 and §45; §4's tree is structural, not exhaustive |
| D006 | `clients/protocol/` holds shared node protocol schemas | Single source of truth for the Windows node ↔ server contract (spec §13, §28, §42) |
| D007 | `server/pyproject.toml` per spec §4; tool config lives there | Keeps root free of Python packaging concerns |
| D008 | Two packages added to §4 tree marked as deviations | See D005/D006; recorded so the deviation is intentional and traceable |
| D009 | Integration tests skip cleanly when PostgreSQL/Redis/Docker absent | Test suite must pass on a bare checkout without paid APIs or services |
| D010 | Task DAG hand-rolled instead of `networkx` | Avoids a heavyweight dependency for a small, well-understood algorithm |

---

## Phase status log

Appended after each phase, per spec §51/§58.

<!-- PHASE STATUS BLOCKS BELOW -->

# ULTRON Server

The Python backend for ULTRON — a local-first, agentic AI operating
environment. This package is ULTRON's brain and its execution environment: it
owns models, agents, planning, tool execution, memory, browser automation,
voice, IoT devices, scheduling, permissions, verification, events, persistent
state and observability.

The UI is never the brain, and an LLM is never the operating system.

Full project documentation, architecture and deployment guides live in the
repository root: [`../README.md`](../README.md) and [`../docs/`](../docs).

## Layout

```
app/
  main.py          FastAPI application factory and lifespan
  container.py     Dependency-injection composition root
  api/             REST routes, WebSocket endpoints, dependencies
  core/            orchestrator, planner, router, executor, context,
                   permissions, events, lifecycle
  agents/          base, manager, registry, one package per agent type
  models/          provider interface + Ollama / OpenAI / Gemini / Anthropic
  tools/           base, registry, executor, one package per tool family
  memory/          short-term, long-term, episodic, semantic, project
  tasks/           manager, executor, graph, state
  events/          bus, types, handlers
  voice/           stt, tts, wake, manager
  devices/         manager, esp32, protocol
  scheduler/       manager
  verification/    verifier, checks
  security/        auth, permissions, audit
  database/        session, models, repositories
  observability/   logging, metrics, health
  config/          settings
  workspaces/      project and git workspace management
tests/             unit, integration, e2e, fixtures
migrations/        Alembic
```

## Development

Run these from the repository root.

```bash
./scripts/setup.sh          # create .venv, install dependencies
./scripts/dev.sh            # API with auto-reload
./scripts/lint.sh           # ruff + mypy
./scripts/test.sh           # unit tests
```

Windows equivalents: `setup.ps1`, `dev.ps1`, `lint.ps1`, `test.ps1`.

## Invariants

These are enforced by tests, not just by convention:

1. ULTRON Core is never coupled to a single model provider — everything goes
   through a `ModelProvider` adapter.
2. No LLM reaches the operating system directly. Every tool call traverses
   schema validation → permission check → policy check → execution →
   verification.
3. Agents cannot bypass the permission manager or the event bus.
4. Agents never access the database directly.
5. Nothing is faked. An unavailable subsystem reports `unavailable`; it never
   pretends to succeed.
6. PostgreSQL is the source of truth. Redis holds only temporary state.
7. No circular imports between layers.

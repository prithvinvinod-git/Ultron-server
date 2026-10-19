# Development

ULTRON is developed on Windows and deployed to Ubuntu Server. The same checkout
produces the same behaviour on both: no Windows-only paths, no hard-coded model
names, no secrets in the tree.

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.12+ | The backend runtime. |
| [uv](https://docs.astral.sh/uv/) | latest | Dependency and environment management. |
| PostgreSQL | 15+ | Authoritative store. Native install or Docker. |
| Redis | 7+ | Cache, queues, pub/sub, locks. Native install or Docker. |
| Node.js | 20+ | Only for the command console under `UIPROTO/` / `clients/`. |

Ollama is optional. The server boots with every cloud provider unconfigured.

## First run

```powershell
# Windows
Copy-Item .env.example .env
.\scripts\setup.ps1        # creates server/.venv and installs dependencies
.\scripts\dev.ps1          # API with auto-reload
```

```bash
# Linux
cp .env.example .env
./scripts/setup.sh
./scripts/dev.sh
```

`setup` syncs the core dependency set plus the `dev` extra. Heavy optional
stacks are opt-in:

```bash
ULTRON_SYNC_EXTRAS="browser,providers" ./scripts/setup.sh
```

Fill in `JWT_SECRET`, `ADMIN_PASSWORD`, and the `DATABASE_URL` password before
the first run; see [`configuration.md`](configuration.md).

## Scripts

Every script exists in both a `.ps1` (Windows) and `.sh` (Linux) form and reads
its paths from a shared bootstrap (`_bootstrap.ps1` / `_bootstrap.sh`), so no
script hard-codes an absolute location.

| Script | Purpose |
|---|---|
| `setup` | Create the virtualenv and install dependencies. |
| `dev` | Run the API with auto-reload. |
| `start` | Run the API without reload (what systemd uses). |
| `format` | `ruff format` then `ruff check --fix`. |
| `lint` | `ruff check`, `ruff format --check`, `mypy`. |
| `test` | Unit tests; `-All` adds integration, `-E2E` adds end-to-end. |

```powershell
.\scripts\format.ps1
.\scripts\lint.ps1
.\scripts\test.ps1                 # unit only
.\scripts\test.ps1 -All            # unit + integration
.\scripts\test.ps1 -Path tests\unit\test_health.py
.\scripts\test.ps1 -Coverage
```

```bash
./scripts/format.sh
./scripts/lint.sh
./scripts/test.sh
./scripts/test.sh --all
```

### The static gate

A feature is not complete until the gate passes in this order:

```
format → lint → type check → unit tests → integration tests
```

The suite **must pass on a bare checkout** with no Docker and no paid API keys
(spec §38, §54). Integration tests skip themselves when PostgreSQL or Redis are
unreachable; they do not fail.

> **Known noise (honest note).** Two test modules carry pre-existing violations
> recorded at commit `de2dcd4` (T174): `tests/unit/test_scheduler_phase11.py`
> and `tests/unit/test_scheduler_triggers.py` (7 ruff + 8 mypy errors). They are
> not regressions and are outside the scope of the work that added them. Until
> they are fixed, run the gate on the files you changed rather than repo-wide:
>
> ```powershell
> .\.venv\Scripts\ruff.exe check <changed files>
> .\.venv\Scripts\mypy.exe <changed files>
> ```

## Testing conventions

- Tests live in `server/tests/unit/` and `server/tests/integration/`.
- Prefer `sqlite+aiosqlite://` for in-memory tests. `sqlite://` is rejected by
  `create_engine`; use the async driver.
- Integration tests gate themselves on service availability.
- Set `asyncio_mode = "auto"` (configured in `pyproject.toml`); async tests need
  no decorator.

## Front end

The command console is a Vite + React app under `UIPROTO/`, published into
`server/app/static/`. Its own gates:

```powershell
cd UIPROTO
.\node_modules\.bin\tsc --noEmit
npm run build
```

> The console's `npm run lint` / Prettier setup is currently broken repo-wide.
> Do not run or reformat with it; use `tsc` and the build as the gate instead.

## Branching

| Branch | Purpose |
|---|---|
| `main` | Released, deployable state. |
| `develop` | Integration branch. |
| `feature/*` | One branch per phase or feature (e.g. `feature/unified-clients`). |

Work happens on a `feature/*` branch and merges to `develop`, then `main`. The
current line of work is `feature/unified-clients`.

### Commit convention

One commit per task, and the task id leads the message:

```
T184: deployment/compose prometheus+grafana profile and dashboard JSON
T527: publish PWA assets to server/app/static + spec doc
```

This keeps `git log` aligned with `tasks.md`, so a task's status can always be
traced to the commit that delivered it.

## Where things are

| Path | What |
|---|---|
| `server/app/` | The backend. See [`architecture.md`](architecture.md). |
| `server/tests/` | Unit and integration tests. |
| `UIPROTO/` | The command console source. |
| `clients/` | Desktop (Electron), shared contracts, ESP32 client. |
| `deployment/` | systemd, compose profile, operational scripts. |
| `tasks.md` | The ledger: every task and its verification line. |
| `server_arc.md` | The specification (§-referenced throughout the code). |

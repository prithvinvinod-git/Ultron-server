# Troubleshooting

Start here, then branch into the subsystem. Every check below is a real
command/endpoint, not a guess.

## First checks

1. **Is it up?**

   ```
   deployment/scripts/health-check.sh 127.0.0.1 8000
   ```

   It probes `/health`, `/ready` and `/metrics` and exits non-zero on failure.

2. **`GET /health`** always returns HTTP `200`; read the `status` field:

   | Status | Meaning |
   |---|---|
   | `ok` | Everything is fine. |
   | `degraded` | A non-fatal dependency is down (Redis, an optional model). The service is usable. |
   | `skipped` | An optional check did not run. |
   | `unhealthy` | A fatal check failed. |

   A `degraded` status **never** crashes the process; the body lists a
   `warnings` array naming the failed dependency.

3. **`GET /ready`** returns `200` when ready and `503` when not. PostgreSQL is a
   hard dependency: if Postgres is down the server is not ready. Redis is not.

## Common failures

### The server logs `sqlite://` or an engine error

`create_engine` rejects `sqlite://`. In-memory tests must use
`sqlite+aiosqlite://`. Set `DATABASE_URL` to a real `postgresql+asyncpg://` DSN in
development and production.

### `/ready` returns 503

PostgreSQL is unreachable. Check `DATABASE_URL`, that Postgres is running and
accepting connections, and that migrations have run
(`deployment/scripts/migrate.sh`).

### Status is `degraded`

Redis is unreachable, or an optional model is unavailable. Redis being down only
means a cold cache; the service still works. Verify `REDIS_URL` and that Redis is
running.

### Semantic memory returns nothing

pgvector is optional and off by default. With `ENABLE_PGVECTOR=false` the
`embedding` column is JSONB and semantic search falls back to keyword recall. Set
`EMBEDDING_PROVIDER` and, if you have the extension, `ENABLE_PGVECTOR=true`. See
[`memory.md`](memory.md).

### A tool returns `deny` / `require_confirmation`

That is the permission gate working. The tool's `required_level` exceeds the
principal's `granted_level`, or it is at/above `AGENT_CONFIRMATION_LEVEL`. Raise
`AGENT_DEFAULT_PERMISSION_LEVEL` or pass an explicit confirmation. Every decision
is in the audit log. See [`security.md`](security.md).

### Shell execution never runs

`TERMINAL_ALLOWED_COMMANDS` is empty, which denies all shell execution, or the
command matches `TERMINAL_DENIED_PATTERNS`. Add the command to the allow-list.

### `computer.*` tools are missing

The computer gateway is disabled or absent. Set `COMPUTER_NODES_ENABLED=true` and
register a node. A node with no matching capability is `skipped`, never faked.
See [`computer-nodes.md`](computer-nodes.md).

### A device shows `offline`

Its heartbeat stopped. A device must heartbeat within
`DEVICE_HEARTBEAT_INTERVAL`; after `DEVICE_OFFLINE_AFTER` it is marked `offline`.
Check the device's network path and token (`DEVICE_TOKEN_TTL`). See
[`esp32.md`](esp32.md).

### Voice does not start

Voice is off by default. Set `VOICE_ENABLED=true`, then a real `STT_ENGINE` and
`TTS_ENGINE` (`none` means that half is disabled). If a primary engine fails,
configure `STT_FALLBACK_ENGINE` / `TTS_FALLBACK_ENGINE`. See
[`voice.md`](voice.md).

### Port already in use

Another process holds `API_PORT`. Find and stop it, or change `API_PORT` and
restart.

### Migrations fail

Run migrations with `DATABASE_URL` pointing at the target database:
`deployment/scripts/migrate.sh`. For a fresh database, check the role has create
privileges.

## Static gate fails

The required order is format → lint → type check → unit tests → integration
tests (per `README.md`).

- Server: `.\.venv\Scripts\ruff.exe check <files>` and
  `.\.venv\Scripts\mypy.exe <files>`, then
  `.\.venv\Scripts\pytest.exe <files>`.
- UI: `.\node_modules\.bin\tsc --noEmit` in `UIPROTO`.

**Known noise:** `server/tests/unit/test_scheduler_phase11.py` and
`test_scheduler_triggers.py` carry pre-existing ruff/mypy errors (committed with
T174). The gate applies to changed files; do not "fix" unrelated noise as part of
another task.

`npm run lint` and prettier are broken repo-wide; do not run or reformat with
them.

## Where to look

| Symptom area | Logs / file |
|---|---|
| Startup and subsystems | stdout of the API process; `Container.startup()` order in `server/app/container.py` |
| Background work | in-process (persistence, scheduler, notifications, observability) — there is no separate worker |
| Health | `server/app/observability/health.py`, `/health`, `/ready` |
| Metrics | `/metrics`, Grafana dashboard (`deployment/compose/`) |
| Deployment | `deployment/README.md`, `deployment/systemd/README.md` |

See [`development.md`](development.md) and [`deployment.md`](deployment.md) for
setup and operational detail.

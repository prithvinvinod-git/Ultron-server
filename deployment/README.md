# ULTRON deployment

The deployment path is **non-Docker systemd on Ubuntu Server** (T194). Docker
artifacts are kept as a documented alternative only:

| Path | Status |
|---|---|
| systemd units (`systemd/`) | **Real.** The deployment path. |
| scripts (`scripts/`) | **Real.** Backup, restore, migrate, health check. |
| compose observability profile (`compose/`) | Optional add-on (Prometheus + Grafana, T184). |
| `docker/Dockerfile`, root `docker-compose.yml` | `DEFERRED` (T190/T191) — no Docker here. |

## Directory layout (T197, spec §47)

```
/opt/ultron/
  server/        application code (git checkout; delivered, not written at runtime)
  deployment/    these units and scripts
  config/        .env and other configuration (secrets; outside the git tree)
  workspaces/    agent git workspaces        <- WRITABLE, persistent
  logs/          structured logs             <- WRITABLE, persistent
  data/          persistent non-database data<- WRITABLE, persistent
    backups/     output of backup.sh (never included in a backup)
```

Everything outside those three writable directories is read-only to the service
(`ProtectSystem=strict`). This is deliberate: it is what keeps a runaway tool
from rewriting the code or the config.

## Persistent volumes

Two stores are persistent and must survive a redeploy:

- **PostgreSQL** — the source of truth (A§22). It lives outside `/opt/ultron`
  (a managed cluster or a separate volume). `backup.sh` dumps it.
- **`/opt/ultron/{workspaces,data}`** — agent clones and derived data. Archived
  by `backup.sh`.

Redis is intentionally **not** backed up: it holds locks, queues and transient
state, and restoring it can resurrect a lock for a task that no longer exists
(A§24). Rebuild it empty.

## Hardening (T196, spec A§31)

The `ultron-api.service` unit applies the following, and the runbook assumes
them rather than re-documenting them:

- **No public datastores.** PostgreSQL, Redis and Ollama bind `127.0.0.1` (or a
  private interface). `/metrics` and `/health` are the only HTTP surfaces, and
  they sit behind the reverse proxy like everything else.
- **Non-root, dedicated user.** `User=ultron`, no login shell, owns only
  `/opt/ultron`.
- **Least-privilege sandbox.** `NoNewPrivileges`, `ProtectSystem=strict`,
  `ProtectHome=read-only`, `PrivateTmp`, `RestrictAddressFamilies`, a
  `SystemCallFilter`, and only three `ReadWritePaths`.
- **Secrets in the environment file, not the repo.** `/opt/ultron/config/.env`
  is `chmod 600`, owned by `ultron`, and never committed. `JWT_SECRET` and
  `ADMIN_PASSWORD` must be generated, not defaulted.
- **Reverse proxy terminates TLS.** ULTRON binds `127.0.0.1`; the proxy owns
  certificates, `ALLOWED_HOSTS`, and `CORS_ORIGINS`.

## Scripts

| Script | Purpose |
|---|---|
| `scripts/backup.sh [dest]` | Dump PostgreSQL + archive `workspaces/ data/ logs/`. |
| `scripts/restore.sh <archive> [--yes]` | Restore; refuses while `ultron-api` runs. |
| `scripts/migrate.sh` | `alembic upgrade head` under the deployed checkout. |
| `scripts/health-check.sh [host] [port]` | Probe `/health`, `/ready`, `/metrics`. |

All scripts source `scripts/_common.sh`, read `DATABASE_URL` from
`/opt/ultron/config/.env`, and take `ULTRON_ROOT` from the environment (default
`/opt/ultron`).

See `systemd/README.md` for the install runbook.

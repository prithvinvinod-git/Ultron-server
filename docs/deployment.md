# Deployment

ULTRON's deployment path is **non-Docker systemd on Ubuntu Server**. Docker
artifacts exist but are deferred; see the note at the end.

The authoritative, operational details live next to the artifacts:

- [`deployment/README.md`](../deployment/README.md) — layout, persistent
  volumes, hardening, and the script reference.
- [`deployment/systemd/README.md`](../deployment/systemd/README.md) — the
  install runbook for the service.

This document is the summary.

## What runs

| Unit | Command | Status |
|---|---|---|
| `ultron-api.service` | `uv run --project /opt/ultron/server uvicorn app.main:app` | Real. |

There is **no separate worker unit**. All background work (scheduler, event
automation, notification dispatch, persistence, the Phase 12 metrics subscriber
and resource monitor) starts inside the API process via `Container.startup()`.
There is no `ultron worker` entrypoint, so a worker unit would be fiction.

## Target layout (spec §47)

```
/opt/ultron/
  server/        application code (git checkout)
  deployment/    units and operational scripts
  config/        .env (chmod 600, owned by ultron)
  workspaces/    agent git workspaces   <- WRITABLE
  logs/          structured logs        <- WRITABLE
  data/          persistent data        <- WRITABLE
    backups/     backup.sh output
```

Everything outside the three writable directories is read-only to the service
under `ProtectSystem=strict`.

## Install (summary)

```bash
sudo useradd --system --home /opt/ultron --shell /usr/sbin/nologin ultron
sudo mkdir -p /opt/ultron/{server,config,workspaces,logs,data}
sudo chown -R ultron:ultron /opt/ultron

sudo -u ultron git clone <repo> /opt/ultron/server
sudo -u ultron bash /opt/ultron/server/scripts/setup.sh

sudo -u ultron cp /opt/ultron/server/.env.example /opt/ultron/config/.env
sudo chmod 600 /opt/ultron/config/.env
sudo -u ultron "${EDITOR:-nano}" /opt/ultron/config/.env

cd /opt/ultron/server && sudo -u ultron bash -lc \
    'uv run --project /opt/ultron/server alembic upgrade head'

sudo cp /opt/ultron/server/deployment/systemd/ultron-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ultron-api
curl http://127.0.0.1:8000/ready
```

The full runbook, including the sandbox directives and their rationale, is in
[`deployment/systemd/README.md`](../deployment/systemd/README.md).

## Operations

All scripts source `deployment/scripts/_common.sh` and read `DATABASE_URL` from
`/opt/ultron/config/.env`. Override the install root with `ULTRON_ROOT`.

| Task | Command |
|---|---|
| Backup | `deployment/scripts/backup.sh [dest]` |
| Restore | `deployment/scripts/restore.sh <archive> [--yes]` |
| Migrate | `deployment/scripts/migrate.sh` |
| Health probe | `deployment/scripts/health-check.sh [host] [port]` |

`backup.sh` dumps PostgreSQL and archives `workspaces/ data/ logs/`. Redis is
**not** backed up: it holds locks and transient state, and restoring it can
resurrect a lock for a task that no longer exists. `restore.sh` refuses to run
while `ultron-api` is active.

## Security

Hardening requirements (spec §31) are recorded in
[`deployment/README.md`](../deployment/README.md#hardening-t196-spec-a31) and
enforced by the unit:

- PostgreSQL, Redis and Ollama bind `127.0.0.1` (or a private interface).
- The service runs as a dedicated non-root user with no login shell.
- `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=read-only`,
  `PrivateTmp`, `RestrictAddressFamilies`, a `SystemCallFilter`, and exactly
  three `ReadWritePaths`.
- Secrets live in `/opt/ultron/config/.env`, never in the repository.
- A reverse proxy terminates TLS; ULTRON binds `127.0.0.1`.

## Observability profile (optional)

Prometheus and Grafana are available as an **optional** compose profile for a
host that already runs Docker:

```bash
docker compose -f deployment/compose/docker-compose.observability.yml up -d
```

Prometheus scrapes the API's `/metrics`; Grafana is provisioned with the
`ULTRON Core` dashboard. Both ports bind `127.0.0.1`, matching the rule that
`/metrics` is never public.

## Docker path (deferred)

`deployment/docker/Dockerfile` (T190) and the root `docker-compose.yml` (T191)
are **deferred**, not designed-and-shipped. The dev machine has no Docker, so
they would be unverifiable. T193's original "`docker compose up -d`" instruction
is revised accordingly: the documented path is systemd. Do not treat the absence
of a Dockerfile as an oversight; it is a recorded decision.

# ULTRON on systemd (T192 / T194)

This is the **actual deployment path** for ULTRON on Ubuntu Server. It is
non-Docker on purpose: the author's server runs ULTRON directly, and T190/T191
(Dockerfile, root `docker-compose.yml`) are `DEFERRED` until Docker is in play.
`docker compose up -d` is therefore *not* the documented path (T193 is revised
to say so).

The repository's `scripts/start.sh` already starts the API the way a service
should. These units wrap that command in a supervisor, add boot ordering, and
confine the process.

## What runs

| Unit | Command | Status |
|---|---|---|
| `ultron-api.service` | `uv run ... uvicorn app.main:app` | Real. This is the process that exists today. |

**There is no separate worker unit.** Every background component
(scheduler, event automation, notification dispatch, persistence, the metrics
subscriber and the resource monitor added in Phase 12) is started *inside* the
API process by `Container.startup()`. A second process would therefore duplicate
that work, not offload it. A `ultron worker` entrypoint does **not** exist:
`server/pyproject.toml` declares `ultron = "app.__main__:main"`, but
`app/__main__.py` has not been written yet. Until it is, the API unit is the
whole deployment. This is recorded here rather than shipped as a plausible unit
that would fail its first start.

## Layout (spec §47)

```
/opt/ultron/
  server/        application code (this git checkout)
  deployment/    these units and deployment/scripts
  config/        .env and other configuration
  workspaces/    agent git workspaces (WRITABLE)
  logs/          structured logs (WRITABLE)
  data/          persistent non-database data (WRITABLE)
  .local/bin/    uv, in the service user's home (or .cargo/bin)
```

## Install

```bash
# 1. Service user (no login shell, owns /opt/ultron)
sudo useradd --system --home /opt/ultron --shell /usr/sbin/nologin ultron
sudo mkdir -p /opt/ultron/{server,config,workspaces,logs,data}
sudo chown -R ultron:ultron /opt/ultron

# 2. Code and dependencies
sudo -u ultron git clone <repo> /opt/ultron/server
sudo -u ultron bash /opt/ultron/server/scripts/setup.sh

# 3. Configuration
sudo -u ultron cp /opt/ultron/server/.env.example /opt/ultron/config/.env
sudo chmod 600 /opt/ultron/config/.env
sudo -u ultron "${EDITOR:-nano}" /opt/ultron/config/.env   # fill in secrets

# 4. Migrations
cd /opt/ultron/server && sudo -u ultron bash -lc \
    'uv run --project /opt/ultron/server alembic upgrade head'

# 5. Service
sudo cp /opt/ultron/server/deployment/systemd/ultron-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ultron-api
curl http://127.0.0.1:8000/ready
```

## Notes

- **Bind address.** The unit binds `127.0.0.1:8000`. A reverse proxy (nginx /
  Caddy) terminates TLS in front of it. Do not expose `0.0.0.0` directly; see
  `docs/security.md`.
- **`ProtectSystem=strict`** makes the whole filesystem read-only except the
  three `ReadWritePaths`. If a component needs to write elsewhere, add that path
  rather than disabling the sandbox.
- **Configuration is read from `/opt/ultron/config/.env`**, not from the
  checkout, so secrets are never inside the git working tree. `WorkingDirectory`
  is `/opt/ultron/server` so relative paths resolve to the checkout.

See `deployment/scripts/` for backup, restore, migration and the health probe.

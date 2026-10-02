# ULTRON Server - Setup & Deployment Log

**Date:** October 2, 2026  
**Target Environment:** Ubuntu Server (Headless, Ethernet)  
**Access Environment:** Windows Client PC  

---

## 1. Overview
Documenting the deployment, database initialization, Redis configuration, ASGI server setup, and firewall access configuration for ULTRON Server (Phase 1 Foundation).

---

## 2. Infrastructure & Environment Setup

- **Python Runtime:** Python 3.12+ / 3.14 via `uv` package manager.
- **Database Engine:** PostgreSQL 17 (authoritative store).
- **Caching & Locks:** Redis 7 (transient state, pub/sub, locks).
- **Process Manager:** Uvicorn ASGI server.

---

## 3. Database Initialization & Schema Migrations

### User & Database Creation
Created authoritative database role and database instance:
```sql
CREATE USER ultron WITH PASSWORD '<SECURE_PASSWORD>';
CREATE DATABASE ultron OWNER ultron;
GRANT ALL PRIVILEGES ON DATABASE ultron TO ultron;
GRANT ALL ON SCHEMA public TO ultron;
```

### Database Connection String (`.env`)
```env
DATABASE_URL=postgresql+asyncpg://ultron:<SECURE_PASSWORD>@127.0.0.1:5432/ultron
```

### Alembic Migrations
Ran initial migration (`0001_initial`) creating 17 foundation tables:
- `users`, `sessions`, `agents`, `tasks`, `task_steps`, `tool_executions`, `events`, `conversations`, `messages`, `memories`, `projects`, `devices`, `device_events`, `agent_logs`, `model_usage`, `audit_logs`, `schedules`.

Command executed:
```bash
cd server && uv run alembic upgrade head
```

---

## 4. Redis Service Setup
- Installed and enabled `redis-server` service on Ubuntu Server.
- Verified connection status via `redis-cli ping` returning `PONG`.

---

## 5. Application Code Fixes

### ASGI Application Instance Fix ([`server/app/main.py`](file:///d:/Downloads/cloneserver/Ultron-server/server/app/main.py#L432))
Exported module-level `app = create_app()` in `server/app/main.py` so Uvicorn launcher (`uvicorn app.main:app`) resolves the application instance without factory errors.

---

## 6. Networking & Remote Access Configuration

To allow external connection from the Windows PC to the Ubuntu Server:

1. **Environment Binding (`.env`):**
   ```env
   API_HOST=0.0.0.0
   ```
2. **Firewall Rule (Ubuntu UFW):**
   ```bash
   sudo ufw allow 8000/tcp
   ```
3. **Execution Command:**
   ```bash
   ULTRON_HOST=0.0.0.0 ./scripts/start.sh
   ```

---

## 7. Verification & Endpoint Checks

From Windows PC web browser / client:
- **Liveness Endpoint:** `http://<UBUNTU_SERVER_IP>:8000/health`
  ```json
  {"status":"ok","alive":true,"version":"0.1.0","warnings":[]}
  ```
- **Readiness Endpoint:** `http://<UBUNTU_SERVER_IP>:8000/ready`
  ```json
  {
    "status": "ok",
    "checks": {
      "postgresql": "ok",
      "redis": "ok",
      "ollama": "skipped",
      "filesystem": "ok"
    }
  }
  ```

---

## 8. Firewall Persistence & Operational Policy

- **Persistence:** The UFW firewall rule (`sudo ufw allow 8000/tcp`) is persistent across system reboots and process restarts.
- **Shutdown / Closing State:** When stopping the ULTRON server process or shutting down the Ubuntu machine, no changes are required to the firewall rules. The port remains permitted for subsequent server startups.
- **Rollback Command (If required in future):** `sudo ufw delete allow 8000/tcp`

---

*Status: **Phase 1 Foundation Live & Verified***


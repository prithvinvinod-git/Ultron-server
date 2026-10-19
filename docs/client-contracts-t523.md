# T523: Contract Inventory and Reuse Map

**Objective:** One documented contract set for all three clients (A§69.7)
**Dependencies:** T514
**Date:** 2026-10-18

## Summary

This document inventories existing endpoints, events, and status models across the ULTRON system. The key principle is reuse - zero new endpoints are introduced where one already exists. All contracts are shared by the console (UIPROTO), Electron desktop app, and will be reused by mobile PWA and ESP32 firmware (as a subset).

## 1. Authentication (A§30)

**Server Route:** pp/api/routes/auth.py

**Endpoints:**

| Method | Path | Purpose | Request | Response | Notes |
|---|---|---|---|---|---|
| POST | /auth/login | Exchange username/password for session | LoginRequest (username, password) | TokenResponse | Same 401 for all failures |
| POST | /auth/refresh | Rotate refresh token | RefreshRequest | TokenResponse | Rotating reused token revokes session family |
| POST | /auth/logout | Revoke session | - | {} | Requires Auth |
| GET | /auth/me | Current principal | - | PrincipalResponse | Requires Auth |
| POST | /auth/password | Change password | ChangePasswordRequest | {} | Revokes other sessions, requires current password |

**Auth Model:** Principal - actor_type, username, user_id, session_id, is_superuser, expires_at

## 2. Health & Readiness (A§29)

**Server Route:** pp/api/routes/health.py

**Endpoints:**

| Method | Path | Purpose | Response | Notes |
|---|---|---|---|---|
| GET | /health | Basic health check | {status: "ok"} | Public |
| GET | /ready | Readiness with dependencies | ReadyResponse | Checks DB, Redis, event bus, agents, scheduler |

## 3. Event Stream (SSE) (A§19, A§27)

**Server Route:** pp/api/routes/events.py

**Endpoints:**

| Method | Path | Purpose | Query | Notes |
|---|---|---|---|---|
| GET | /events | Server-Sent Events (receive-only) | topics, lastEventId/last_event_id | Requires Auth. Use fetch+ReadableStream (not EventSource - no auth header). Heartbeat as comments. Resume via Last-Event-ID. |

## 4. Agents (A§17, A§29)

**Server Route:** pp/api/routes/agents.py

**Endpoints:**

| Method | Path | Purpose | Response/Action |
|---|---|---|---|
| GET | /agents | List agents | Array of AgentRead |
| POST | /agents | Create agent | AgentRead (201) |
| GET | /agents/{id} | Get agent | AgentRead |
| GET | /agents/types | List agent types | Array of AgentTypeRead |
| POST | /agents/{id}/start | Start agent | AgentRead |
| POST | /agents/{id}/stop | Stop agent | AgentRead |
| POST | /agents/{id}/restart | Restart agent | AgentRead |

## 5. Tasks (A§18, A§29)

**Server Route:** pp/api/routes/tasks.py

**Endpoints:**

| Method | Path | Purpose | Response/Action |
|---|---|---|---|
| GET | /tasks | List tasks | Array of TaskRead (filters: status, project_id, limit, offset) |
| POST | /tasks | Create task | TaskRead (201) |
| GET | /tasks/{id} | Get task | TaskRead |
| POST | /tasks/{task_id}/cancel | Cancel task | TaskRead |
| POST | /tasks/{task_id}/retry | Retry task | TaskRead |
| POST | /tasks/{task_id}/priority | Change priority | TaskRead |
| GET | /tasks/{id}/steps | Get task steps | Array of TaskStepRead |
| POST | /tasks/{id}/steps | Add steps | TaskRead |

## 6. Tools (A§29)

**Server Route:** pp/api/routes/tools.py

**Endpoints:**

| Method | Path | Purpose | Response |
|---|---|---|---|
| GET | /tools | List tools | Array of ToolRead |

## 7. Chat (A§15)

**Server Route:** pp/api/routes/chat.py

**Endpoints:**

| Method | Path | Purpose | Notes |
|---|---|---|---|
| POST | /chat/stream | Streaming chat response | SSE-style streaming via fetch/ReadableStream (like /events). Auth required. |

## 8. Nodes (A§29)

**Server Route:** pp/api/routes/nodes.py

**Endpoints:**

| Method | Path | Purpose | Notes |
|---|---|---|---|
| GET | /nodes | List nodes | Node management |
| GET/POST/other | /nodes/* | Node operations | As implemented |
| WS | /nodes/ws | WebSocket for node communication | Upgraded via HTTP - proxied by Electron static server |

## 9. Devices (A§29)

**Server Route:** pp/api/routes/devices.py

**Endpoints:**

| Method | Path | Purpose |
|---|---|---|
| GET | /devices | List devices |
| POST | /devices | Register device |
| GET | /devices/{id} | Get device |
| POST | /devices/{id}/register | Device registration/presence |
| POST | /devices/{id}/heartbeat | Device heartbeat |

## 10. Voice (A§29)
**Server Route:** pp/api/routes/voice.py - Voice operations

## 11. Schedules (A§29)
**Server Route:** pp/api/routes/schedules.py - Schedule management

## 12. Notifications (A§29)
**Server Route:** pp/api/routes/notifications.py - Notification management

## Status Models (from API types)

Key enums used across all clients (see UIPROTO/src/lib/api.ts):
- AgentStatus: created, initializing, ready, running, waiting, verifying, completed, failed, cancelled, timeout
- TaskStatus: pending, queued, running, blocked, paused, completed, failed, cancelled
- TaskPriority: low, normal, high, urgent
- VerificationOutcome: success, partial, failed, unverified

## Client Reuse

- **Console (UIPROTO):** Uses @/lib/api.ts with fetch-based client, SSE via fetch+ReadableStream (streamEvents), token refresh on 401
- **Electron:** Serves same static bundle, same API contracts. REST/WS/SSE proxied to backend (static-server.ts) - no new protocol
- **Mobile PWA:** Must reuse same REST contracts and types (T524). No Windows Node Bridge access
- **ESP32:** Subset only (T525) - auth, device registration/heartbeat, basic status, command results. Must not gain privileged ops

## Conclusion

All contracts already exist. No new endpoints introduced. Reuse principle satisfied. Map cross-checked against server routes.

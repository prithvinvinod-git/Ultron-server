# API

ULTRON Core exposes a FastAPI HTTP + WebSocket API. Interactive docs are served
at `/docs` (disablable via `DOCS_ENABLED`) and the OpenAPI schema at
`/openapi.json`. The app reports version `0.1.0`.

Code: `server/app/api/routes/`; routers are mounted in
`server/app/main.py::_include_routers`.

Unless noted, endpoints require an `Authorization: Bearer <token>` header (login
first) and return JSON. When `ALLOW_ANONYMOUS=true` this is relaxed — never in
production.

## Authentication — `/auth`

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/auth/login` | Exchange username + password for access + refresh tokens. |
| `POST` | `/auth/refresh` | Rotate a refresh token. |
| `POST` | `/auth/logout` | Revoke the current session. |
| `GET` | `/auth/me` | Describe the authenticated caller. |
| `POST` | `/auth/password` | Change the authenticated user's password. |

## Health — (no prefix)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness probe (always `200`; read `status`). |
| `GET` | `/ready` | Readiness probe (`200` ready, `503` not). |
| `GET` | `/metrics` | Prometheus metrics. |

`/metrics` is unauthenticated by design; restrict it at the proxy/firewall.

## Events — `/events`

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/events/stream` | The event stream (Server-Sent Events). |

## Chat — `/chat`

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/chat` | Ask one question. |
| `POST` | `/chat/stream` | Ask one question, streamed (SSE). |

## Agents — `/agents`

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/agents` | List agents. |
| `POST` | `/agents` | Spawn an agent. |
| `GET` | `/agents/types` | List agent types. |
| `GET` | `/agents/{id}` | Inspect one agent. |
| `DELETE` | `/agents/{id}` | Remove an agent. |
| `POST` | `/agents/{id}/cancel` `…/pause` `…/resume` `…/complete` `…/fail` | Lifecycle transitions. |
| `POST` | `/agents/{id}/task` | Assign a task. |
| `POST` | `/agents/{id}/model` | Assign a model. |
| `POST` | `/agents/{id}/tools` | Assign tools. |

## Tasks — `/tasks`

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/tasks` | List tasks. |
| `POST` | `/tasks` | Create a task. |
| `GET` | `/tasks/{id}` | Get one task. |
| `DELETE` | `/tasks/{id}` | Delete a finished task. |
| `POST` | `/tasks/{id}/cancel` `…/retry` `…/priority` | Control a task. |
| `GET` | `/tasks/{id}/steps` | List a task's steps. |
| `POST` | `/tasks/{id}/steps` | Append a step. |

## Tools — `/tools`

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/tools` | List registered tools. |
| `GET` | `/tools/{name}` | Describe one tool. |
| `POST` | `/tools/{name}/validate` | Validate arguments against a tool's schema (`422` with details on violation). |

## Computer nodes — `/nodes`

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/nodes` | List registered nodes and capabilities. |
| `GET` | `/nodes/{id}` | Describe one node. |
| `WS` | `/nodes/ws` | Node transport. |

See [`computer-nodes.md`](computer-nodes.md).

## Devices — `/devices`

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/devices` | List registered devices. |
| `GET` | `/devices/{id}` | Describe one device. |
| `WS` | `/devices/ws` | Device transport. |

See [`esp32.md`](esp32.md).

## Voice — `/voice`

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/voice/engines` | Report available STT/TTS engines. |
| `WS` | `/voice/ws` | Voice session transport. |

See [`voice.md`](voice.md).

## Schedules — `/schedules`

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/schedules` | List schedules. |
| `POST` | `/schedules` | Create a schedule. |
| `GET` | `/schedules/{id}` | Get one schedule. |
| `PATCH` | `/schedules/{id}` | Edit a schedule. |
| `DELETE` | `/schedules/{id}` | Remove a schedule. |
| `POST` | `/schedules/{id}/pause` `…/resume` `…/fire` | Control a schedule (fire runs it now). |

## Notifications — `/notifications`

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/notifications` | Recent notifications. |
| `GET` | `/notifications/providers` | Configured providers. |
| `POST` | `/notifications/test` | Send a test notification. |

## Errors

Errors are structured and machine-readable. A tool-argument violation returns
`422` with per-field details; a missing resource returns `404` with a typed code
(e.g. `TOOL_NOT_FOUND`); a permission denial is reported with its decision
(`deny` / `require_confirmation`). See [`security.md`](security.md).

## Transports

- **HTTP/JSON** — request/response endpoints above.
- **Server-Sent Events** — `/events/stream`, `/chat/stream`.
- **WebSocket** — `/nodes/ws`, `/devices/ws`, `/voice/ws` for live sessions.

## Hard rules

1. Endpoints route through the Core; they do not bypass permissions or the event
   bus.
2. Every mutating call is auditable and attributable to a principal.
3. `/metrics` must be restricted externally — it is not authenticated.

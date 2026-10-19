# Computer nodes

A computer node is a separate machine that ULTRON can operate: a Windows desktop
or another server. The server registers nodes, correlates commands and results,
and monitors liveness — the node never drives itself.

Code: `server/app/agents/computer/`.

## Gateway

`NodeGateway` (`gateway.py`) is the server's entry point:

- `register(...)` — mint a node token and register (or re-register) a node.
- `send(...)` — deliver one structured command and await its result.
- heartbeat monitoring — records liveness and marks a node `offline` after
  `COMPUTER_NODE_OFFLINE_AFTER`.
- `resolve(...)` — resolve a pending command/result correlation.

Nodes reach the gateway over a `NodeTransport` (a Protocol: `send` + `close`).
`ComputerNode` holds one node's state; `_Pending` tracks in-flight commands.

`auth.py` mints and validates node tokens (`COMPUTER_NODE_TOKEN_TTL`).
`fallback.py` holds the capability fallback chain. `protocol.py` defines the wire
vocabulary. The computer-use tools (`computer.*`) are built in
`app/tools/computer/tools.py`.

## Node inventory (§64.5)

| NodeType | Meaning |
|---|---|
| `windows` | A Windows desktop node. |
| `server` | A server node. |
| `esp32` | An ESP32 device node. |
| `future` | Escape hatch (§64.2) so a new kind can register without a schema change. |

## Node status (§64.10)

`online` \| `offline` \| `degraded`.

A node goes `offline` when its heartbeat stops
(`COMPUTER_NODE_OFFLINE_AFTER` seconds after `COMPUTER_NODE_HEARTBEAT`).

## Command/result

| Message | Direction | Purpose |
|---|---|---|
| `register` / `registered` | node → server → node | Registration handshake. |
| `heartbeat` / `heartbeat_ack` | node ↔ server | Liveness. |
| `command` / `result` | server → node → server | One operation and its outcome. |
| `goodbye` | node → server | Graceful departure. |

`ResultStatus` is `ok` or `error`. Every command result is correlated back to its
command, so a result for a command that no longer exists is rejected rather than
applied to the wrong call.

## Capability priority (§42)

Interaction methods are tried in a configured order — application API first,
then increasingly indirect approaches:

| Priority | Method |
|---|---|
| 1 | `application_api` |
| 2 | `dom` |
| 3 | `ui_automation` |
| 4 | `keyboard_shortcut` |
| 5 | `mouse` |
| 6 | `vision` |

`COMPUTER_CAPABILITY_PRIORITY` sets the order; `fallback.py` walks it and picks
the first capability a node actually supports. A capability that is unavailable
is reported `skipped`, never silently simulated.

## Computer tools

The gateway exposes these tools when `COMPUTER_NODES_ENABLED` is true and a
gateway is present:

`computer.list_windows`, `computer.screenshot`, `computer.click`,
`computer.type`, `computer.keys`, `computer.open_application`,
`computer.close_application`.

A `ComputerAgent` (`computer.agent`) drives them through the normal tool gate.

## Configuration

| Variable | Default | Effect |
|---|---|---|
| `COMPUTER_NODES_ENABLED` | `true` | Enable the gateway and `computer.*` tools. |
| `COMPUTER_NODE_TOKEN_TTL` | `3600` | Node token lifetime (seconds). |
| `COMPUTER_NODE_HEARTBEAT` | `15` | Expected heartbeat interval (seconds). |
| `COMPUTER_NODE_OFFLINE_AFTER` | `45` | Mark offline after this many seconds. |
| `COMPUTER_CAPABILITY_PRIORITY` | `application_api,dom,…` | Capability order. |
| `COMPUTER_NODE_DEFAULT_PERMISSION_LEVEL` | `4` | Default node permission level. |

See [`configuration.md`](configuration.md) for the full list.

## Hard rules

1. The node never drives itself; the server sends commands.
2. Node tokens have a TTL.
3. Command results are correlated; an uncorrelated result is rejected.
4. A node that stops heartbeating is reported `offline`, never assumed present.
5. A missing capability is reported `skipped`, never faked.

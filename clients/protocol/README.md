# Computer-Node Protocol

The wire contract between the ULTRON **server gateway**
(`server/app/agents/computer/gateway.py`) and a **computer node**
(`clients/desktop-node/`).

The normative document is [`node-protocol.schema.json`](./node-protocol.schema.json)
(JSON Schema 2020-12). The server's Pydantic models
(`server/app/agents/computer/protocol.py`) are a typed view of the same contract,
and `server/tests/unit/test_node_protocol.py` asserts the two agree
field-for-field — a change to one that forgets the other fails the test suite
rather than the wire.

## Transport

One JSON object per **WebSocket text frame**. The node dials the server:

```
node ──(outbound WSS)──▶ server   ws(s)://<host>/nodes/ws
```

The connection is always **outbound from the node** (spec §59.15/§64.6.4): the
server never dials into the LAN, and nothing here assumes a listening socket on
the node. Across a non-loopback hop the socket is TLS with a pinned server
certificate; the node presents its identity as the node credential below, not as
a client certificate (T347, mTLS-ready).

## Handshake and lifecycle

```
node   ──── register        ──▶  server
server ──── registered      ──▶  node     (accepted, or a typed refusal)
node   ──── heartbeat       ──▶  server
server ──── heartbeat_ack   ──▶  node
server ──── command         ──▶  node
node   ──── result          ──▶  server
either ──── goodbye         ──▶  other
```

1. **register** — the node's first frame: `node_id`, `node_type`, capabilities,
   and its `credential`. The server answers with **registered**.
2. **registered** — `accepted` plus the rules the node must follow
   (`heartbeat_interval`, `offline_after`). A refusal is `accepted: false` with a
   `reason`; the server then closes. A refused handshake is a normal outcome the
   node can report, **not** a server fault.
3. **heartbeat** — every `heartbeat_interval` seconds. It may refresh
   `capabilities` and/or `status`. The server answers **heartbeat_ack**.
4. **command / result** — the server sends structured commands; the node answers
   each with one **result** correlated by `command_id`.
5. A node that stops heartbeating is marked offline by the server; a dropped
   socket is treated as a disconnect. **goodbye** is a courtesy, not the
   mechanism.

## Two rules that shape every field

- **The connection is outbound from the node.** The node dials the server. The
  server never initiates a connection into the LAN.
- **A node credential authenticates a node, never a user.** `credential` rides on
  `register` and identifies the *machine*. The human who asked for a command is a
  separate principal, carried on the command as `principal`, for the node-side
  permission re-check (spec §64.6.3 Path B, §64.13).

## Messages

| `type`           | direction       | purpose                                        |
| ---------------- | --------------- | ---------------------------------------------- |
| `register`       | node → server   | announce identity, capabilities, credential    |
| `registered`     | server → node   | accept/refuse, hand down heartbeat rules       |
| `heartbeat`      | node → server   | liveness; optional capability/status refresh   |
| `heartbeat_ack`  | server → node   | acknowledge liveness, carry server time        |
| `command`        | server → node   | one structured command (never a shell string)  |
| `result`         | node → server   | the answer to a command, correlated by id      |
| `goodbye`        | either          | polite close                                   |

See the schema for exact fields, required sets and bounds. `extra` properties are
**forbidden** (`additionalProperties: false`) on every message: an unknown field
is a contract violation the node should fix, not something to ignore.

## Capabilities

Capabilities are **strings** advertised by the node (spec §64.10) — one registry,
no third. `$defs/Capability` in the schema lists the common families (Windows
§64.6.2, server §64.7, ESP32 §64.9), but the list is advisory: the registry stores
whatever a node advertises. Discovery is **live** — a node's capabilities are what
it last advertised, not a config file.

## Structured commands only

`command.tool` is a registered tool name and `command.arguments` is that tool's
argument object. There is **no** field for an arbitrary command string, PowerShell
line, or shell pipeline (spec §13): the node dispatches on the tool name to a
handler it implements. A capability the node does not advertise, or a tool it does
not implement, is answered with a `result` whose `status` is `error` and whose
`error_code` is `CAPABILITY_NOT_IMPLEMENTED` — never executed speculatively.

## Versioning

`client_of` names the node generation; `version`/`firmware_version` are free-form
build strings. The closed `NodeType` set plus the `future` member let a new node
kind register before its own schema lands, and unknown fields fail fast, so the
contract evolves by adding a documented member rather than by drifting.

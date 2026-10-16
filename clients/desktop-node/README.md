# ULTRON Desktop Node

The Windows-side half of the Computer Node architecture (spec §13/§42/§64). This
directory owns the **contract** a node must satisfy. The executable reference and
the contract tests live in the server test suite so they run under the same
tooling as the gateway they talk to.

- **Normative wire contract:** [`../protocol/node-protocol.schema.json`](../protocol/node-protocol.schema.json)
  (see [`../protocol/README.md`](../protocol/README.md) for the handshake).
- **Executable test double:** `server/tests/support/node_double.py` (`FakeNode`).
- **Contract tests:** `server/tests/unit/test_node_protocol.py` (schema ↔ server
  models ↔ double) and `server/tests/unit/test_nodes_routes.py` (the live
  WebSocket handshake and command round-trip).

There is **no Python on the Windows node**. The server is Python; the node is a
separate program that speaks the JSON contract. The schema is therefore the
source of truth a non-Python implementation is written against, which is why it,
not the server's Pydantic models, is normative.

## What a node must do

1. **Dial out, never listen.** Open the WebSocket to `ws(s)://<server>/nodes/ws`
   (spec §59.15/§64.6.4). The server never connects into the LAN, so the node
   needs no inbound firewall rule.
2. **Register first.** Send exactly one `register` carrying `node_id`,
   `node_type: "windows"`, the capabilities it implements, its build `version`,
   and its `credential`. Wait for `registered`.
3. **Honour the server's rules.** If `registered.accepted` is `false`, read
   `reason`, back off, and retry — do not hammer. Otherwise send `heartbeat`
   every `heartbeat_interval` seconds and treat `offline_after` as the window the
   server allows between heartbeats.
4. **Run structured tools, never shell strings.** On `command`, look up
   `command.tool` in the node's handler table and run it with
   `command.arguments`. There is no field for a shell line (spec §13). Abandon
   the work once `command.deadline` has passed.
5. **Answer every command exactly once.** Send one `result` with the same
   `command_id`; `status: "ok"` with `result`, or `status: "error"` with `error`
   (and an `error_code`). A command with no result is indistinguishable from a
   hung node.
6. **Refuse cleanly.** A tool the node does not implement gets a result with
   `status: "error"` and `error_code: "CAPABILITY_NOT_IMPLEMENTED"` — never a
   silent drop and never an attempt to run something it cannot.

## Capabilities

Advertise the strings from `$defs/Capability` in the schema that the node
actually implements (spec §64.6.2): `filesystem`, `powershell`, `windows_apps`,
`windows_ui`, `browser`, `git`, `processes`, `screenshots`, `notifications`,
`clipboard`.

Capabilities are a **claim the node makes about itself**. The server records what
was advertised and offers a node only the tools its capabilities cover; a node
that advertises a capability it cannot honour will simply fail those commands.
Keep the list honest, and refresh it with a `heartbeat` when what is available
changes (a display is unplugged, a service stops).

## Mapping capabilities to server tools

The server's `computer.*` tools (T143) map onto node capabilities like this:

| Server tool                  | Node capability | Effect                                    |
| ---------------------------- | --------------- | ----------------------------------------- |
| `computer.list_windows`      | `windows_ui`    | enumerate top-level windows               |
| `computer.screenshot`        | `screenshots`   | capture the screen (audited, §15)         |
| `computer.click`             | `windows_ui`    | move/click at a coordinate                |
| `computer.type`              | `windows_ui`    | type text into the focused control        |
| `computer.keys`              | `windows_ui`    | send a key chord                          |
| `computer.open_application`  | `windows_apps`  | launch a named application                |
| `computer.close_application` | `windows_apps`  | close a named application                 |

The router picks the interaction method; the node only executes the one it is
asked for (spec §64.11: the model expresses a preference, the router decides,
and there is no silent re-route).

## Security

- **The credential authenticates the machine, never a user.** A user's authority
  is a separate principal carried on the command (`principal`); the node may use
  it for a local re-check (spec §64.6.3 Path B) but must never treat the node
  credential as permission to act for that user.
- **High-sensitivity operations are audited.** Screenshot and clipboard are
  high-sensitivity (§15); the server audits their dispatch, so the node must not
  add a second, unaudited path around them.
- **Transport is TLS with a pinned server certificate** across any non-loopback
  hop. The node presents no client certificate today (the credential is the
  identity); mTLS is prepared for, not required (T347).
- **No inbound listener, no arbitrary execution.** The node exposes no port and
  runs only registered tools.

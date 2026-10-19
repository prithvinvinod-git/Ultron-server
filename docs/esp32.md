# ESP32 devices

An ESP32 is a lightweight device node: a microphone button, a sensor, a mesh
node. It connects to the server, reports events and accepts commands — the same
protocol a computer node uses, at a smaller scale.

Code: `server/app/devices/`.

## Device manager

`DeviceManager` (`manager.py`) registers devices, correlates commands and
results, tracks liveness, and records events. It is transport-agnostic: a device
behaves identically whichever transport it dialled in on.

- `DeviceTransport` — one device connection (send + close).
- `DeviceStore` — the durable view (`store.py`); `NullDeviceStore` is a no-op
  used when persistence is off.
- `DeviceConnection` — one registered device's live state.
- A device is marked `offline` when its heartbeat stops
  (`DEVICE_OFFLINE_AFTER`).

## Transports (§26)

| Transport | Module | Model |
|---|---|---|
| WebSocket (reference) | `esp32/transport.py` | One socket per device. |
| MQTT (optional) | `mqtt/transport.py` | One shared broker connection, devices addressed by topic. |

A deployment that already runs a broker (Home Assistant, an ESP32 mesh gateway)
uses MQTT; one without uses the WebSocket and pays nothing for the MQTT module.

### MQTT topics

All under `MQTT_TOPIC_PREFIX`:

```
{prefix}/devices/{device_id}/up     device -> server   (register, heartbeat, event, result)
{prefix}/devices/{device_id}/down   server -> device   (registered, heartbeat_ack, command)
```

The bridge subscribes once to `{prefix}/devices/+/up` and reads the device id
off each topic, so it never needs to know which devices exist. A stray broker
message on an unexpected topic is ignored, not fatal.

## Protocol

`protocol.py` defines the wire vocabulary shared by both transports.

Message types: `register`, `registered`, `heartbeat`, `heartbeat_ack`, `event`,
`command`, `result`, `goodbye`. `ResultStatus` is `ok` or `error`.

Device event types:

| Event | Maps to core event | Notes |
|---|---|---|
| `wake_word` | `WAKE_DETECTED` | The device detects the wake word. |
| `button_press` | — | Application-defined. |
| `sensor_event` | — | Application-defined. |
| `audio_stream` | — | Consumed by the voice pipeline. |
| `device_online` | `DEVICE_CONNECTED` | Liveness. |
| `device_offline` | `DEVICE_DISCONNECTED` | Liveness. |

Event names are matched case-insensitively, so a firmware that sends `WAKE_WORD`
still maps.

## Configuration

| Variable | Default | Effect |
|---|---|---|
| `DEVICES_ENABLED` | `true` | Enable the device manager. |
| `DEVICE_TOKEN_TTL` | `3600` | Device token lifetime (seconds). |
| `DEVICE_HEARTBEAT_INTERVAL` | `30` | Expected heartbeat interval. |
| `DEVICE_OFFLINE_AFTER` | `90` | Mark offline after this many seconds. |
| `DEVICE_MAX_EVENT_RATE` | — | Rate-limit device events. |
| `MQTT_ENABLED` | `false` | Enable the MQTT transport. |
| `MQTT_BROKER_URL`, `MQTT_CLIENT_ID`, `MQTT_USERNAME`, `MQTT_PASSWORD` | — | Broker connection. |
| `MQTT_TOPIC_PREFIX` | `ultron` | Topic prefix. |
| `MQTT_QOS`, `MQTT_KEEPALIVE` | — | Broker tuning. |

See [`configuration.md`](configuration.md) for the full list.

## Firmware status

There is **no firmware in this repository**, and the server-side protocol above
does not depend on any particular firmware. A device implementation is expected
to:

1. Open a WebSocket to the server (or publish/subscribe on the MQTT topics).
2. `register` and begin `heartbeat`s within `DEVICE_HEARTBEAT_INTERVAL`.
3. Send `event` messages (`wake_word`, `button_press`, `sensor_event`,
   `audio_stream`) and `result` messages for commands.
4. `goodbye` on a clean shutdown.

The ESP32 node type is registered in the computer-node inventory as `esp32`
(see [`computer-nodes.md`](computer-nodes.md)). Firmware source is tracked as an
open client work item, not as delivered code.

## Hard rules

1. A device that stops heartbeating is reported `offline`, never assumed present.
2. Device tokens have a TTL.
3. Command results are correlated; an uncorrelated result is rejected.
4. A stray topic message is ignored, not fatal.
5. No firmware is claimed to exist until it is written.

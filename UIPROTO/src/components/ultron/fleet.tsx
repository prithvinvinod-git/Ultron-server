/**
 * Fleet surfaces (T507): the registered nodes and the known devices.
 *
 * Both read the REST inventory only — the machine-facing WebSocket endpoints are
 * for the node agents, not the console. When a subsystem is disabled the server
 * returns an empty list, and the panel says so rather than inventing rows.
 */

import { Cpu, Radio } from "lucide-react";

import { useDevices, useNodes } from "@/lib/queries";
import type { DeviceStatus, NodeStatus } from "@/lib/api";
import { useWorkspace } from "@/lib/workspace";

const ONLINE_TONE = "";

function nodeTone(status: NodeStatus): string {
  if (status === "online") return ONLINE_TONE;
  if (status === "degraded") return " inactive";
  return " failed";
}

function deviceTone(status: DeviceStatus): string {
  if (status === "online") return ONLINE_TONE;
  if (status === "disabled") return " failed";
  return " inactive";
}

function shortTime(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleTimeString("en-GB", { hour12: false });
}

export function FleetPanel() {
  const { data, isLoading, error } = useNodes();
  const { select } = useWorkspace();
  const nodes = data ?? [];
  const online = nodes.filter((node) => node.status === "online").length;

  return (
    <section className="hud-panel">
      <div className="panel-heading">
        <h2>NODE FLEET</h2>
        <span>
          {online}/{nodes.length} ONLINE
        </span>
        <Radio size={13} />
      </div>
      {error ? <div className="panel-empty">nodes unavailable</div> : null}
      {isLoading && !data ? <div className="panel-empty">loading…</div> : null}
      {data && nodes.length === 0 ? <div className="panel-empty">no nodes registered</div> : null}

      <div className="agent-list">
        {nodes.map((node) => (
          <button
            key={node.node_id}
            type="button"
            className="agent-row agent-button"
            onClick={() => select({ kind: "node", id: node.node_id })}
          >
            <span className={`status-dot${nodeTone(node.status)}`} />
            <div>
              <strong>{node.name}</strong>
              <small>
                {node.node_type} · {node.version}
              </small>
            </div>
            <span className="agent-pct">{shortTime(node.last_seen)}</span>
          </button>
        ))}
      </div>

      <div className="panel-foot">
        <span>{nodes.length} nodes</span>
        <span>REST</span>
      </div>
    </section>
  );
}

export function DevicesPanel() {
  const { data, isLoading, error } = useDevices();
  const { select } = useWorkspace();
  const devices = data ?? [];
  const online = devices.filter((device) => device.status === "online").length;

  return (
    <section className="hud-panel">
      <div className="panel-heading">
        <h2>DEVICES</h2>
        <span>
          {online}/{devices.length} ONLINE
        </span>
        <Cpu size={13} />
      </div>
      {error ? <div className="panel-empty">devices unavailable</div> : null}
      {isLoading && !data ? <div className="panel-empty">loading…</div> : null}
      {data && devices.length === 0 ? <div className="panel-empty">no devices registered</div> : null}

      <div className="agent-list">
        {devices.map((device) => (
          <button
            key={device.device_id}
            type="button"
            className="agent-row agent-button"
            onClick={() => select({ kind: "device", id: device.device_id })}
          >
            <span className={`status-dot${deviceTone(device.status)}`} />
            <div>
              <strong>{device.name}</strong>
              <small>
                {device.device_type}
                {device.capabilities.length ? ` · ${device.capabilities.length} cap` : ""}
              </small>
            </div>
            <span className="agent-pct">{shortTime(device.last_seen)}</span>
          </button>
        ))}
      </div>

      <div className="panel-foot">
        <span>{devices.length} devices</span>
        <span>{online} online</span>
      </div>
    </section>
  );
}

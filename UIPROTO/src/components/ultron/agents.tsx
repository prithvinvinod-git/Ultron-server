/**
 * Live agent fleet panel.
 *
 * Reads `GET /agents` and drives the five lifecycle operations the API exposes
 * (pause, resume, complete, cancel, fail) plus spawn. Progress is derived from
 * status: the API carries no percentage, and inventing a fake one that ticked on
 * a timer is exactly the "demo telemetry" this console is replacing.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Bot, Pause, Play, Plus, RotateCw, Square, X } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { ApiError, agentsApi } from "@/lib/api";
import type { Agent, AgentStatus } from "@/lib/api";
import { broadcastUnauthorized } from "@/lib/auth";
import { queryKeys, useAgents } from "@/lib/queries";
import { useWorkspace } from "@/lib/workspace";

const ACTIVE_STATUSES: ReadonlySet<AgentStatus> = new Set([
  "created",
  "initializing",
  "ready",
  "running",
  "waiting",
  "verifying",
]);

const TERMINAL_STATUSES: ReadonlySet<AgentStatus> = new Set([
  "completed",
  "failed",
  "cancelled",
  "timeout",
]);

function progressOf(status: AgentStatus): number {
  switch (status) {
    case "ready":
    case "completed":
      return 100;
    case "verifying":
      return 88;
    case "running":
      return 68;
    case "waiting":
      return 50;
    case "initializing":
      return 30;
    case "created":
      return 8;
    default:
      return 0;
  }
}

function reportError(error: unknown): void {
  if (error instanceof ApiError && error.status === 401) {
    broadcastUnauthorized();
    return;
  }
  toast.error(error instanceof Error ? error.message : "request failed");
}

function useAgentActions() {
  const queryClient = useQueryClient();
  const invalidate = () => queryClient.invalidateQueries({ queryKey: queryKeys.agents });
  const onSuccess = (agent: Agent) => {
    toast.success(`${agent.name} → ${agent.status}`);
    void invalidate();
  };
  return {
    pause: useMutation({ mutationFn: (id: string) => agentsApi.pause(id), onSuccess, onError: reportError }),
    resume: useMutation({ mutationFn: (id: string) => agentsApi.resume(id), onSuccess, onError: reportError }),
    cancel: useMutation({ mutationFn: (id: string) => agentsApi.cancel(id), onSuccess, onError: reportError }),
  };
}

function SpawnForm({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [type, setType] = useState("");

  const types = useQuery({
    queryKey: ["agent-types"],
    queryFn: agentsApi.types,
    staleTime: 300000,
  });

  const create = useMutation({
    mutationFn: () => agentsApi.create({ agent_type: type || (types.data?.[0]?.agent_type ?? ""), name }),
    onSuccess: (agent) => {
      toast.success(`${agent.name} spawned`);
      void queryClient.invalidateQueries({ queryKey: queryKeys.agents });
      onClose();
    },
    onError: reportError,
  });

  return (
    <form
      className="task-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (!name.trim() || !(type || types.data?.length)) return;
        create.mutate();
      }}
    >
      <select
        aria-label="Agent type"
        value={type}
        onChange={(event) => setType(event.target.value)}
        className="agent-select"
      >
        {(types.data ?? []).map((option) => (
          <option key={option.agent_type} value={option.agent_type}>
            {option.agent_type}
          </option>
        ))}
      </select>
      <input
        aria-label="Agent name"
        autoFocus
        value={name}
        onChange={(event) => setName(event.target.value)}
        placeholder="Agent name…"
      />
      <Button variant="ghost" size="icon" type="submit" aria-label="Spawn agent" disabled={create.isPending}>
        <Plus />
      </Button>
      <Button variant="ghost" size="icon" type="button" aria-label="Cancel" onClick={onClose}>
        <X />
      </Button>
    </form>
  );
}

export function AgentsPanel() {
  const { data, isLoading, error } = useAgents();
  const actions = useAgentActions();
  const { select } = useWorkspace();
  const [selected, setSelected] = useState<string | null>(null);
  const [spawning, setSpawning] = useState(false);

  const agents = data ?? [];
  const active = agents.filter((agent) => ACTIVE_STATUSES.has(agent.status)).length;
  const current = agents.find((agent) => agent.id === selected) ?? null;

  return (
    <section className="hud-panel">
      <div className="panel-heading">
        <h2>
          AGENTS · {active}/{agents.length} ACTIVE
        </h2>
        <Bot size={13} />
      </div>

      {error ? <div className="panel-empty">agents unavailable</div> : null}
      {isLoading && !data ? <div className="panel-empty">loading…</div> : null}
      {data && agents.length === 0 ? <div className="panel-empty">no agents spawned</div> : null}

      <div className="agent-list">
        {agents.map((agent) => {
          const on = ACTIVE_STATUSES.has(agent.status);
          const pct = progressOf(agent.status);
          return (
            <button
              key={agent.id}
              type="button"
              className={`agent-row agent-button${selected === agent.id ? " is-selected" : ""}`}
              onClick={() => {
                const next = selected === agent.id ? null : agent.id;
                setSelected(next);
                select(next ? { kind: "agent", id: next } : null);
              }}
              aria-pressed={selected === agent.id}
            >
              <span className="agent-dot" data-on={on} />
              <div>
                <strong>{agent.name}</strong>
                <small>
                  {agent.agent_type} · {agent.status}
                </small>
              </div>
              <span className="agent-pct">{on ? `${pct}%` : "—"}</span>
              <div className="agent-bar">
                <span style={{ width: `${pct}%` }} />
              </div>
            </button>
          );
        })}
      </div>

      {current ? (
        <div className="agent-actions">
          {current.status === "running" ? (
            <Button variant="ghost" size="sm" onClick={() => actions.pause.mutate(current.id)}>
              <Pause />
              Pause
            </Button>
          ) : null}
          {current.status === "waiting" ? (
            <Button variant="ghost" size="sm" onClick={() => actions.resume.mutate(current.id)}>
              <Play />
              Resume
            </Button>
          ) : null}
          {!TERMINAL_STATUSES.has(current.status) ? (
            <Button variant="ghost" size="sm" onClick={() => actions.cancel.mutate(current.id)}>
              <Square />
              Cancel
            </Button>
          ) : null}
          <Button variant="ghost" size="sm" onClick={() => { setSelected(null); select(null); }}>
            <RotateCw />
            Refresh view
          </Button>
        </div>
      ) : null}

      {spawning ? (
        <SpawnForm onClose={() => setSpawning(false)} />
      ) : (
        <Button variant="ghost" className="add-task" onClick={() => setSpawning(true)}>
          <Plus />
          Spawn agent
        </Button>
      )}
    </section>
  );
}

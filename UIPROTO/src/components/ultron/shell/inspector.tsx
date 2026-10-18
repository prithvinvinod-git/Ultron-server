import { useState } from "react";
import type { ReactNode } from "react";
import { Check, Play, Pause, Square, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { Agent } from "@/lib/api";
import { useAgentActions, useAgent, useAgents, useTasks, useTools } from "@/lib/queries";
import type { Selection } from "@/lib/workspace";

function Field({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="inspector-field">
      <span>{label}</span>
      <code>{value ?? "—"}</code>
    </div>
  );
}

function timestamp(value: string): string {
  return new Date(value).toLocaleString("en-GB", { hour12: false });
}

function parseList(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

/**
 * Full control surface for one agent (T504): the lifecycle verbs plus every
 * assignment endpoint. Drafts are seeded from the agent and re-seeded whenever
 * the server's copy changes (the parent keys on `updated_at`), so a poll never
 * clobbers what the operator is typing. Controls disable while any call is in
 * flight.
 */
function AgentControls({ agent }: { agent: Agent }) {
  const actions = useAgentActions(agent.id);
  const { data: tasks } = useTasks();
  const [taskId, setTaskId] = useState(agent.task_id ?? "");
  const [model, setModel] = useState(agent.model ?? "");
  const [tools, setTools] = useState(agent.tools.join(", "));
  const [permissions, setPermissions] = useState(agent.permissions.join(", "));
  const [failReason, setFailReason] = useState("");

  const busy = Object.values(actions).some((mutation) => mutation.isPending);

  return (
    <div className="inspector-controls">
      <div className="inspector-group">
        <span className="inspector-group-title">LIFECYCLE</span>
        <div className="inspector-buttons">
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => actions.pause.mutate()}>
            <Pause />
            Pause
          </Button>
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => actions.resume.mutate()}>
            <Play />
            Resume
          </Button>
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => actions.complete.mutate()}>
            <Check />
            Complete
          </Button>
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => actions.cancel.mutate()}>
            <Square />
            Cancel
          </Button>
        </div>
        <div className="inspector-row">
          <input
            aria-label="Failure reason"
            placeholder="failure reason…"
            value={failReason}
            onChange={(event) => setFailReason(event.target.value)}
          />
          <Button
            variant="ghost"
            size="sm"
            disabled={busy || !failReason.trim()}
            onClick={() => actions.fail.mutate(failReason.trim())}
          >
            <X />
            Fail
          </Button>
        </div>
      </div>

      <div className="inspector-group">
        <span className="inspector-group-title">TASK</span>
        <select
          aria-label="Assign task"
          className="agent-select"
          value={taskId}
          onChange={(event) => setTaskId(event.target.value)}
        >
          <option value="">— none —</option>
          {(tasks ?? []).map((task) => (
            <option key={task.id} value={task.id}>
              {task.goal}
            </option>
          ))}
        </select>
        <Button variant="ghost" size="sm" disabled={busy || !taskId} onClick={() => actions.assignTask.mutate(taskId)}>
          Assign task
        </Button>
      </div>

      <div className="inspector-group">
        <span className="inspector-group-title">MODEL</span>
        <div className="inspector-row">
          <input
            aria-label="Model"
            placeholder="model id…"
            value={model}
            onChange={(event) => setModel(event.target.value)}
          />
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => actions.assignModel.mutate(model.trim() || null)}>
            Set
          </Button>
        </div>
      </div>

      <div className="inspector-group">
        <span className="inspector-group-title">TOOLS</span>
        <input
          aria-label="Tools"
          placeholder="comma-separated…"
          value={tools}
          onChange={(event) => setTools(event.target.value)}
        />
        <Button variant="ghost" size="sm" disabled={busy} onClick={() => actions.assignTools.mutate(parseList(tools))}>
          Assign tools
        </Button>
      </div>

      <div className="inspector-group">
        <span className="inspector-group-title">PERMISSIONS</span>
        <input
          aria-label="Permissions"
          placeholder="comma-separated…"
          value={permissions}
          onChange={(event) => setPermissions(event.target.value)}
        />
        <Button
          variant="ghost"
          size="sm"
          disabled={busy}
          onClick={() => actions.assignPermissions.mutate(parseList(permissions))}
        >
          Assign permissions
        </Button>
      </div>
    </div>
  );
}

function AgentInspector({ id }: { id: string }) {
  const { data: detail } = useAgent(id);
  const { data } = useAgents();
  const agent = detail ?? (data ?? []).find((item) => item.id === id);
  if (!agent) return <div className="panel-empty">agent not found</div>;
  return (
    <>
      <div className="inspector-title">{agent.name}</div>
      <Field label="STATUS" value={agent.status} />
      <Field label="TYPE" value={agent.agent_type} />
      <Field label="MODEL" value={agent.model ?? "—"} />
      <Field label="TASK" value={agent.task_id ?? "—"} />
      <Field label="TOOLS" value={agent.tools.length ? agent.tools.join(", ") : "—"} />
      <Field label="PERMISSIONS" value={agent.permissions.length ? agent.permissions.join(", ") : "—"} />
      <Field label="MEMORY" value={agent.memory_namespace ?? "—"} />
      <Field label="PROJECT" value={agent.project_id ?? "—"} />
      <Field label="PARENT" value={agent.parent_agent_id ?? "—"} />
      <Field label="CREATED" value={timestamp(agent.created_at)} />
      <Field label="UPDATED" value={timestamp(agent.updated_at)} />
      <Field label="ID" value={agent.id} />
      <AgentControls key={`${agent.id}:${agent.updated_at}`} agent={agent} />
    </>
  );
}

function TaskInspector({ id }: { id: string }) {
  const { data } = useTasks();
  const task = (data ?? []).find((item) => item.id === id);
  if (!task) return <div className="panel-empty">task not found</div>;
  return (
    <>
      <div className="inspector-title">{task.goal}</div>
      <Field label="STATUS" value={task.status} />
      <Field label="PRIORITY" value={task.priority} />
      <Field label="RETRIES" value={`${task.retry_count}/${task.max_retries}`} />
      <Field label="CREATED" value={timestamp(task.created_at)} />
      <Field label="ID" value={task.id} />
    </>
  );
}

function ToolInspector({ name }: { name: string }) {
  const { data } = useTools();
  const tool = (data ?? []).find((item) => item.name === name);
  if (!tool) return <div className="panel-empty">tool not found</div>;
  return (
    <>
      <div className="inspector-title">{tool.name}</div>
      <Field label="CATEGORY" value={tool.category} />
      <Field label="PERMISSION" value={`L${tool.permission_level}`} />
      <Field label="AUDIT" value={tool.audit ? "enabled" : "disabled"} />
    </>
  );
}

function FallbackInspector({ selection }: { selection: Selection }) {
  return <Field label={selection.kind.toUpperCase()} value={selection.id} />;
}

/** Right-hand inspector: renders the detail view for the current selection. */
export function SelectionInspector({ selection, onClose }: { selection: Selection; onClose: () => void }) {
  return (
    <aside className="shell-inspector" aria-label="Inspector">
      <div className="panel-heading">
        <h2>INSPECTOR</h2>
        <span>{selection.kind.toUpperCase()}</span>
      </div>
      {selection.kind === "agent" ? (
        <AgentInspector id={selection.id} />
      ) : selection.kind === "task" ? (
        <TaskInspector id={selection.id} />
      ) : selection.kind === "tool" ? (
        <ToolInspector name={selection.id} />
      ) : (
        <FallbackInspector selection={selection} />
      )}
      <Button variant="ghost" size="sm" onClick={onClose}>
        Close
      </Button>
    </aside>
  );
}

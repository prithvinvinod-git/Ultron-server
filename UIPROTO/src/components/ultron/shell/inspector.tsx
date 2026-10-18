import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { useAgents, useTasks, useTools } from "@/lib/queries";
import type { Selection } from "@/lib/workspace";

function Field({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="inspector-field">
      <span>{label}</span>
      <code>{value ?? "—"}</code>
    </div>
  );
}

function AgentInspector({ id }: { id: string }) {
  const { data } = useAgents();
  const agent = (data ?? []).find((item) => item.id === id);
  if (!agent) return <div className="panel-empty">agent not found</div>;
  return (
    <>
      <div className="inspector-title">{agent.name}</div>
      <Field label="STATUS" value={agent.status} />
      <Field label="TYPE" value={agent.agent_type} />
      <Field label="ID" value={agent.id} />
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
      <Field label="CREATED" value={new Date(task.created_at).toLocaleString("en-GB", { hour12: false })} />
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

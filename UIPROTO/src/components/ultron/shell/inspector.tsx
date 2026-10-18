import { useState } from "react";
import type { ReactNode } from "react";
import { useMutation } from "@tanstack/react-query";
import { Check, Play, Pause, RotateCw, Square, Trash2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ApiError, toolsApi } from "@/lib/api";
import type { Agent, Task, TaskPriority } from "@/lib/api";
import { broadcastUnauthorized } from "@/lib/auth";
import { useAgent, useAgentActions, useAgents, useTask, useTaskActions, useTasks, useTaskSteps, useTool, useTools } from "@/lib/queries";
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

function TaskSteps({ taskId }: { taskId: string }) {
  const { data } = useTaskSteps(taskId);
  const steps = data ?? [];
  if (!steps.length) return <div className="panel-empty">no steps</div>;
  return (
    <div className="step-list">
      {steps.map((step) => (
        <div key={step.id} className="step-row">
          <span
            className={`status-dot${step.status === "failed" ? " failed" : step.status === "completed" ? "" : " inactive"}`}
          />
          <div>
            <strong>
              {step.position + 1}. {step.name}
            </strong>
            <small>
              {step.status}
              {step.attempt > 1 ? ` · attempt ${step.attempt}` : ""}
              {step.error ? ` · ${step.error}` : ""}
            </small>
          </div>
        </div>
      ))}
    </div>
  );
}

const OPEN_TASK_STATUSES = new Set(["pending", "queued", "running", "blocked", "paused"]);
const PRIORITIES: TaskPriority[] = ["low", "normal", "high", "urgent"];

/** Task lifecycle + step authoring for one task (T505). */
function TaskControls({ task }: { task: Task }) {
  const actions = useTaskActions(task.id);
  const [stepName, setStepName] = useState("");
  const [stepDescription, setStepDescription] = useState("");
  const busy = Object.values(actions).some((mutation) => mutation.isPending);
  const isOpen = OPEN_TASK_STATUSES.has(task.status);

  return (
    <div className="inspector-controls">
      <div className="inspector-group">
        <span className="inspector-group-title">PRIORITY</span>
        <select
          aria-label="Priority"
          className="agent-select"
          value={task.priority}
          disabled={busy}
          onChange={(event) => actions.priority.mutate(event.target.value as TaskPriority)}
        >
          {PRIORITIES.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
      </div>

      <div className="inspector-group">
        <span className="inspector-group-title">ACTIONS</span>
        <div className="inspector-buttons">
          {isOpen ? (
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => actions.cancel.mutate()}>
              <Square />
              Cancel
            </Button>
          ) : null}
          {task.status === "failed" ? (
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => actions.retry.mutate()}>
              <RotateCw />
              Retry
            </Button>
          ) : null}
          {!isOpen ? (
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => actions.remove.mutate()}>
              <Trash2 />
              Delete
            </Button>
          ) : null}
        </div>
      </div>

      <div className="inspector-group">
        <span className="inspector-group-title">ADD STEP</span>
        <input
          aria-label="Step name"
          placeholder="step name…"
          value={stepName}
          onChange={(event) => setStepName(event.target.value)}
        />
        <div className="inspector-row">
          <input
            aria-label="Step description"
            placeholder="description (optional)…"
            value={stepDescription}
            onChange={(event) => setStepDescription(event.target.value)}
          />
          <Button
            variant="ghost"
            size="sm"
            disabled={busy || !stepName.trim()}
            onClick={() =>
              actions.addStep.mutate(
                { name: stepName.trim(), description: stepDescription.trim() || null },
                {
                  onSuccess: () => {
                    setStepName("");
                    setStepDescription("");
                  },
                },
              )
            }
          >
            Add
          </Button>
        </div>
      </div>
    </div>
  );
}

function TaskInspector({ id }: { id: string }) {
  const { data: detail } = useTask(id);
  const { data } = useTasks();
  const task = detail ?? (data ?? []).find((item) => item.id === id);
  if (!task) return <div className="panel-empty">task not found</div>;
  return (
    <>
      <div className="inspector-title">{task.goal}</div>
      <Field label="STATUS" value={task.status} />
      <Field label="PRIORITY" value={task.priority} />
      <Field label="RETRIES" value={`${task.retry_count}/${task.max_retries}`} />
      <Field label="VERIFICATION" value={task.verification ?? "—"} />
      <Field label="ERROR" value={task.error ?? "—"} />
      <Field label="SCHEDULED" value={task.scheduled_for ? timestamp(task.scheduled_for) : "—"} />
      <Field label="CREATED" value={timestamp(task.created_at)} />
      <Field label="UPDATED" value={timestamp(task.updated_at)} />
      <Field label="ID" value={task.id} />
      <span className="inspector-group-title">STEPS</span>
      <TaskSteps taskId={task.id} />
      <TaskControls key={`${task.id}:${task.updated_at}`} task={task} />
    </>
  );
}

function ToolInspector({ name }: { name: string }) {
  const { data: detail } = useTool(name);
  const { data } = useTools();
  const tool = detail ?? (data ?? []).find((item) => item.name === name);
  const [args, setArgs] = useState("{}");
  const [verdict, setVerdict] = useState<string | null>(null);

  const validate = useMutation({
    mutationFn: (parsed: Record<string, unknown>) => toolsApi.validate(name, parsed),
    onSuccess: (result) =>
      setVerdict(result.valid ? "VALID — arguments accepted" : "REJECTED by the tool schema"),
    onError: (error) => {
      if (error instanceof ApiError && error.status === 401) {
        broadcastUnauthorized();
        return;
      }
      setVerdict(error instanceof Error ? error.message : "validation failed");
    },
  });

  if (!tool) return <div className="panel-empty">tool not found</div>;

  function onValidate() {
    setVerdict(null);
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(args) as Record<string, unknown>;
    } catch {
      setVerdict("arguments must be valid JSON");
      return;
    }
    validate.mutate(parsed);
  }

  return (
    <>
      <div className="inspector-title">{tool.name}</div>
      <Field label="CATEGORY" value={tool.category} />
      <Field label="PERMISSION" value={`L${tool.permission_level}`} />
      <Field label="REVERSIBILITY" value={tool.reversibility} />
      <Field label="TIMEOUT" value={`${tool.timeout}s`} />
      <Field label="NODE SCOPE" value={tool.node_scope} />
      <Field label="AUDIT" value={tool.audit ? "enabled" : "disabled"} />
      {tool.description ? <Field label="DESCRIPTION" value={tool.description} /> : null}

      <span className="inspector-group-title">INPUT SCHEMA</span>
      <pre className="inspector-code">{JSON.stringify(tool.input_schema, null, 2)}</pre>

      <div className="inspector-controls">
        <div className="inspector-group">
          <span className="inspector-group-title">VALIDATE ARGUMENTS</span>
          <textarea
            aria-label="Tool arguments"
            className="inspector-args"
            spellCheck={false}
            value={args}
            onChange={(event) => setArgs(event.target.value)}
          />
          <Button variant="ghost" size="sm" disabled={validate.isPending} onClick={onValidate}>
            {validate.isPending ? "Validating…" : "Validate"}
          </Button>
          {verdict ? <p className={`inspector-verdict${validate.isSuccess ? " ok" : ""}`}>{verdict}</p> : null}
        </div>
      </div>
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

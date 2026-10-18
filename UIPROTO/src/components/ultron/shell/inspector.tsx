import { useState } from "react";
import type { ReactNode } from "react";
import { useMutation } from "@tanstack/react-query";
import { Check, Pause, Play, RotateCw, Square, Trash2, X, Zap } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ApiError, toolsApi } from "@/lib/api";
import type { Agent, Schedule, ScheduleUpdate, Task, TaskPriority } from "@/lib/api";
import { broadcastUnauthorized } from "@/lib/auth";
import { useAgent, useAgentActions, useAgents, useDevice, useDevices, useNode, useNodes, useScheduleActions, useSchedules, useTask, useTaskActions, useTasks, useTaskSteps, useTool, useTools } from "@/lib/queries";
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

function NodeInspector({ id }: { id: string }) {
  const { data: detail } = useNode(id);
  const { data } = useNodes();
  const node = detail ?? (data ?? []).find((item) => item.node_id === id);
  if (!node) return <div className="panel-empty">node not found</div>;
  return (
    <>
      <div className="inspector-title">{node.name}</div>
      <Field label="STATUS" value={node.status} />
      <Field label="TYPE" value={node.node_type} />
      <Field label="VERSION" value={node.version} />
      <Field label="CAPABILITIES" value={node.capabilities.length ? node.capabilities.join(", ") : "—"} />
      <Field label="CLIENT OF" value={node.client_of ?? "—"} />
      <Field label="CONNECTED" value={node.connected_at ? timestamp(node.connected_at) : "—"} />
      <Field label="LAST SEEN" value={node.last_seen ? timestamp(node.last_seen) : "—"} />
      <Field label="NODE ID" value={node.node_id} />
    </>
  );
}

function DeviceInspector({ id }: { id: string }) {
  const { data: detail } = useDevice(id);
  const { data } = useDevices();
  const device = detail ?? (data ?? []).find((item) => item.device_id === id);
  if (!device) return <div className="panel-empty">device not found</div>;
  return (
    <>
      <div className="inspector-title">{device.name}</div>
      <Field label="STATUS" value={device.status} />
      <Field label="TYPE" value={device.device_type} />
      <Field label="FIRMWARE" value={device.firmware_version ?? "—"} />
      <Field label="CAPABILITIES" value={device.capabilities.length ? device.capabilities.join(", ") : "—"} />
      <Field label="CONNECTED" value={device.connected_at ? timestamp(device.connected_at) : "—"} />
      <Field label="LAST SEEN" value={device.last_seen ? timestamp(device.last_seen) : "—"} />
      <Field label="ID" value={device.device_id} />
    </>
  );
}

function FallbackInspector({ selection }: { selection: Selection }) {
  return <Field label={selection.kind.toUpperCase()} value={selection.id} />;
}

function ScheduleControls({ schedule }: { schedule: Schedule }) {
  const actions = useScheduleActions();
  const [name, setName] = useState(schedule.name);
  const [interval, setInterval] = useState(schedule.interval_seconds?.toString() ?? "");
  const [cron, setCron] = useState(schedule.cron_expression ?? "");
  const [runAt, setRunAt] = useState(schedule.run_at ?? "");

  const busy = Object.values(actions).some((mutation) => mutation.isPending);
  const armed = schedule.status === "active";
  const completed = schedule.status === "completed" || schedule.status === "failed";

  function save() {
    const body: ScheduleUpdate = {};
    const trimmed = name.trim();
    if (trimmed && trimmed !== schedule.name) body.name = trimmed;
    if (schedule.kind === "interval") {
      const seconds = Number(interval);
      if (interval.trim() && Number.isFinite(seconds) && seconds >= 1 && seconds !== schedule.interval_seconds) {
        body.interval_seconds = seconds;
      }
    }
    if (schedule.kind === "cron" && cron.trim() && cron.trim() !== schedule.cron_expression) {
      body.cron_expression = cron.trim();
    }
    if (schedule.kind === "once" && runAt.trim() && runAt.trim() !== schedule.run_at) {
      body.run_at = runAt.trim();
    }
    if (Object.keys(body).length) actions.update.mutate({ id: schedule.id, body });
  }

  return (
    <div className="inspector-controls">
      <div className="inspector-group">
        <span className="inspector-group-title">LIFECYCLE</span>
        <div className="inspector-buttons">
          {armed ? (
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => actions.pause.mutate(schedule.id)}>
              <Pause />
              Pause
            </Button>
          ) : null}
          {schedule.status === "paused" ? (
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => actions.resume.mutate(schedule.id)}>
              <Play />
              Resume
            </Button>
          ) : null}
          {!completed ? (
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => actions.fire.mutate(schedule.id)}>
              <Zap />
              Fire now
            </Button>
          ) : null}
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => actions.remove.mutate(schedule.id)}>
            <Trash2 />
            Delete
          </Button>
        </div>
      </div>

      <div className="inspector-group">
        <span className="inspector-group-title">DEFINITION</span>
        <input
          aria-label="Schedule name"
          value={name}
          disabled={busy}
          onChange={(event) => setName(event.target.value)}
        />
        {schedule.kind === "interval" ? (
          <div className="inspector-row">
            <input
              aria-label="Interval seconds"
              value={interval}
              disabled={busy}
              inputMode="numeric"
              onChange={(event) => setInterval(event.target.value)}
            />
            <Button variant="ghost" size="sm" disabled={busy} onClick={save}>
              Save
            </Button>
          </div>
        ) : null}
        {schedule.kind === "cron" ? (
          <div className="inspector-row">
            <input
              aria-label="Cron expression"
              value={cron}
              disabled={busy}
              onChange={(event) => setCron(event.target.value)}
            />
            <Button variant="ghost" size="sm" disabled={busy} onClick={save}>
              Save
            </Button>
          </div>
        ) : null}
        {schedule.kind === "once" ? (
          <div className="inspector-row">
            <input
              aria-label="Run at"
              value={runAt}
              disabled={busy}
              onChange={(event) => setRunAt(event.target.value)}
            />
            <Button variant="ghost" size="sm" disabled={busy} onClick={save}>
              Save
            </Button>
          </div>
        ) : null}
        {schedule.kind !== "interval" && schedule.kind !== "cron" && schedule.kind !== "once" ? (
          <Button variant="ghost" size="sm" disabled={busy} onClick={save}>
            Save name
          </Button>
        ) : null}
      </div>

      <div className="inspector-group">
        <span className="inspector-group-title">PAYLOAD</span>
        <pre className="inspector-code">{JSON.stringify(schedule.payload, null, 2)}</pre>
      </div>
    </div>
  );
}

function ScheduleInspector({ id }: { id: string }) {
  const { data } = useSchedules();
  const schedule = (data ?? []).find((item) => item.id === id);
  if (!schedule) return <div className="panel-empty">schedule not found</div>;
  return (
    <>
      <div className="inspector-title">{schedule.name}</div>
      <Field label="STATUS" value={schedule.status} />
      <Field label="KIND" value={schedule.kind} />
      <Field label="CRON" value={schedule.cron_expression ?? "—"} />
      <Field label="INTERVAL" value={schedule.interval_seconds ? `${schedule.interval_seconds}s` : "—"} />
      <Field label="RUN AT" value={schedule.run_at ? timestamp(schedule.run_at) : "—"} />
      <Field label="NEXT RUN" value={schedule.next_run_at ? timestamp(schedule.next_run_at) : "—"} />
      <Field label="LAST RUN" value={schedule.last_run_at ? timestamp(schedule.last_run_at) : "—"} />
      <Field label="LAST TASK" value={schedule.last_task_id ?? "—"} />
      <Field label="RUNS" value={`${schedule.run_count} · ${schedule.failure_count} failed`} />
      <Field label="LAST ERROR" value={schedule.last_error ?? "—"} />
      <Field label="ID" value={schedule.id} />
      <ScheduleControls key={`${schedule.id}:${schedule.status}:${schedule.next_run_at ?? ""}`} schedule={schedule} />
    </>
  );
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
      ) : selection.kind === "node" ? (
        <NodeInspector id={selection.id} />
      ) : selection.kind === "device" ? (
        <DeviceInspector id={selection.id} />
      ) : selection.kind === "schedule" ? (
        <ScheduleInspector id={selection.id} />
      ) : (
        <FallbackInspector selection={selection} />
      )}
      <Button variant="ghost" size="sm" onClick={onClose}>
        Close
      </Button>
    </aside>
  );
}

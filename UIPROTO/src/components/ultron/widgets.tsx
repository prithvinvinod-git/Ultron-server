/**
 * Live utility panels for the command center.
 *
 * Each panel is bound to a real endpoint: tasks to `/tasks`, the connection and
 * health panels to `/ready` + the event stream, capabilities to `/tools`, and the
 * event feed to `GET /events`. Ambient-only panels from the prototype (weather,
 * music, static notes) are gone: the console reports the system, it does not
 * impersonate one.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  Activity,
  Check,
  ChevronDown,
  ChevronRight,
  Cpu,
  Database,
  Plus,
  Radio,
  RotateCw,
  ShieldCheck,
  Trash2,
  Wrench,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { ApiError, tasksApi } from "@/lib/api";
import type { CheckResult, Task, TaskPriority } from "@/lib/api";
import { broadcastUnauthorized, useAuth } from "@/lib/auth";
import { useEventStream } from "@/lib/events";
import { queryKeys, useHealth, useTasks, useTools } from "@/lib/queries";
import { useWorkspace } from "@/lib/workspace";

export function PanelHeading({
  label,
  detail,
  action,
}: {
  label: string;
  detail?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="panel-heading">
      <h2>{label}</h2>
      {detail ? <span>{detail}</span> : null}
      {action}
    </div>
  );
}

function reportError(error: unknown): void {
  if (error instanceof ApiError && error.status === 401) {
    broadcastUnauthorized();
    return;
  }
  toast.error(error instanceof Error ? error.message : "request failed");
}

function statusTone(status: string): string {
  if (status === "ok") return "";
  if (status === "failed") return " failed";
  return " inactive";
}

function shortTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString("en-GB", { hour12: false });
}

// --- Connection + health -----------------------------------------------------

export function NetworkPanel() {
  const { state, info, events, dropped } = useEventStream();
  const health = useHealth();

  const ready = health.data?.readiness.ready ?? false;
  const host = typeof window === "undefined" ? "localhost" : window.location.host;

  return (
    <section className="hud-panel network-panel">
      <PanelHeading label="CONNECTION" detail={state.toUpperCase()} />
      <div className="network-main">
        <div className="icon-tile">
          <Radio />
        </div>
        <div>
          <strong>{host}</strong>
          <p>
            {info ? `stream ${info.stream_id.slice(0, 8)}` : "awaiting stream"}
            <span className={`status-dot${state === "open" ? "" : " inactive"}`} />
          </p>
        </div>
        <span className="signal-strength">
          {state === "open" ? "99" : "—"}
          <small>{state === "open" ? "%" : ""}</small>
        </span>
      </div>
      <div className="signal-meter">
        <span style={{ width: state === "open" ? "99%" : "20%" }} />
      </div>
      <div className="panel-foot">
        <span>{events.length} events · {dropped} dropped</span>
        <span className={ready ? "text-primary" : "text-muted-foreground"}>
          {ready ? "READY" : "DEGRADED"}
        </span>
      </div>
    </section>
  );
}

function CheckRow({ check }: { check: CheckResult }) {
  return (
    <div className="agent-row">
      <span className={`status-dot${statusTone(check.status)}`} />
      <div>
        <strong>{check.name}</strong>
        <small>{check.detail || (check.required ? "required" : "optional")}</small>
      </div>
      <span className="agent-pct">{check.latency_ms.toFixed(0)}ms</span>
    </div>
  );
}

export function HealthPanel() {
  const health = useHealth();
  const readiness = health.data?.readiness;
  const warnings = readiness?.warnings ?? [];

  return (
    <section className="hud-panel">
      <div className="panel-heading">
        <h2>SYSTEM HEALTH</h2>
        <ShieldCheck size={13} />
      </div>
      {readiness ? (
        <>
          <div className="agent-list">
            {readiness.checks.map((check) => (
              <CheckRow key={check.name} check={check} />
            ))}
          </div>
          {warnings.length ? <div className="panel-warn">{warnings.length} configuration warning(s)</div> : null}
          <div className="panel-foot">
            <span>{readiness.checks.length} checks</span>
            <span className={readiness.ready ? "text-primary" : "text-muted-foreground"}>
              {readiness.status.toUpperCase()} · {readiness.duration_ms.toFixed(0)}ms
            </span>
          </div>
        </>
      ) : (
        <div className="panel-empty">health unavailable</div>
      )}
    </section>
  );
}

// --- Tasks -------------------------------------------------------------------

const PRIORITIES: TaskPriority[] = ["low", "normal", "high", "urgent"];
const OPEN_STATUSES = new Set(["pending", "queued", "running", "blocked", "paused"]);

export function TaskPanel() {
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useTasks();
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState("");
  const [priority, setPriority] = useState<TaskPriority>("normal");

  const invalidate = () => queryClient.invalidateQueries({ queryKey: queryKeys.tasks });
  const create = useMutation({
    mutationFn: () => tasksApi.create(draft.trim(), priority),
    onSuccess: () => {
      setDraft("");
      setPriority("normal");
      setAdding(false);
      void invalidate();
    },
    onError: reportError,
  });
  const cancel = useMutation({ mutationFn: tasksApi.cancel, onSuccess: invalidate, onError: reportError });
  const retry = useMutation({ mutationFn: tasksApi.retry, onSuccess: invalidate, onError: reportError });
  const remove = useMutation({ mutationFn: tasksApi.remove, onSuccess: invalidate, onError: reportError });

  const tasks = data ?? [];
  const done = tasks.filter((task) => task.status === "completed").length;

  return (
    <section className="hud-panel">
      <PanelHeading label="MISSION QUEUE" detail={`${done}/${tasks.length}`} />
      {error ? <div className="panel-empty">tasks unavailable</div> : null}
      {isLoading && !data ? <div className="panel-empty">loading…</div> : null}
      {data && tasks.length === 0 ? <div className="panel-empty">queue empty</div> : null}

      <div className="task-list">
        {tasks.slice(0, 6).map((task) => (
          <TaskRow
            key={task.id}
            task={task}
            onCancel={() => cancel.mutate(task.id)}
            onRetry={() => retry.mutate(task.id)}
            onRemove={() => remove.mutate(task.id)}
          />
        ))}
      </div>

      {adding ? (
        <form
          className="task-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (!draft.trim()) return;
            create.mutate();
          }}
        >
          <input
            aria-label="New task"
            autoFocus
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="New mission…"
          />
          <select
            aria-label="Priority"
            value={priority}
            onChange={(event) => setPriority(event.target.value as TaskPriority)}
            className="agent-select"
          >
            {PRIORITIES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
          <Button variant="ghost" size="icon" type="submit" aria-label="Save task" disabled={create.isPending}>
            <Check />
          </Button>
          <Button variant="ghost" size="icon" type="button" aria-label="Cancel" onClick={() => setAdding(false)}>
            <X />
          </Button>
        </form>
      ) : (
        <Button variant="ghost" className="add-task" onClick={() => setAdding(true)}>
          <Plus />
          Add task
        </Button>
      )}
    </section>
  );
}

function TaskRow({
  task,
  onCancel,
  onRetry,
  onRemove,
}: {
  task: Task;
  onCancel: () => void;
  onRetry: () => void;
  onRemove: () => void;
}) {
  const [open, setOpen] = useState(false);
  const { select } = useWorkspace();
  const isOpen = OPEN_STATUSES.has(task.status);
  return (
    <div className="task-item">
      <Button
        variant="ghost"
        className={`task-row ${task.status === "completed" ? "task-done" : ""}`}
        onClick={() => {
          setOpen((value) => {
            if (!value) select({ kind: "task", id: task.id });
            return !value;
          });
        }}
        aria-expanded={open}
      >
        <span className="task-check">{task.status === "completed" ? <Check size={11} /> : null}</span>
        <span className="task-goal">{task.goal}</span>
        <span className={`task-status status-${task.status}`}>{task.status}</span>
        <ChevronDown size={12} className={open ? "task-chevron open" : "task-chevron"} />
      </Button>
      {open ? (
        <div className="task-actions">
          <span className="task-meta">
            {task.priority} · {shortTime(task.created_at)} · retries {task.retry_count}/{task.max_retries}
          </span>
          {isOpen ? (
            <Button variant="ghost" size="sm" onClick={onCancel}>
              Cancel
            </Button>
          ) : null}
          {task.status === "failed" ? (
            <Button variant="ghost" size="sm" onClick={onRetry}>
              <RotateCw />
              Retry
            </Button>
          ) : null}
          {!isOpen ? (
            <Button variant="ghost" size="sm" onClick={onRemove}>
              <Trash2 />
              Delete
            </Button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

// --- Capabilities (tools) ----------------------------------------------------

export function ToolsPanel() {
  const { data, isLoading } = useTools();
  const { select } = useWorkspace();
  const tools = data ?? [];
  return (
    <section className="hud-panel">
      <PanelHeading label="CAPABILITIES" detail={`${tools.length}`} action={<Wrench size={13} />} />
      {isLoading && !data ? <div className="panel-empty">loading…</div> : null}
      {data && tools.length === 0 ? <div className="panel-empty">no tools registered</div> : null}
      <div className="task-list">
        {tools.slice(0, 6).map((tool) => (
          <button
            key={tool.name}
            type="button"
            className="agent-row agent-button"
            onClick={() => select({ kind: "tool", id: tool.name })}
          >
            <span className="agent-dot" data-on />
            <div>
              <strong>{tool.name}</strong>
              <small>{tool.category} · L{tool.permission_level}</small>
            </div>
            <span className="agent-pct">{tool.audit ? "AUDIT" : ""}</span>
          </button>
        ))}
      </div>
      <div className="panel-foot">
        <span>{tools.length} tools</span>
        <span>REGISTRY</span>
      </div>
    </section>
  );
}

// --- Live event feed ---------------------------------------------------------

export function EventsPanel() {
  const { events, state } = useEventStream();
  const recent = events.slice(0, 8);
  return (
    <section className="hud-panel activity-panel">
      <PanelHeading
        label="EVENT STREAM"
        detail={state.toUpperCase()}
        action={<Activity size={13} />}
      />
      {recent.length === 0 ? (
        <div className="panel-empty">no events yet</div>
      ) : (
        <div className="event-list">
          {recent.map((event) => (
            <div key={`${event.id}-${event.event_type}`} className="event-row">
              <span className="event-type">{event.event_type}</span>
              <small>{event.topic}</small>
              <span className="event-time">{shortTime(event.occurred_at)}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

// --- Session ------------------------------------------------------------------

export function SessionPanel() {
  const { principal } = useAuth();
  const { state, info, events } = useEventStream();
  return (
    <section className="hud-panel">
      <PanelHeading label="SESSION" detail={state.toUpperCase()} action={<Cpu size={13} />} />
      <div className="agent-row">
        <span className="status-dot" />
        <div>
          <strong>{principal?.username ?? principal?.actor_type ?? "unknown"}</strong>
          <small>{principal?.is_superuser ? "superuser" : "operator"}</small>
        </div>
        <span className="agent-pct">{events.length}</span>
      </div>
      <div className="agent-row">
        <span className="status-dot" />
        <div>
          <strong>event bus</strong>
          <small>{info ? `hb ${info.heartbeat_seconds}s · ${info.topics.length} topics` : "idle"}</small>
        </div>
        <span className="agent-pct">{state === "open" ? "LIVE" : "—"}</span>
      </div>
      <div className="panel-foot">
        <span>events received</span>
        <span className="text-primary">{events.length}</span>
      </div>
    </section>
  );
}

// Retained for the dock dialogs in index.tsx.
export function QuickLinks({ onOpen }: { onOpen: (tool: string) => void }) {
  const links = ["Documents", "Workspace", "System terminal", "Core settings"];
  return (
    <section className="hud-panel">
      <PanelHeading label="QUICK ACCESS" action={<Database size={13} />} />
      <div className="quick-links">
        {links.map((label, index) => (
          <Button variant="ghost" key={label} onClick={() => onOpen(label)}>
            <span className="quick-index">0{index + 1}</span>
            <span>{label}</span>
            <ChevronRight />
          </Button>
        ))}
      </div>
    </section>
  );
}

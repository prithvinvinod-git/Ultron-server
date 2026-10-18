/**
 * Schedule surfaces (T511): the §44 scheduler over the console.
 *
 * The list reads the `/schedules` collection; every verb the server exposes —
 * create, pause, resume, fire, edit, delete — is reachable from here, with the
 * destructive or one-shot actions in the inspector where they cannot be hit by
 * accident. A paused or completed schedule still appears, because an operator
 * looking for "what stopped running" needs the row, not just the live ones.
 */

import { useState } from "react";
import { CalendarClock } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { Schedule, ScheduleCreate, ScheduleKind } from "@/lib/api";
import { useScheduleActions, useSchedules } from "@/lib/queries";
import { useWorkspace } from "@/lib/workspace";

const KINDS: ScheduleKind[] = ["interval", "cron", "once", "event"];

function scheduleTone(status: Schedule["status"]): string {
  if (status === "active") return "";
  if (status === "paused") return " inactive";
  return " failed";
}

function timingSummary(schedule: Schedule): string {
  if (schedule.kind === "interval" && schedule.interval_seconds) {
    return `every ${schedule.interval_seconds}s`;
  }
  if (schedule.kind === "cron" && schedule.cron_expression) {
    return schedule.cron_expression;
  }
  if (schedule.kind === "once" && schedule.run_at) {
    return new Date(schedule.run_at).toLocaleString("en-GB", { hour12: false });
  }
  return schedule.kind;
}

function CreateScheduleForm({ onDone }: { onDone: () => void }) {
  const { create } = useScheduleActions();
  const [name, setName] = useState("");
  const [kind, setKind] = useState<ScheduleKind>("interval");
  const [interval, setInterval] = useState("3600");
  const [cron, setCron] = useState("");
  const [runAt, setRunAt] = useState("");

  const busy = create.isPending;
  const invalidName = name.trim().length === 0;
  const invalidTiming =
    (kind === "interval" && (!(Number(interval) >= 1) || interval.trim() === "")) ||
    (kind === "cron" && cron.trim().length === 0) ||
    (kind === "once" && runAt.trim().length === 0);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (invalidName || invalidTiming) return;
    const body: ScheduleCreate = { name: name.trim(), kind, payload: {} };
    if (kind === "interval") body.interval_seconds = Number(interval);
    if (kind === "cron") body.cron_expression = cron.trim();
    if (kind === "once") body.run_at = runAt.trim();
    create.mutate(body, { onSuccess: onDone });
  }

  return (
    <form className="shell-form" onSubmit={submit}>
      <div className="form-field">
        <Label htmlFor="schedule-name">Name</Label>
        <Input id="schedule-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="nightly-report" />
      </div>
      <div className="form-field">
        <Label htmlFor="schedule-kind">Kind</Label>
        <select
          id="schedule-kind"
          className="agent-select"
          value={kind}
          onChange={(event) => setKind(event.target.value as ScheduleKind)}
        >
          {KINDS.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      </div>
      {kind === "interval" ? (
        <div className="form-field">
          <Label htmlFor="schedule-interval">Interval (seconds)</Label>
          <Input
            id="schedule-interval"
            value={interval}
            onChange={(event) => setInterval(event.target.value)}
            inputMode="numeric"
          />
        </div>
      ) : null}
      {kind === "cron" ? (
        <div className="form-field">
          <Label htmlFor="schedule-cron">Cron expression</Label>
          <Input id="schedule-cron" value={cron} onChange={(event) => setCron(event.target.value)} placeholder="0 3 * * *" />
        </div>
      ) : null}
      {kind === "once" ? (
        <div className="form-field">
          <Label htmlFor="schedule-runat">Run at (UTC, ISO-8601)</Label>
          <Input id="schedule-runat" value={runAt} onChange={(event) => setRunAt(event.target.value)} placeholder="2026-01-01T03:00:00Z" />
        </div>
      ) : null}
      <div className="form-actions">
        <Button type="submit" size="sm" disabled={busy || invalidName || invalidTiming}>
          {busy ? "Creating…" : "Create"}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

export function SchedulesPanel() {
  const { data, isLoading, error } = useSchedules();
  const { select } = useWorkspace();
  const [creating, setCreating] = useState(false);
  const schedules = data ?? [];
  const armed = schedules.filter((schedule) => schedule.status === "active").length;

  return (
    <section className="hud-panel">
      <div className="panel-heading">
        <h2>SCHEDULES</h2>
        <span>
          {armed}/{schedules.length} ARMED
        </span>
        <CalendarClock size={13} />
      </div>
      {error ? <div className="panel-empty">schedules unavailable</div> : null}
      {isLoading && !data ? <div className="panel-empty">loading…</div> : null}
      {data && schedules.length === 0 ? <div className="panel-empty">no schedules</div> : null}

      <div className="agent-list">
        {schedules.map((schedule) => (
          <button
            key={schedule.id}
            type="button"
            className="agent-row agent-button"
            onClick={() => select({ kind: "schedule", id: schedule.id })}
          >
            <span className={`status-dot${scheduleTone(schedule.status)}`} />
            <div>
              <strong>{schedule.name}</strong>
              <small>
                {schedule.status} · {timingSummary(schedule)} · {schedule.run_count} runs
                {schedule.failure_count ? ` · ${schedule.failure_count} failed` : ""}
              </small>
            </div>
            <span className="agent-pct">
              {schedule.next_run_at
                ? new Date(schedule.next_run_at).toLocaleTimeString("en-GB", { hour12: false })
                : "—"}
            </span>
          </button>
        ))}
      </div>

      {creating ? (
        <CreateScheduleForm onDone={() => setCreating(false)} />
      ) : (
        <div className="panel-foot">
          <Button variant="ghost" size="sm" onClick={() => setCreating(true)}>
            New schedule
          </Button>
          <span>§44</span>
        </div>
      )}
    </section>
  );
}

export { scheduleTone, timingSummary };

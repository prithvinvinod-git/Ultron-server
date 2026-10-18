/**
 * Window lifecycle state machine (T520, §69.6).
 *
 * Maps backend health + agent/task activity to the §69.6 state set:
 *
 *   starting → disconnected → connected → idle → agent-active → task-executing
 *         ↑         ↓            ↓
 *        error  ←────┴────────────┘
 *         ↓
 *   shutting-down
 *
 * State derivation:
 * - `starting`   — before the first health poll completes
 * - `disconnected`— backend unreachable (poll failed)
 * - `connected`  — backend reachable, no agent/task activity reported yet
 * - `idle`       — backend reachable, agents exist but none active
 * - `agent-active`— backend reachable, ≥1 agent in an active state
 * - `task-executing` — backend reachable, ≥1 task running (supersedes agent-active)
 * - `error`      — backend unreachable after repeated failures (3+)
 * - `shutting-down` — app quit initiated
 *
 * The renderer reports agent/task activity counts through
 * `lifecycle:report`; the main process polls backend health directly.
 */
import { setInterval, clearInterval } from "node:timers";

export type LifecycleState =
  | "starting"
  | "disconnected"
  | "connected"
  | "idle"
  | "agent-active"
  | "task-executing"
  | "error"
  | "shutting-down";

export interface LifecycleInfo {
  state: LifecycleState;
  agentCount: number;
  taskCount: number;
  consecutiveFailures: number;
}

export interface LifecycleOptions {
  backendUrl: string;
  pollMs?: number;
  onState: (state: LifecycleState) => void;
  onError: (message: string) => void;
}

const POLL_MS = 10_000;
const MAX_FAILURES = 3;

export class LifecycleTracker {
  private state: LifecycleState = "starting";
  private agentCount = 0;
  private taskCount = 0;
  private consecutiveFailures = 0;
  private pollTimer: ReturnType<typeof setInterval> | null = null;
  private readonly opts: LifecycleOptions;

  constructor(opts: LifecycleOptions) {
    this.opts = { pollMs: POLL_MS, ...opts };
  }

  get current(): LifecycleState {
    return this.state;
  }

  get info(): LifecycleInfo {
    return {
      state: this.state,
      agentCount: this.agentCount,
      taskCount: this.taskCount,
      consecutiveFailures: this.consecutiveFailures,
    };
  }

  start(): void {
    void this.poll();
    this.pollTimer = setInterval(() => void this.poll(), this.opts.pollMs ?? POLL_MS);
  }

  stop(): void {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  }

  shutdown(): void {
    this.stop();
    this.transition("shutting-down");
  }

  reportActivity(agentCount: number, taskCount: number): void {
    this.agentCount = agentCount;
    this.taskCount = taskCount;
    this.reevaluate();
  }

  private async poll(): Promise<void> {
    try {
      const res = await fetch(`${this.opts.backendUrl}/health`, {
        signal: AbortSignal.timeout(5_000),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      this.consecutiveFailures = 0;
      this.reevaluate();
    } catch (err) {
      this.consecutiveFailures += 1;
      if (this.consecutiveFailures >= MAX_FAILURES) {
        this.transition("error");
      } else {
        this.transition("disconnected");
      }
      this.opts.onError(
        `health check failed (${this.consecutiveFailures}/${MAX_FAILURES}): ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }

  private reevaluate(): void {
    if (this.consecutiveFailures > 0) return;
    if (this.taskCount > 0) {
      this.transition("task-executing");
    } else if (this.agentCount > 0) {
      this.transition("agent-active");
    } else if (this.state === "starting" || this.state === "disconnected" || this.state === "error") {
      this.transition("connected");
    } else {
      this.transition("idle");
    }
  }

  private transition(next: LifecycleState): void {
    if (next === this.state) return;
    this.state = next;
    this.opts.onState(next);
  }
}

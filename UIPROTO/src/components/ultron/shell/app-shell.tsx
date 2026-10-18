import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Bot, Cpu, LogOut, Wifi } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";
import { useEventStream } from "@/lib/events";
import { useAgents, useHealth, useTasks } from "@/lib/queries";
import { TABS, useWorkspace } from "@/lib/workspace";
import { SelectionInspector } from "@/components/ultron/shell/inspector";

const ACTIVE_AGENT_STATUSES = new Set(["created", "initializing", "ready", "running", "waiting", "verifying"]);
const OPEN_TASK_STATUSES = new Set(["pending", "queued", "running", "blocked", "paused"]);

/**
 * Fixed-viewport application shell: command header, left tab rail, workspace,
 * right inspector, and system status bar. The workspace never scrolls as a
 * whole — overflow is contained by the active panel region.
 */
export function AppShell({ children }: { children: ReactNode }) {
  const { tab, setTab, selection, select } = useWorkspace();
  const { state: streamState } = useEventStream();
  const health = useHealth();
  const agents = useAgents();
  const tasks = useTasks();
  const { principal, logout } = useAuth();
  const [clock, setClock] = useState("--:--:--");

  useEffect(() => {
    const update = () => setClock(new Date().toLocaleTimeString("en-GB", { hour12: false }));
    update();
    const timer = setInterval(update, 1000);
    return () => clearInterval(timer);
  }, []);

  const readiness = health.data?.readiness;
  const online = readiness?.ready ?? streamState === "open";
  const activeAgents = (agents.data ?? []).filter(agent => ACTIVE_AGENT_STATUSES.has(agent.status)).length;
  const openTasks = (tasks.data ?? []).filter(task => OPEN_TASK_STATUSES.has(task.status)).length;

  return (
    <main className="ultron-shell">
      <section className="command-header">
        <div className="command-title">
          <h2>Command Center</h2>
          <span>YOUR SYSTEM. IN SYNC.</span>
        </div>
        <div className="session-status">
          <span className={`status-dot ${online ? "" : "inactive"}`} />
          {online ? "SYSTEM OPERATIONAL" : readiness ? "SYSTEM DEGRADED" : "SYSTEM STANDBY"}
          <span> / </span>
          {principal?.username ?? "SESSION"} · {clock}
          <Button variant="ghost" size="icon" title="Sign out" aria-label="Sign out" onClick={() => void logout()}>
            <LogOut />
          </Button>
        </div>
      </section>

      <div className="shell-body">
        <nav className="shell-rail" aria-label="Workspace sections">
          {TABS.map(({ id, label, icon: Icon }) => (
            <Button
              key={id}
              variant="ghost"
              size="icon"
              title={label}
              aria-label={label}
              aria-pressed={tab === id}
              onClick={() => setTab(id)}
            >
              <Icon />
            </Button>
          ))}
        </nav>

        <div className="shell-workspace">{children}</div>

        {selection && <SelectionInspector selection={selection} onClose={() => select(null)} />}
      </div>

      <footer className="bottom-bar">
        <div className="bottom-brand"><Cpu />ULTRON OS <span>LIVE</span></div>
        <div className="bottom-system">
          <span><Wifi /><b>{streamState === "open" ? "LIVE" : "—"}</b></span>
          <span><Bot /><b>{activeAgents}↑</b></span>
          <span><b>{openTasks}</b> OPEN</span>
          <span><span className={`status-dot${online ? "" : " inactive"}`} />{online ? "CONNECTED" : "STANDBY"}</span>
        </div>
      </footer>
    </main>
  );
}

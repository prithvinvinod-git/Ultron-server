import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Bot, Cpu, Orbit, Wifi } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useEventStream } from "@/lib/events";
import { useAgents, useHealth, useTasks } from "@/lib/queries";
import { TABS, useWorkspace } from "@/lib/workspace";
import { SelectionInspector } from "@/components/ultron/shell/inspector";
import { Orb } from "@/components/ultron/chat/orb-states";

const ACTIVE_AGENT_STATUSES = new Set(["created", "initializing", "ready", "running", "waiting", "verifying"]);
const OPEN_TASK_STATUSES = new Set(["pending", "queued", "running", "blocked", "paused"]);

/**
 * The Electron shell already renders this orb as a separate always-on-top
 * desktop widget (`clients/electron/orb`, using the same `thinking-orbs`
 * engine). Rendering it in the command header too would put two copies of the
 * same orb on screen, so the in-window orb is hidden when the preload bridge is
 * present. The browser console keeps it.
 */
const DESKTOP_SHELL =
  typeof window !== "undefined" &&
  (window as unknown as { ultron?: unknown }).ultron !== undefined;

/** The slice of the preload bridge this file uses. */
interface OrbBridge {
  getOrbState?: () => Promise<{ visible?: boolean }>;
  setOrbVisible?: (visible: boolean) => Promise<{ visible?: boolean }>;
  onOrbVisibility?: (listener: (state: { visible?: boolean }) => void) => () => void;
}

function orbBridge(): OrbBridge | undefined {
  return (window as unknown as { ultron?: OrbBridge }).ultron;
}

/**
 * Show/hide the floating orb window. It hides itself from its own context menu,
 * so this is the way back once it is gone. Only rendered in the desktop shell —
 * the browser has no orb window to show.
 */
function OrbToggle() {
  const [visible, setVisible] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const bridge = orbBridge();
    if (bridge === undefined) return;
    let cancelled = false;
    void bridge.getOrbState?.()
      .then((state) => {
        if (!cancelled) setVisible(state.visible === true);
      })
      .catch(() => {
        // Leave the label at its optimistic default; the toggle still works.
      });
    const off = bridge.onOrbVisibility?.((state) => {
      if (!cancelled) setVisible(state.visible === true);
    });
    return () => {
      cancelled = true;
      off?.();
    };
  }, []);

  const shown = visible !== false;

  async function toggle() {
    const bridge = orbBridge();
    if (bridge?.setOrbVisible === undefined || busy) return;
    setBusy(true);
    try {
      const result = await bridge.setOrbVisible(!shown);
      setVisible(result.visible === true);
    } catch {
      // The main process refuses invalid calls; keep the current label.
    } finally {
      setBusy(false);
    }
  }

  return (
    <Button
      variant="ghost"
      className="orb-toggle"
      data-visible={shown}
      aria-pressed={shown}
      title={shown ? "Hide the floating ULTRON orb" : "Show the floating ULTRON orb"}
      onClick={() => { void toggle(); }}
      disabled={busy}
    >
      <Orbit />
      {shown ? "Hide orb" : "Show orb"}
    </Button>
  );
}

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
  const principal = { username: "admin prithvin" };
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
        {!DESKTOP_SHELL && (
          <div className="orb-container">
            <Orb />
          </div>
        )}
        <div className="command-title">
          <h2>Command Center</h2>
          <span>YOUR SYSTEM. IN SYNC.</span>
          {DESKTOP_SHELL && <OrbToggle />}
        </div>
        <div className="session-status">
          <span className={`status-dot ${online ? "" : "inactive"}`} />
          {online ? "SYSTEM OPERATIONAL" : readiness ? "SYSTEM DEGRADED" : "SYSTEM STANDBY"}
          <span> / </span>
          {principal?.username ?? "SESSION"} · {clock}
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

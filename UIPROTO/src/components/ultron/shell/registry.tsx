import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { Activity, Bot, Cpu, Radio, ShieldCheck, Terminal, Wrench } from "lucide-react";

import { AgentsPanel } from "@/components/ultron/agents";
import { DevicesPanel, FleetPanel } from "@/components/ultron/fleet";
import {
  EventsPanel,
  HealthPanel,
  NetworkPanel,
  SessionPanel,
  TaskPanel,
  ToolsPanel,
} from "@/components/ultron/widgets";
import type { TabId } from "@/lib/workspace";

/**
 * A panel is the unit of capability in the command center. Adding a new
 * surface to the console means adding one entry here — the shell, the tabs and
 * the inspector never change. `tab` decides where it lands, `order` decides
 * its position, and `span: 2` lets it take two grid columns.
 */
export interface PanelDef {
  id: string;
  title: string;
  icon: LucideIcon;
  tab: TabId;
  order: number;
  span?: 1 | 2;
  render: () => ReactNode;
}

export const PANELS: PanelDef[] = [
  { id: "tasks", title: "Mission queue", icon: Activity, tab: "tasks", order: 10, render: () => <TaskPanel /> },
  { id: "agents", title: "Agents", icon: Bot, tab: "agents", order: 10, render: () => <AgentsPanel /> },
  { id: "tools-agents", title: "Capabilities", icon: Wrench, tab: "agents", order: 20, render: () => <ToolsPanel /> },
  { id: "nodes", title: "Node fleet", icon: Radio, tab: "fleet", order: 10, render: () => <FleetPanel /> },
  { id: "devices", title: "Devices", icon: Cpu, tab: "fleet", order: 20, render: () => <DevicesPanel /> },
  { id: "network", title: "Connection", icon: Radio, tab: "system", order: 10, render: () => <NetworkPanel /> },
  { id: "health", title: "System health", icon: ShieldCheck, tab: "system", order: 20, render: () => <HealthPanel /> },
  { id: "session", title: "Session", icon: Terminal, tab: "system", order: 30, render: () => <SessionPanel /> },
  { id: "tools-system", title: "Capabilities", icon: Wrench, tab: "system", order: 40, render: () => <ToolsPanel /> },
  { id: "events", title: "Event stream", icon: Activity, tab: "system", order: 50, render: () => <EventsPanel /> },
];

export function panelsForTab(tab: TabId): PanelDef[] {
  return PANELS.filter((panel) => panel.tab === tab).sort((a, b) => a.order - b.order);
}

/** Renders the registry panels registered for a tab. */
export function PanelGrid({ tab }: { tab: TabId }) {
  return (
    <div className="shell-panels">
      {panelsForTab(tab).map((panel) => (
        <div key={panel.id} className={panel.span === 2 ? "panel-wide" : undefined}>
          {panel.render()}
        </div>
      ))}
    </div>
  );
}

import { createContext, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Activity, Bell, Bot, CalendarClock, Grid2X2, ListChecks, Radio, Settings } from "lucide-react";
import type { LucideIcon } from "lucide-react";

/** Top-level workspace tabs rendered in the left rail. */
export type TabId = "command" | "agents" | "tasks" | "fleet" | "schedules" | "notifications" | "system" | "settings";

/** What the inspector sidebar can describe. */
export type SelectionKind = "agent" | "task" | "tool" | "node" | "device" | "schedule";

export interface Selection {
  kind: SelectionKind;
  id: string;
}

export interface TabDef {
  id: TabId;
  label: string;
  icon: LucideIcon;
}

export const TABS: TabDef[] = [
  { id: "command", label: "Command center", icon: Grid2X2 },
  { id: "agents", label: "Agents", icon: Bot },
  { id: "tasks", label: "Tasks", icon: ListChecks },
  { id: "fleet", label: "Fleet", icon: Radio },
  { id: "schedules", label: "Schedules", icon: CalendarClock },
  { id: "notifications", label: "Notifications", icon: Bell },
  { id: "system", label: "System", icon: Activity },
  { id: "settings", label: "Settings", icon: Settings },
];

interface WorkspaceValue {
  tab: TabId;
  setTab: (tab: TabId) => void;
  selection: Selection | null;
  select: (selection: Selection | null) => void;
}

const WorkspaceContext = createContext<WorkspaceValue | null>(null);

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [tab, setTab] = useState<TabId>("command");
  const [selection, setSelection] = useState<Selection | null>(null);
  const value = useMemo(
    () => ({ tab, setTab, selection, select: setSelection }),
    [tab, selection],
  );
  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace(): WorkspaceValue {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error("useWorkspace must be used within a WorkspaceProvider");
  return value;
}

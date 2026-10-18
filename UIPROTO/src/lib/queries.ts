/**
 * TanStack Query bindings for the ULTRON resources, plus live invalidation.
 *
 * Lists are polled slowly as a floor (so a console left open still converges even
 * if the event stream is down), and invalidated immediately when a matching event
 * arrives on `/events`. The two together mean the console is fresh through the
 * event bus and merely *eventually* fresh if the bus is unavailable.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { agentsApi, devicesApi, healthApi, metricsApi, nodesApi, notificationsApi, schedulesApi, tasksApi, toolsApi } from "./api";
import { onEnvelope } from "./events";

export const queryKeys = {
  agents: ["agents"] as const,
  tasks: ["tasks"] as const,
  tools: ["tools"] as const,
  health: ["health"] as const,
  nodes: ["nodes"] as const,
  devices: ["devices"] as const,
  schedules: ["schedules"] as const,
  notifications: ["notifications"] as const,
  metrics: ["metrics"] as const,
  steps: (taskId: string) => ["tasks", taskId, "steps"] as const,
};

export function useAgents() {
  return useQuery({
    queryKey: queryKeys.agents,
    queryFn: agentsApi.list,
    refetchInterval: 15000,
  });
}

export function useTasks() {
  return useQuery({
    queryKey: queryKeys.tasks,
    queryFn: tasksApi.list,
    refetchInterval: 15000,
  });
}

export function useTools() {
  return useQuery({
    queryKey: queryKeys.tools,
    queryFn: toolsApi.list,
    staleTime: 60000,
  });
}

export interface HealthSnapshot {
  liveness: Awaited<ReturnType<typeof healthApi.liveness>>;
  readiness: Awaited<ReturnType<typeof healthApi.readiness>>;
}

export function useHealth() {
  return useQuery<HealthSnapshot>({
    queryKey: queryKeys.health,
    queryFn: async () => ({
      liveness: await healthApi.liveness(),
      readiness: await healthApi.readiness(),
    }),
    refetchInterval: 10000,
    retry: false,
  });
}

export function useNodes() {
  return useQuery({
    queryKey: queryKeys.nodes,
    queryFn: nodesApi.list,
    refetchInterval: 15000,
  });
}

export function useDevices() {
  return useQuery({
    queryKey: queryKeys.devices,
    queryFn: devicesApi.list,
    refetchInterval: 15000,
  });
}

export function useSchedules() {
  return useQuery({
    queryKey: queryKeys.schedules,
    queryFn: schedulesApi.list,
    refetchInterval: 15000,
  });
}

export function useNotifications() {
  return useQuery({
    queryKey: queryKeys.notifications,
    queryFn: notificationsApi.list,
    refetchInterval: 15000,
  });
}

export function useMetrics() {
  return useQuery({
    queryKey: queryKeys.metrics,
    queryFn: metricsApi.text,
    refetchInterval: 15000,
  });
}

export function useTaskSteps(taskId: string | null) {
  return useQuery({
    queryKey: queryKeys.steps(taskId ?? ""),
    queryFn: () => tasksApi.steps(taskId as string),
    enabled: Boolean(taskId),
  });
}

/** Invalidate the right list when the event bus reports work on it. */
export function useLiveInvalidation(): void {
  const queryClient = useQueryClient();
  useEffect(
    () =>
      onEnvelope((event) => {
        if (event.event_type.startsWith("AGENT_")) {
          void queryClient.invalidateQueries({ queryKey: queryKeys.agents });
        } else if (event.event_type.startsWith("TASK_")) {
          void queryClient.invalidateQueries({ queryKey: queryKeys.tasks });
        } else if (event.event_type.startsWith("SCHEDULE")) {
          void queryClient.invalidateQueries({ queryKey: queryKeys.schedules });
        } else if (event.event_type.startsWith("NOTIFICATION")) {
          void queryClient.invalidateQueries({ queryKey: queryKeys.notifications });
        } else if (event.event_type.startsWith("NODE")) {
          void queryClient.invalidateQueries({ queryKey: queryKeys.nodes });
        } else if (event.event_type.startsWith("DEVICE")) {
          void queryClient.invalidateQueries({ queryKey: queryKeys.devices });
        }
      }),
    [queryClient],
  );
}

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

import { agentsApi, healthApi, tasksApi, toolsApi } from "./api";
import { onEnvelope } from "./events";

export const queryKeys = {
  agents: ["agents"] as const,
  tasks: ["tasks"] as const,
  tools: ["tools"] as const,
  health: ["health"] as const,
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
        }
      }),
    [queryClient],
  );
}

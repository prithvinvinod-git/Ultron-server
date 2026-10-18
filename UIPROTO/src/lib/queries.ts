/**
 * TanStack Query bindings for the ULTRON resources, plus live invalidation.
 *
 * Lists are polled slowly as a floor (so a console left open still converges even
 * if the event stream is down), and invalidated immediately when a matching event
 * arrives on `/events`. The two together mean the console is fresh through the
 * event bus and merely *eventually* fresh if the bus is unavailable.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { toast } from "sonner";

import { agentsApi, devicesApi, healthApi, metricsApi, nodesApi, notificationsApi, schedulesApi, tasksApi, toolsApi, ApiError } from "./api";
import { broadcastUnauthorized } from "./auth";
import { onEnvelope } from "./events";

export const queryKeys = {
  agents: ["agents"] as const,
  agent: (id: string) => ["agents", id] as const,
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

export function useAgent(id: string | null) {
  return useQuery({
    queryKey: queryKeys.agent(id ?? ""),
    queryFn: () => agentsApi.get(id as string),
    enabled: Boolean(id),
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

/** Surface a failed mutation: bounce to the gate on 401, else toast the message. */
export function reportMutationError(error: unknown): void {
  if (error instanceof ApiError && error.status === 401) {
    broadcastUnauthorized();
    return;
  }
  toast.error(error instanceof Error ? error.message : "request failed");
}

/**
 * Every lifecycle and assignment mutation for one agent (T504).
 *
 * Each mutation invalidates the agent list so the fleet panel and the inspector
 * both reflect the server's answer; the caller disables controls while
 * `isPending`. `onError` routes 401s to the login gate.
 */
export function useAgentActions(agentId: string) {
  const queryClient = useQueryClient();
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.agents });
  };
  const ok = (message: string) => () => {
    toast.success(message);
    refresh();
  };
  const options = { onError: reportMutationError };
  return {
    pause: useMutation({ ...options, mutationFn: () => agentsApi.pause(agentId), onSuccess: ok("Agent paused") }),
    resume: useMutation({ ...options, mutationFn: () => agentsApi.resume(agentId), onSuccess: ok("Agent resumed") }),
    complete: useMutation({ ...options, mutationFn: () => agentsApi.complete(agentId), onSuccess: ok("Agent completed") }),
    cancel: useMutation({ ...options, mutationFn: () => agentsApi.cancel(agentId), onSuccess: ok("Agent cancelled") }),
    fail: useMutation({
      ...options,
      mutationFn: (error: string) => agentsApi.fail(agentId, error),
      onSuccess: ok("Agent failed"),
    }),
    assignTask: useMutation({
      ...options,
      mutationFn: (taskId: string) => agentsApi.assignTask(agentId, taskId),
      onSuccess: ok("Task assigned"),
    }),
    assignModel: useMutation({
      ...options,
      mutationFn: (model: string | null) => agentsApi.assignModel(agentId, model),
      onSuccess: ok("Model assigned"),
    }),
    assignTools: useMutation({
      ...options,
      mutationFn: (tools: string[]) => agentsApi.assignTools(agentId, tools),
      onSuccess: ok("Tools assigned"),
    }),
    assignPermissions: useMutation({
      ...options,
      mutationFn: (permissions: string[]) => agentsApi.assignPermissions(agentId, permissions),
      onSuccess: ok("Permissions assigned"),
    }),
  };
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

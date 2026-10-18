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
import type { ScheduleCreate, ScheduleUpdate, StepCreate, TaskPriority } from "./api";
import { broadcastUnauthorized } from "./auth";
import { onEnvelope } from "./events";

export const queryKeys = {
  agents: ["agents"] as const,
  agent: (id: string) => ["agents", id] as const,
  tasks: ["tasks"] as const,
  task: (id: string) => ["tasks", id] as const,
  tools: ["tools"] as const,
  tool: (name: string) => ["tools", name] as const,
  health: ["health"] as const,
  nodes: ["nodes"] as const,
  node: (id: string) => ["nodes", id] as const,
  devices: ["devices"] as const,
  device: (id: string) => ["devices", id] as const,
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

export function useTask(id: string | null) {
  return useQuery({
    queryKey: queryKeys.task(id ?? ""),
    queryFn: () => tasksApi.get(id as string),
    enabled: Boolean(id),
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

export function useTool(name: string | null) {
  return useQuery({
    queryKey: queryKeys.tool(name ?? ""),
    queryFn: () => toolsApi.get(name as string),
    enabled: Boolean(name),
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

export function useNode(id: string | null) {
  return useQuery({
    queryKey: queryKeys.node(id ?? ""),
    queryFn: () => nodesApi.get(id as string),
    enabled: Boolean(id),
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

export function useDevice(id: string | null) {
  return useQuery({
    queryKey: queryKeys.device(id ?? ""),
    queryFn: () => devicesApi.get(id as string),
    enabled: Boolean(id),
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

/**
 * Every mutation the task inspector offers (T505). Each one refreshes both the
 * queue and the open task's step list.
 */
export function useTaskActions(taskId: string) {
  const queryClient = useQueryClient();
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.tasks });
    void queryClient.invalidateQueries({ queryKey: queryKeys.steps(taskId) });
  };
  const ok = (message: string) => () => {
    toast.success(message);
    refresh();
  };
  const options = { onError: reportMutationError };
  return {
    cancel: useMutation({ ...options, mutationFn: () => tasksApi.cancel(taskId), onSuccess: ok("Task cancelled") }),
    retry: useMutation({ ...options, mutationFn: () => tasksApi.retry(taskId), onSuccess: ok("Task retried") }),
    priority: useMutation({
      ...options,
      mutationFn: (priority: TaskPriority) => tasksApi.priority(taskId, priority),
      onSuccess: ok("Priority updated"),
    }),
    addStep: useMutation({
      ...options,
      mutationFn: (step: StepCreate) => tasksApi.addStep(taskId, step),
      onSuccess: ok("Step added"),
    }),
    remove: useMutation({ ...options, mutationFn: () => tasksApi.remove(taskId), onSuccess: ok("Task removed") }),
  };
}

/** Surface a failed mutation: bounce to the gate on 401, else toast the message. */
export function reportMutationError(error: unknown): void {
  if (error instanceof ApiError && error.status === 401) {
    broadcastUnauthorized();
    return;
  }
  toast.error(error instanceof Error ? error.message : "request failed");
}

export function useScheduleActions() {
  const queryClient = useQueryClient();
  const refresh = () => void queryClient.invalidateQueries({ queryKey: queryKeys.schedules });
  const ok = (message: string) => () => {
    toast.success(message);
    refresh();
  };
  const options = { onError: reportMutationError };
  return {
    create: useMutation({
      ...options,
      mutationFn: (body: ScheduleCreate) => schedulesApi.create(body),
      onSuccess: ok("Schedule created"),
    }),
    update: useMutation({
      ...options,
      mutationFn: ({ id, body }: { id: string; body: ScheduleUpdate }) =>
        schedulesApi.update(id, body),
      onSuccess: ok("Schedule updated"),
    }),
    pause: useMutation({
      ...options,
      mutationFn: (id: string) => schedulesApi.pause(id),
      onSuccess: ok("Schedule paused"),
    }),
    resume: useMutation({
      ...options,
      mutationFn: (id: string) => schedulesApi.resume(id),
      onSuccess: ok("Schedule resumed"),
    }),
    fire: useMutation({
      ...options,
      mutationFn: (id: string) => schedulesApi.fire(id),
      onSuccess: ok("Schedule fired"),
    }),
    remove: useMutation({
      ...options,
      mutationFn: (id: string) => schedulesApi.remove(id),
      onSuccess: ok("Schedule removed"),
    }),
  };
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

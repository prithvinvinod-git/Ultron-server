/**
 * Typed client for the ULTRON REST + SSE surface.
 *
 * Every request is same-origin: the FastAPI app serves this SPA at `/` and the
 * API routers at the same root, so there is no base URL to configure. During
 * `vite dev` the API prefixes are proxied to 127.0.0.1:8000 (see vite.config.ts),
 * so the same relative paths work in both environments.
 *
 * The bearer token lives in localStorage and is attached to every call. `/events`
 * and `/chat/stream` cannot use `EventSource` (it sends no Authorization header
 * and §30 keeps tokens out of URLs), so both are read through `fetch` +
 * ReadableStream and a hand-written SSE frame parser.
 */

export const TOKEN_KEY = "ultron.token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (typeof window === "undefined") return;
  if (token) window.localStorage.setItem(TOKEN_KEY, token);
  else window.localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly details?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function parseBody(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function errorFrom(res: Response, body: unknown): ApiError {
  if (body && typeof body === "object" && "error" in body) {
    const envelope = (body as { error?: { code?: string; message?: string; details?: unknown } })
      .error;
    if (envelope?.message) {
      return new ApiError(res.status, envelope.message, envelope.details);
    }
  }
  return new ApiError(res.status, `HTTP ${res.status}`);
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  for (const [key, value] of Object.entries(authHeaders())) headers.set(key, value);

  const res = await fetch(path, { ...init, headers });
  const body = await parseBody(res);
  if (!res.ok) throw errorFrom(res, body);
  return body as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) => {
    const init: RequestInit = { method: "POST" };
    if (body !== undefined) init.body = JSON.stringify(body);
    return request<T>(path, init);
  },
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  /** For non-JSON endpoints such as the Prometheus `/metrics` exposition. */
  text: (path: string) => request<string>(path),
};

// --- Domain types (mirroring the server's pydantic response models) ----------

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_at: string;
  refresh_expires_at: string | null;
  session_id: string;
}

export interface Principal {
  actor_type: string;
  username: string | null;
  is_superuser: boolean;
}

export type AgentStatus =
  | "created"
  | "initializing"
  | "ready"
  | "running"
  | "waiting"
  | "verifying"
  | "completed"
  | "failed"
  | "cancelled"
  | "timeout";

export interface Agent {
  id: string;
  agent_type: string;
  name: string;
  description: string | null;
  status: AgentStatus;
  task_id: string | null;
  model: string | null;
  tools: string[];
  permissions: string[];
  memory_namespace: string | null;
  project_id: string | null;
  parent_agent_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface AgentType {
  agent_type: string;
  description: string;
}

export type TaskStatus =
  | "pending"
  | "queued"
  | "running"
  | "blocked"
  | "paused"
  | "completed"
  | "failed"
  | "cancelled";

export type TaskPriority = "low" | "normal" | "high" | "urgent";

export interface Task {
  id: string;
  goal: string;
  status: TaskStatus;
  priority: TaskPriority;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  completed_at: string | null;
  parent_task_id: string | null;
  project_id: string | null;
  conversation_id: string | null;
  result: Record<string, unknown> | null;
  error: string | null;
  verification: string | null;
  retry_count: number;
  max_retries: number;
  scheduled_for: string | null;
}

export interface Tool {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
  permission_level: string;
  timeout: number;
  reversibility: string;
  node_scope: string | null;
  audit: boolean;
  category: string;
}

export type VerificationOutcome = "success" | "partial" | "failed" | "unverified";

export type StepStatus = "pending" | "running" | "completed" | "failed" | "skipped";

export interface TaskStep {
  id: string;
  task_id: string;
  position: number;
  name: string;
  description: string | null;
  status: StepStatus;
  attempt: number;
  depends_on: string[];
  input_payload: Record<string, unknown> | null;
  output_payload: Record<string, unknown> | null;
  error: string | null;
}

export interface StepCreate {
  name: string;
  description?: string | null;
  position?: number | null;
  depends_on?: string[];
  input_payload?: Record<string, unknown> | null;
}

export interface ToolValidation {
  valid: boolean;
  tool: string;
  arguments: Record<string, unknown>;
}

export type NodeType = "windows" | "server" | "esp32" | "future";
export type NodeStatus = "online" | "offline" | "degraded";

export interface Node {
  node_id: string;
  node_type: NodeType;
  name: string;
  capabilities: string[];
  status: NodeStatus;
  last_seen: string | null;
  connected_at: string | null;
  version: string;
  client_of: string | null;
}

export type DeviceStatus = "unknown" | "online" | "offline" | "disabled";

export interface Device {
  device_id: string;
  name: string;
  device_type: string;
  capabilities: string[];
  status: DeviceStatus;
  last_seen: string | null;
  connected_at: string | null;
  firmware_version: string | null;
}

export type ScheduleKind = "interval" | "cron" | "once" | "event";
export type ScheduleStatus = "active" | "paused" | "completed" | "failed";

export interface Schedule {
  id: string;
  name: string;
  kind: ScheduleKind;
  status: ScheduleStatus;
  cron_expression: string | null;
  interval_seconds: number | null;
  run_at: string | null;
  payload: Record<string, unknown>;
  last_run_at: string | null;
  last_task_id: string | null;
  next_run_at: string | null;
  run_count: number;
  failure_count: number;
  last_error: string | null;
}

export type NotificationLevel = "info" | "warning" | "error" | "critical";

export interface NotificationItem {
  id: string;
  title: string;
  message: string;
  level: NotificationLevel;
  source: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface CheckResult {
  name: string;
  status: "ok" | "degraded" | "failed" | "skipped";
  ready: boolean;
  required: boolean;
  latency_ms: number;
  detail?: string;
  error?: string;
}

export interface HealthReport {
  status: "ok" | "degraded" | "failed" | "skipped";
  ready: boolean;
  duration_ms: number;
  checks: CheckResult[];
  warnings: string[];
}

export interface LivenessReport {
  status: "ok" | "degraded" | "failed" | "skipped";
  warnings: string[];
}

export interface ChatResponse {
  answer: string;
  model: string;
  finish_reason: string;
  tool_calls: number;
  took_ms: number;
}

export function login(username: string, password: string): Promise<TokenResponse> {
  return api.post<TokenResponse>("/auth/login", { username, password });
}

export function whoami(): Promise<Principal> {
  return api.get<Principal>("/auth/me");
}

export function logout(): Promise<void> {
  return request<void>("/auth/logout", { method: "POST" });
}

/** Auth surface beyond the login/me/logout helpers above (T502/T503). */
export const authApi = {
  login,
  me: whoami,
  logout,
  /** Rotate a refresh token for a fresh pair; the old token is invalidated. */
  refresh: (refreshToken: string) => api.post<TokenResponse>("/auth/refresh", { refresh_token: refreshToken }),
  /** Change the password after proving the current one; 204 on success. */
  changePassword: (currentPassword: string, newPassword: string) =>
    request<void>("/auth/password", {
      method: "POST",
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    }),
};

// --- SSE (Server-Sent Events) ------------------------------------------------

export interface SseFrame {
  id: string | null;
  event: string | null;
  data: string;
}

/**
 * Read an SSE byte stream into frames.
 *
 * Implements just enough of the wire format for ULTRON: `id:`, `event:` and
 * `data:` fields, `:` comment lines (keepalives), and blank-line frame
 * termination. Multi-line `data:` is joined with newlines.
 */
export async function* readSse(body: ReadableStream<Uint8Array>): AsyncGenerator<SseFrame> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const raw = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      let id: string | null = null;
      let event: string | null = null;
      const data: string[] = [];
      for (const line of raw.split("\n")) {
        if (line.startsWith(":")) continue;
        const colon = line.indexOf(":");
        const field = colon === -1 ? line : line.slice(0, colon);
        let value2 = colon === -1 ? "" : line.slice(colon + 1);
        if (value2.startsWith(" ")) value2 = value2.slice(1);
        if (field === "id") id = value2;
        else if (field === "event") event = value2;
        else if (field === "data") data.push(value2);
      }
      if (id !== null || event !== null || data.length) {
        yield { id, event, data: data.join("\n") };
      }
      boundary = buffer.indexOf("\n\n");
    }
  }
}

export interface ChatStreamHandlers {
  token?: (data: { text: string }) => void;
  tool_started?: (data: { tool: string; arguments: Record<string, unknown> }) => void;
  tool_completed?: (data: { tool: string; ok: boolean; output: unknown }) => void;
  done?: (data: ChatResponse & { iterations: number }) => void;
  error?: (data: { code: string; message: string }) => void;
}

/**
 * Send one prompt and stream the answer.
 *
 * Resolves when the socket closes. The `done` frame carries the same fields as
 * `POST /chat`, so a caller that ignores every intermediate frame still ends with
 * the full answer.
 */
export async function streamChat(
  message: string,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
  system?: string,
): Promise<void> {
  const headers = new Headers({ "Content-Type": "application/json", Accept: "text/event-stream" });
  for (const [key, value] of Object.entries(authHeaders())) headers.set(key, value);

  const init: RequestInit = {
    method: "POST",
    headers,
    body: JSON.stringify(system ? { message, system } : { message }),
  };
  if (signal) init.signal = signal;
  const res = await fetch("/chat/stream", init);
  if (!res.ok) throw errorFrom(res, await parseBody(res));
  if (!res.body) throw new ApiError(0, "the response carried no stream");

  for await (const frame of readSse(res.body)) {
    if (!frame.event) continue;
    const data = frame.data ? JSON.parse(frame.data) : {};
    const handler = handlers[frame.event as keyof ChatStreamHandlers];
    if (handler) (handler as (payload: unknown) => void)(data);
  }
}

export interface EventEnvelope {
  id: number;
  event_type: string;
  topic: string;
  payload: Record<string, unknown>;
  occurred_at: string;
  correlation_id: string | null;
  task_id: string | null;
  agent_id: string | null;
  device_id: string | null;
}

export interface StreamConnectionInfo {
  stream_id: string;
  topics: string[];
  resumed_from: number | null;
  dropped: number;
  heartbeat_seconds: number;
}

// --- Prompt repositories ------------------------------------------------------

export const agentsApi = {
  list: () => api.get<Agent[]>("/agents"),
  types: () => api.get<AgentType[]>("/agents/types"),
  get: (id: string) => api.get<Agent>(`/agents/${id}`),
  create: (body: { agent_type: string; name: string; model?: string | null }) =>
    api.post<Agent>("/agents", body),
  cancel: (id: string, reason = "cancelled from console") =>
    api.post<Agent>(`/agents/${id}/cancel`, { reason }),
  pause: (id: string) => api.post<Agent>(`/agents/${id}/pause`),
  resume: (id: string) => api.post<Agent>(`/agents/${id}/resume`),
  complete: (id: string) => api.post<Agent>(`/agents/${id}/complete`),
  fail: (id: string, error: string) => api.post<Agent>(`/agents/${id}/fail`, { error }),
  assignTask: (id: string, taskId: string) => api.post<Agent>(`/agents/${id}/task`, { task_id: taskId }),
  assignModel: (id: string, model: string | null) => api.post<Agent>(`/agents/${id}/model`, { model }),
  assignTools: (id: string, tools: string[]) => api.post<Agent>(`/agents/${id}/tools`, { tools }),
  assignPermissions: (id: string, permissions: string[]) =>
    api.post<Agent>(`/agents/${id}/permissions`, { permissions }),
  destroy: (id: string) => api.del<void>(`/agents/${id}`),
};

export const tasksApi = {
  list: () => api.get<Task[]>("/tasks"),
  get: (id: string) => api.get<Task>(`/tasks/${id}`),
  create: (goal: string, priority?: TaskPriority) =>
    api.post<Task>("/tasks", priority ? { goal, priority } : { goal }),
  cancel: (id: string) => api.post<Task>(`/tasks/${id}/cancel`),
  retry: (id: string) => api.post<Task>(`/tasks/${id}/retry`),
  priority: (id: string, priority: TaskPriority) =>
    api.post<Task>(`/tasks/${id}/priority`, { priority }),
  steps: (id: string) => api.get<TaskStep[]>(`/tasks/${id}/steps`),
  addStep: (id: string, body: StepCreate) => api.post<TaskStep>(`/tasks/${id}/steps`, body),
  remove: (id: string) => api.del<void>(`/tasks/${id}`),
};

export const toolsApi = {
  list: () => api.get<Tool[]>("/tools"),
  get: (name: string) => api.get<Tool>(`/tools/${encodeURIComponent(name)}`),
  validate: (name: string, args: Record<string, unknown>) =>
    api.post<ToolValidation>(`/tools/${encodeURIComponent(name)}/validate`, { arguments: args }),
};

export const nodesApi = {
  list: () => api.get<Node[]>("/nodes"),
  get: (id: string) => api.get<Node>(`/nodes/${id}`),
};

export const devicesApi = {
  list: () => api.get<Device[]>("/devices"),
  get: (id: string) => api.get<Device>(`/devices/${id}`),
};

export const metricsApi = {
  /** The Prometheus text exposition; parsed client-side by the diagnostics panel. */
  text: () => api.text("/metrics"),
};

// Schedules and notifications route modules land in T509/T510; these clients are
// typed against the server models those routes expose so the panels only need
// the wiring commit.
export const schedulesApi = {
  list: () => api.get<Schedule[]>("/schedules"),
  get: (id: string) => api.get<Schedule>(`/schedules/${id}`),
  pause: (id: string) => api.post<Schedule>(`/schedules/${id}/pause`),
  resume: (id: string) => api.post<Schedule>(`/schedules/${id}/resume`),
  remove: (id: string) => api.del<void>(`/schedules/${id}`),
};

export const notificationsApi = {
  list: () => api.get<NotificationItem[]>("/notifications"),
};

export const healthApi = {
  liveness: () => api.get<LivenessReport>("/health"),
  /**
   * `/ready` answers 503 with a *body* when a required dependency is down; that
   * body is the report, not an error, so a 503 is returned rather than thrown.
   */
  readiness: async (): Promise<HealthReport> => {
    const headers = new Headers(authHeaders());
    const res = await fetch("/ready", { headers });
    const body = await parseBody(res);
    if (res.status === 503 && body && typeof body === "object") return body as HealthReport;
    if (!res.ok) throw errorFrom(res, body);
    return body as HealthReport;
  },
};

// --- Voice (§25) -------------------------------------------------------------

export interface VoiceEngineInfo {
  engine: string;
  available: boolean;
  reason: string | null;
  sample_rate: number;
  content_type?: string | null;
}

export interface VoiceEngines {
  enabled: boolean;
  stt: VoiceEngineInfo;
  tts: VoiceEngineInfo;
}

export const voiceApi = {
  /** The honest STT/TTS availability report; read before offering the mic. */
  engines: () => api.get<VoiceEngines>("/voice/engines"),
};

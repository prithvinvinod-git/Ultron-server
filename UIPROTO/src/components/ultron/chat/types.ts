/**
 * Chat UI types and the mapping from ULTRON's `/chat/stream` SSE frames onto the
 * rich transcript shape the ported chat UI renders (ported from the Ultron
 * reference repo, §69.3). The transport stays ULTRON's: `token`,
 * `tool_started`, `tool_completed`, `done`, `error`.
 */

export type ChatRole = "user" | "assistant";

export interface ToolStepUI {
  toolCallId: string;
  name: string;
  state: "running" | "done" | "error";
  result?: string | undefined;
  error?: string | undefined;
}

export interface ChatMessage {
  id: string;
  role: ChatRole;
  text: string;
  thinking?: string | undefined;
  toolSteps?: ToolStepUI[] | undefined;
  provider?: string | undefined;
  model?: string | undefined;
  streaming?: boolean | undefined;
  error?: string | undefined;
}

export function makeId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

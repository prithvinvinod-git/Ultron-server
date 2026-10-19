/**
 * Shared TypeScript client types for ULTRON clients
 * Version: 1.0.0
 * 
 * This module provides a single source of truth for API types
 * shared across console (UIPROTO), Electron desktop, and PWA/mobile.
 */

export const CLIENT_TYPES_VERSION = '1.0.0';

// --- Auth ---------------------------------------------------------------

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

// --- Agents -------------------------------------------------------------

export type AgentStatus =
  | 'created'
  | 'initializing'
  | 'ready'
  | 'running'
  | 'waiting'
  | 'verifying'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'timeout';

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

// --- Tasks --------------------------------------------------------------

export type TaskStatus =
  | 'pending'
  | 'queued'
  | 'running'
  | 'blocked'
  | 'paused'
  | 'completed'
  | 'failed'
  | 'cancelled';

export type TaskPriority = 'low' | 'normal' | 'high' | 'urgent';

export type VerificationOutcome = 'success' | 'partial' | 'failed' | 'unverified';

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
  verification: VerificationOutcome | null;
  retry_count: number;
  max_retries: number;
  scheduled_for: string | null;
}

export interface TaskStep {
  id: string;
  task_id: string;
  position: number;
  name: string;
  description: string | null;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped';
  attempt: number;
  depends_on: string[];
  input_payload: Record<string, unknown> | null;
  output_payload: Record<string, unknown> | null;
  error: string | null;
}

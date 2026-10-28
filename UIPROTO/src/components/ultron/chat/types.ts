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

/** GenUI visualization specification - received from the AI in the `done` SSE frame. */
export interface GenUiResponse {
  type: "genui";
  version: string;
  title: string;
  explanation: string;
  visualization: {
    type: GenUiVisualizationType;
    data: VisualizationData;
    controls?: VisualizationControls;
    state?: GenUiStateModel;
  };
  follow_up_suggestion?: string | null;
}

export type GenUiVisualizationType =
  | "binary_tree"
  | "chart"
  | "flowchart"
  | "timeline"
  | "equation"
  | "vector"
  | "matrix"
  | "physics_simulation"
  | "interactive_table"
  | "image_panel";

/** Visualization data - union of all supported types */
export interface VisualizationData {
  /** Binary tree data */
  binary_tree?: {
    nodes: Array<{ id: string; value: number | string; left?: string; right?: string }>;
    edges?: Array<{ source: string; target: string }>;
    root: string;
  };
  /** Chart data */
  chart?: {
    type: "bar" | "line" | "pie" | "area" | "scatter";
    data: Record<string, unknown>;
  };
  /** Flowchart data */
  flowchart?: {
    nodes: Array<{ id: string; label: string; [key: string]: unknown }>;
    edges: Array<{ source: string; target: string }>;
  };
  /** Timeline data */
  timeline?: {
    events: Array<{ label: string; date: string; [key: string]: unknown }>;
  };
  /** Equation data */
  equation?: {
    latex: string;
  };
  /** Vector data */
  vector?: {
    magnitude: number;
    direction: number;
    label?: string;
  };
  /** Matrix data */
  matrix?: {
    rows: number;
    cols: number;
    values: number[][];
  };
  /** Physics simulation data */
  physics_simulation?: {
    particles: Array<{
      id: string;
      position: { x: number; y: number };
      velocity: { x: number; y: number };
      mass: number;
    }>;
    fields?: Record<string, unknown>;
  };
  /** Interactive table data */
  interactive_table?: {
    columns: Array<{ header: string; field: string; [key: string]: unknown }>;
    rows: Array<Record<string, unknown>>;
  };
  /** Image panel data */
  image_panel?: {
    image_url: string;
    caption?: string;
  };
}

/** Control configuration for a visualization */
export interface VisualizationControls {
  traversal_method?: "preorder" | "inorder" | "postorder" | "level-order";
  speed?: number;
  isPlaying?: boolean;
  reset?: boolean;
}

/** Binary tree traversal state */
export type TraversalAlgorithm = "preorder" | "inorder" | "postorder" | "level-order";

export interface GenUiStateModel {
  algorithm?: string;
  stepIndex?: number;
  isPlaying?: boolean;
  speed?: number;
  visitedNodeIds?: string[];
  currentNodeId?: string | null;
  [key: string]: unknown;
}

/** ChatMessage extended with GenUI support */
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
  /** Optional GenUI visualization from the AI - only present in `done` SSE frames */
  genui?: GenUiResponse | undefined;
}

/** Progress info for binary tree traversal visualization */
export interface BinaryTreeProgress {
  stepIndex: number;
  totalSteps: number;
  completedNodes: string[];
  currentNode: string | null;
  algorithm: TraversalAlgorithm;
  isPlaying: boolean;
  speed: number;
}

/** MessageBubble rendering state for GenUI */
export interface MessageBubbleRenderState {
  isRenderingGenui: boolean;
  isReducedMotion: boolean;
}

/* ── Compact 3x3 pixel dot loader ────────────────────────────────────────── */
const CHEVRON_DELAYS = Array.from({ length: 9 }, (_, i) => {
  const r = Math.floor(i / 3);
  const c = i % 3;
  return (c + Math.abs(r - 1)) * 90;
});

export function makeId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

/* ── Binary tree visualizer state types ──────────────────────────────────── */
export interface BinaryTreeLayoutNode {
  id: string;
  value: number | string;
  x: number;
  y: number;
  state: "unvisited" | "current" | "visited";
}

export interface BinaryTreeVisualizationState {
  nodes: BinaryTreeLayoutNode[];
  edges: Array<{ source: string; target: string }>;
  traversalSequence: string[];
  currentStep: number;
  completedNodes: string[];
  algorithm: TraversalAlgorithm;
  isPlaying: boolean;
  speed: number;
  stepExplanations: string[];
  progress: number;
  isComplete: boolean;
}
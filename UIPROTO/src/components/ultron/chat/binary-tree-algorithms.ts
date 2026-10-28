/**
 * Binary tree traversal algorithms - pure, testable, deterministic implementations.
 * These run on the client without any LLM involvement.
 */

import type { BinaryTreeData, BinaryTreeNode, TraversalAlgorithm, BinaryTreeState, NodeRenderState } from "./genui-types";

/** Build adjacency map from tree data */
function buildAdjacency(data: BinaryTreeData): Map<string, { left?: string; right?: string; value: number | string }> {
  const map = new Map<string, { left?: string; right?: string; value: number | string }>();
  for (const node of data.nodes) {
    map.set(node.id, { left: node.left, right: node.right, value: node.value });
  }
  return map;
}

/** Validate tree structure - no cycles, all references valid */
export function validateTree(data: BinaryTreeData): { valid: boolean; errors: string[] } {
  const errors: string[] = [];
  const adjacency = buildAdjacency(data);
  const visited = new Set<string>();

  // Check root exists
  if (!adjacency.has(data.root)) {
    errors.push(`Root node "${data.root}" not found in nodes`);
    return { valid: false, errors };
  }

  // Check for cycles and valid references
  function dfs(nodeId: string, path: Set<string>): void {
    if (visited.has(nodeId)) return;
    if (path.has(nodeId)) {
      errors.push(`Cycle detected at node "${nodeId}"`);
      return;
    }
    if (!adjacency.has(nodeId)) {
      errors.push(`Node "${nodeId}" referenced but not defined`);
      return;
    }
    path.add(nodeId);
    const node = adjacency.get(nodeId)!;
    if (node.left) dfs(node.left, path);
    if (node.right) dfs(node.right, path);
    path.delete(nodeId);
    visited.add(nodeId);
  }

  dfs(data.root, new Set());

  // Check for unreferenced nodes
  for (const nodeId of adjacency.keys()) {
    if (!visited.has(nodeId)) {
      errors.push(`Node "${nodeId}" is unreachable from root`);
    }
  }

  return { valid: errors.length === 0, errors };
}

/** Preorder traversal: Root -> Left -> Right */
export function preorderTraversal(data: BinaryTreeData): { sequence: string[]; explanations: string[] } {
  const adjacency = buildAdjacency(data);
  const sequence: string[] = [];
  const explanations: string[] = [];

  function traverse(nodeId: string): void {
    if (!nodeId || !adjacency.has(nodeId)) return;
    const node = adjacency.get(nodeId)!;
    sequence.push(nodeId);
    explanations.push(`Visit node ${nodeId} (value: ${node.value}) - Preorder: Root first`);
    if (node.left) traverse(node.left);
    if (node.right) traverse(node.right);
  }

  traverse(data.root);
  return { sequence, explanations };
}

/** Inorder traversal: Left -> Root -> Right */
export function inorderTraversal(data: BinaryTreeData): { sequence: string[]; explanations: string[] } {
  const adjacency = buildAdjacency(data);
  const sequence: string[] = [];
  const explanations: string[] = [];

  function traverse(nodeId: string): void {
    if (!nodeId || !adjacency.has(nodeId)) return;
    const node = adjacency.get(nodeId)!;
    if (node.left) traverse(node.left);
    sequence.push(nodeId);
    explanations.push(`Visit node ${nodeId} (value: ${node.value}) - Inorder: Left, Root, Right`);
    if (node.right) traverse(node.right);
  }

  traverse(data.root);
  return { sequence, explanations };
}

/** Postorder traversal: Left -> Right -> Root */
export function postorderTraversal(data: BinaryTreeData): { sequence: string[]; explanations: string[] } {
  const adjacency = buildAdjacency(data);
  const sequence: string[] = [];
  const explanations: string[] = [];

  function traverse(nodeId: string): void {
    if (!nodeId || !adjacency.has(nodeId)) return;
    const node = adjacency.get(nodeId)!;
    if (node.left) traverse(node.left);
    if (node.right) traverse(node.right);
    sequence.push(nodeId);
    explanations.push(`Visit node ${nodeId} (value: ${node.value}) - Postorder: Left, Right, Root`);
  }

  traverse(data.root);
  return { sequence, explanations };
}

/** Level-order traversal: Breadth-first */
export function levelOrderTraversal(data: BinaryTreeData): { sequence: string[]; explanations: string[] } {
  const adjacency = buildAdjacency(data);
  const sequence: string[] = [];
  const explanations: string[] = [];
  const queue: string[] = [data.root];

  while (queue.length > 0) {
    const nodeId = queue.shift()!;
    if (!adjacency.has(nodeId)) continue;
    const node = adjacency.get(nodeId)!;
    sequence.push(nodeId);
    explanations.push(`Visit node ${nodeId} (value: ${node.value}) - Level-order: Breadth-first`);
    if (node.left) queue.push(node.left);
    if (node.right) queue.push(node.right);
  }

  return { sequence, explanations };
}

/** Dispatch to the correct traversal algorithm */
export function runTraversal(
  data: BinaryTreeData,
  algorithm: TraversalAlgorithm
): { sequence: string[]; explanations: string[] } {
  switch (algorithm) {
    case "preorder":
      return preorderTraversal(data);
    case "inorder":
      return inorderTraversal(data);
    case "postorder":
      return postorderTraversal(data);
    case "level-order":
      return levelOrderTraversal(data);
    default:
      return preorderTraversal(data);
  }
}

/** Compute tree layout positions using Reingold-Tilford algorithm (simplified) */
export function computeTreeLayout(data: BinaryTreeData): Map<string, { x: number; y: number }> {
  const adjacency = buildAdjacency(data);
  const positions = new Map<string, { x: number; y: number }>();

  // First pass: compute preliminary x positions (inorder)
  let xCounter = 0;
  const nodeDepths = new Map<string, number>();

  function computeDepths(nodeId: string, depth: number): void {
    if (!nodeId || !adjacency.has(nodeId)) return;
    nodeDepths.set(nodeId, depth);
    const node = adjacency.get(nodeId)!;
    if (node.left) computeDepths(node.left, depth + 1);
    if (node.right) computeDepths(node.right, depth + 1);
  }

  computeDepths(data.root, 0);

  // Inorder traversal for x positions
  function assignX(nodeId: string): void {
    if (!nodeId || !adjacency.has(nodeId)) return;
    const node = adjacency.get(nodeId)!;
    if (node.left) assignX(node.left);
    positions.set(nodeId, { x: xCounter++, y: nodeDepths.get(nodeId) ?? 0 });
    if (node.right) assignX(node.right);
  }

  assignX(data.root);

  // Scale positions for better visual spacing
  const maxX = Math.max(...Array.from(positions.values()).map(p => p.x));
  const maxY = Math.max(...Array.from(positions.values()).map(p => p.y));

  const xScale = maxX > 0 ? 200 / maxX : 1;
  const yScale = maxY > 0 ? 120 / maxY : 1;

  const scaledPositions = new Map<string, { x: number; y: number }>();
  for (const [id, pos] of positions) {
    scaledPositions.set(id, {
      x: pos.x * xScale + 100, // Center offset
      y: pos.y * yScale + 80,
    });
  }

  return scaledPositions;
}

/** Create initial binary tree state */
export function createInitialState(data: BinaryTreeData, algorithm: TraversalAlgorithm = "inorder"): BinaryTreeState {
  const { sequence, explanations } = runTraversal(data, algorithm);

  return {
    algorithm,
    stepIndex: 0,
    isPlaying: false,
    speed: 1,
    visitedNodeIds: [],
    currentNodeId: null,
    traversalSequence: sequence,
    stepExplanations: explanations,
  };
}

/** Advance to next step */
export function stepForward(state: BinaryTreeState): BinaryTreeState {
  if (state.stepIndex >= state.traversalSequence.length) return state;

  const currentNodeId = state.traversalSequence[state.stepIndex];
  return {
    ...state,
    stepIndex: state.stepIndex + 1,
    visitedNodeIds: [...state.visitedNodeIds, currentNodeId],
    currentNodeId,
  };
}

/** Go back to previous step */
export function stepBackward(state: BinaryTreeState): BinaryTreeState {
  if (state.stepIndex <= 0) return state;

  const newVisited = state.visitedNodeIds.slice(0, -1);
  const prevNodeId = state.traversalSequence[state.stepIndex - 1];

  return {
    ...state,
    stepIndex: state.stepIndex - 1,
    visitedNodeIds: newVisited,
    currentNodeId: state.stepIndex > 1 ? state.traversalSequence[state.stepIndex - 2] : null,
  };
}

/** Restart traversal */
export function restart(state: BinaryTreeState): BinaryTreeState {
  return {
    ...state,
    stepIndex: 0,
    visitedNodeIds: [],
    currentNodeId: null,
    isPlaying: false,
  };
}

/** Change traversal algorithm */
export function changeAlgorithm(state: BinaryTreeState, algorithm: TraversalAlgorithm, data: BinaryTreeData): BinaryTreeState {
  const { sequence, explanations } = runTraversal(data, algorithm);
  return {
    ...state,
    algorithm,
    stepIndex: 0,
    visitedNodeIds: [],
    currentNodeId: null,
    isPlaying: false,
    traversalSequence: sequence,
    stepExplanations: explanations,
  };
}

/** Get node render states for visualization */
export function getNodeRenderStates(
  data: BinaryTreeData,
  layout: Map<string, { x: number; y: number }>,
  state: BinaryTreeState
): NodeRenderState[] {
  const states: NodeRenderState[] = [];

  for (const node of data.nodes) {
    const pos = layout.get(node.id);
    let nodeState: "unvisited" | "current" | "visited" = "unvisited";

    if (state.currentNodeId === node.id) {
      nodeState = "current";
    } else if (state.visitedNodeIds.includes(node.id)) {
      nodeState = "visited";
    }

    states.push({
      id: node.id,
      value: node.value,
      x: pos?.x ?? 0,
      y: pos?.y ?? 0,
      state: nodeState,
    });
  }

  return states;
}

/** Get edges for rendering */
export function getEdges(data: BinaryTreeData): Array<{ source: string; target: string }> {
  if (data.edges && data.edges.length > 0) {
    return data.edges;
  }
  // Infer edges from node left/right references
  const edges: Array<{ source: string; target: string }> = [];
  for (const node of data.nodes) {
    if (node.left) edges.push({ source: node.id, target: node.left });
    if (node.right) edges.push({ source: node.id, target: node.right });
  }
  return edges;
}

/** Check if traversal is complete */
export function isComplete(state: BinaryTreeState): boolean {
  return state.stepIndex >= state.traversalSequence.length;
}

/** Get progress percentage */
export function getProgress(state: BinaryTreeState): number {
  if (state.traversalSequence.length === 0) return 100;
  return Math.round((state.stepIndex / state.traversalSequence.length) * 100);
}

/** Generate a sample binary tree for testing */
export function generateSampleTree(): BinaryTreeData {
  return {
    nodes: [
      { id: "1", value: 10, left: "2", right: "3" },
      { id: "2", value: 5, left: "4", right: "5" },
      { id: "3", value: 15, left: "6", right: "7" },
      { id: "4", value: 2, left: undefined, right: undefined },
      { id: "5", value: 7, left: undefined, right: undefined },
      { id: "6", value: 12, left: undefined, right: undefined },
      { id: "7", value: 20, left: undefined, right: undefined },
    ],
    root: "1",
  };
}
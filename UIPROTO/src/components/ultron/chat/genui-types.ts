/** @jsxImportSource @tanstack/react-start */
import { cn } from "@/lib/utils";

/** Binary tree node data interface */
export interface BinaryTreeNode {
  id: string;
  value: number | string;
  left?: string;
  right?: string;
}

/** Binary tree edge data interface */
export interface BinaryTreeEdge {
  source: string;
  target: string;
}

/** Binary tree data specification */
export interface BinaryTreeData {
  nodes: BinaryTreeNode[];
  edges?: BinaryTreeEdge[];
  root: string;
}

/** Animation state interface */
export interface AnimationState {
  isPlaying: boolean;
  speed: number;
  stepIndex: number;
  visitedNodeIds: string[];
  currentNodeId: string | null;
  algorithm: string;
  traversalSequence: string[];
  stepExplanations: string[];
}

/** Control keys */
export type TraversalAlgorithm = "preorder" | "inorder" | "postorder" | "level-order";

/** Controls configuration */
export interface BinaryTreeControls {
  traversal_method?: TraversalAlgorithm;
  speed?: number;
  isPlaying?: boolean;
  reset?: boolean;
}
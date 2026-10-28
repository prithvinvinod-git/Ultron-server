/**
 * Binary Tree Visualization Component
 *
 * An interactive binary tree visualization supporting multiple traversal algorithms.
 * This is the priority feature for GenUI, but designed to be part of a progressive
 * UI system where the AI decides when visualization materially improves the answer.
 *
 * Features:
 * - SVG tree layout with automatic positioning
 * - Node highlighting (unvisited/current/visited states)
 * - Animated traversal with configurable speed (play/pause/step)
 * - Four traversal algorithms: preorder, inorder, postorder, level-order
 * - Progress indicator and step explanations
 * - Traversal algorithm selector
 * - Responsive layout for mobile and desktop
 * - Reduced-motion support
 * - Local computation (no LLM calls needed for traversal)
 */

"use client";

import { useRef, useState, useEffect, useCallback } from "react";
import { cn } from "@/lib/utils";
import {
  BinaryTreeLayoutNode,
  BinaryTreeVisualizationState,
  TraversalAlgorithm,
  NodeRenderState
} from "./genui-types";
import {
  runTraversal,
  createInitialState,
  stepForward,
  stepBackward,
  restart,
  changeAlgorithm,
  getNodeRenderStates,
  getEdges,
  computeTreeLayout,
  isComplete,
  getProgress,
  generateSampleTree
} from "./binary-tree-algorithms";
import { BinaryTreeNode } from "./genui-types";

/** SVG constants */
const NODE_RADIUS = 18;
const NODE_STROKE_WIDTH = 2;
const EDGE_STROKE_WIDTH = 2;
const EDGE_STROKE_OPACITY = 0.5;
const TEXT_FONT_SIZE = 13;
const TRANSITION_DURATION = 250;

/** Binary Tree Visualization Component */
export function BinaryTreeVisualization({
  data,
  controls,
  state,
  reducedMotion,
}: {
  data: BinaryTreeData;
  controls: BinaryTreeControls;
  state: BinaryTreeState;
  reducedMotion: boolean;
}) {
  const [layout, setLayout] = useState<Map<string, { x: number; y: number }>>(new Map());
  const [renderStates, setRenderStates] = useState<NodeRenderState[]>([]);
  const [traversalState, setTraversalState] = useState<BinaryTreeState>(state);
  const [isAnimating, setIsAnimating] = useState(false);
  const animationRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const clickHandlerRef = useRef<(event: React.MouseEvent) => void>(() => {});

  /** Compute layout when data or traversal state changes */
  useEffect(() => {
    const newLayout = computeTreeLayout(data);
    setLayout(newLayout);
    const newRenderStates = getNodeRenderStates(data, newLayout, traversalState);
    setRenderStates(newRenderStates);
  }, [data.nodes, traversalState.algorithm, traversalState.stepIndex]);

  /** Update render states when traversal state changes */
  useEffect(() => {
    if (layout.size === 0) return;
    const newRenderStates = getNodeRenderStates(data, layout, traversalState);
    setRenderStates(newRenderStates);
  }, [traversalState]);

  /** Speed change - restart animation with new speed */
  useEffect(() => {
    if (isAnimating) {
      if (animationRef.current) {
        clearTimeout(animationRef.current);
        setIsAnimating(false);
      }
    }
    if (traversalState.isPlaying) {
      startAutoPlay();
    }
  }, [controls.speed, isAnimating, traversalState.isPlaying]);

  /** Auto-play traversal */
  const startAutoPlay = useCallback(() => {
    if (isComplete(traversalState) || traversalState.stepIndex >= traversalState.traversalSequence.length) {
      setIsAnimating(false);
      return;
    }

    setIsAnimating(true);

    const speed = Math.max(0.5, controls.speed || 1);
    const delay = Math.max(50, 800 / speed);

    animationRef.current = setTimeout(() => {
      setTraversalState(stepForward(traversalState));
      if (!isComplete(traversalState)) {
        startAutoPlay();
      } else {
        setIsAnimating(false);
      }
    }, delay);
  }, [controls.speed, traversalState.isPlaying]);

  /** Subscribe to controls changes */
  useEffect(() => {
    if (controls.traversal_method && controls.traversal_method !== traversalState.algorithm) {
      setTraversalState(changeAlgorithm(traversalState, controls.traversal_method, data));
    }
    if (controls.isPlaying !== undefined && controls.isPlaying !== traversalState.isPlaying) {
      if (controls.isPlaying) {
        startAutoPlay();
      } else {
        setIsAnimating(false);
      }
    }
  }, [controls, traversalState]);

  /** Step forward */
  const handleStepForward = useCallback(() => {
    setTraversalState(stepForward(traversalState));
    if (!isAnimating) {
      setIsAnimating(false);
    }
  }, [traversalState]);

  /** Step backward */
  const handleStepBackward = useCallback(() => {
    setTraversalState(stepBackward(traversalState));
    setIsAnimating(false);
  }, [traversalState]);

  /** Restart */
  const handleRestart = useCallback(() => {
    setTraversalState(restart(traversalState));
    setIsAnimating(false);
    if (animationRef.current) {
      clearTimeout(animationRef.current);
      setIsAnimating(false);
    }
  }, [traversalState]);

  /** Change traversal algorithm */
  const handleChangeAlgorithm = useCallback((algorithm: TraversalAlgorithm) => {
    setTraversalState(changeAlgorithm(traversalState, algorithm, data));
    setIsAnimating(false);
    if (animationRef.current) {
      clearTimeout(animationRef.current);
      setIsAnimating(false);
    }
  }, [traversalState, data]);

  /** Play/pause toggle */
  const handlePlayPause = useCallback(() => {
    const newIsPlaying = !traversalState.isPlaying;
    if (newIsPlaying) {
      startAutoPlay();
    } else {
      setIsAnimating(false);
      if (animationRef.current) {
        clearTimeout(animationRef.current);
      }
    }
  }, [traversalState, startAutoPlay]);

  /** Initialize on mount */
  useEffect(() => {
    if (layout.size === 0) {
      const newLayout = computeTreeLayout(data);
      setLayout(newLayout);
      const newRenderStates = getNodeRenderStates(data, newLayout, traversalState);
      setRenderStates(newRenderStates);
    }
  }, [data.nodes, traversalState]);

  /** Cleanup on unmount */
  useEffect(() => {
    return () => {
      if (animationRef.current) {
        clearTimeout(animationRef.current);
      }
    };
  }, []);

  /** Get edge list (inferred from node left/right references) */
  const edges = getEdges(data);

  /** Get X coordinate */
  function getNodeX(layout: Map<string, { x: number; y: number }>, nodeId: string): number {
    return layout.get(nodeId)?.x ?? 0;
  }

  /** Get Y coordinate */
  function getNodeY(layout: Map<string, { x: number; y: number }>, nodeId: string): number {
    return layout.get(nodeId)?.y ?? 0;
  }

  /** Get fill color based on state */
  function getNodeFillColor(state: "unvisited" | "current" | "visited"): string {
    switch (state) {
      case "current": return "var(--primary)";
      case "visited": return "var(--secondary)";
      case "unvisited":
      default: return "var(--card)";
    }
  }

  /** Compute SVG viewBox */
  function computeSvgViewBox(layout: Map<string, { x: number; y: number }>, data: BinaryTreeData): string {
    if (layout.size === 0) return "0 0 200 200";

    const allX = Array.from(layout.values()).map(p => p.x);
    const allY = Array.from(layout.values()).map(p => p.y);

    const minX = Math.min(...allX) - NODE_RADIUS - 20;
    const minY = Math.min(...allY) - NODE_RADIUS - 20;
    const maxX = Math.max(...allX) + NODE_RADIUS + 20;
    const maxY = Math.max(...allY) + NODE_RADIUS + 20;

    const width = Math.max(400, maxX - minX);
    const height = Math.max(300, maxY - minY);

    return `${minX} ${minY} ${width} ${height}`;
  }

  /** Disable states */
  const isCompleteState = isComplete(traversalState);
  const totalSteps = traversalState.traversalSequence.length;
  const canStepForward = !isCompleteState && traversalState.stepIndex < totalSteps;
  const canStepBackward = traversalState.stepIndex > 0;
  const canRestart = totalSteps > 0;
  const canChangeAlgorithm = totalSteps > 0;

  /** Current step info */
  const currentNodeId = traversalState.currentNodeId;
  const currentStepIndex = traversalState.stepIndex;
  const progress = getProgress(traversalState);

  return (
    <div className="binary-tree-visualization"
         aria-label={`Binary Tree Traversal - ${traversalState.algorithm}`}
         role="region">
      <div className="binary-tree-controls">
        <div className="algorithm-selector">
          <span className="algorithm-label">Algorithm:</span>
          <select
            className="algorithm-select"
            value={traversalState.algorithm}
            onChange={(e) => handleChangeAlgorithm(e.target.value as TraversalAlgorithm)}
            disabled={!canChangeAlgorithm}
          >
            <option value="preorder">Preorder</option>
            <option value="inorder">Inorder</option>
            <option value="postorder">Postorder</option>
            <option value="level-order">Level-order</option>
          </select>
        </div>

        <div className="controls-row">
          <button
            className={cn("control-button", { disabled: !canStepBackward && traversalState.stepIndex > 0 }, "opacity-50 cursor-not-allowed")}
            onClick={handleStepBackward}
            disabled={!canStepBackward}
            title="Previous step"
            aria-label="Previous step"
          >
            <svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <path d="M15 18l-6-6l6-6"/>
            </svg>
          </button>

          <button
            className={cn("control-button", { disabled: !canStepForward }, "opacity-50 cursor-not-allowed")}
            onClick={handleStepForward}
            disabled={!canStepForward}
            title="Next step"
            aria-label="Next step"
          >
            <svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <path d="M9 18l6-6-6-6"/>
            </svg>
          </button>

          <button
            className={cn("control-button", { disabled: !canRestart }, "opacity-50 cursor-not-allowed")}
            onClick={handleRestart}
            disabled={!canRestart}
            title="Restart"
            aria-label="Restart"
          >
            <svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <path d="M4 12h16M12 4l8 8-8 8"/>
            </svg>
          </button>

          <button
            className={cn("control-button", { disabled: isCompleteState }, "opacity-50 cursor-not-allowed")}
            onClick={handlePlayPause}
            title={traversalState.isPlaying ? "Pause" : "Play"}
            aria-label={traversalState.isPlaying ? "Pause" : "Play"}
          >
            {traversalState.isPlaying ? (
              <svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                <path d="M14.752 11.168a6.983 6.983 0 0 1 .246-2.073 6.993 6.993 0 0 0-4.607 1.728 6.996 6.996 0 0 0-2.261-5.56 6.996 6.996 0 0 0-6.148 2.63 6.985 6.985 0 0 1-2.063.843 6.986 6.986 0 0 0-.627 2.293 6.983 6.983 0 0 1-.048 2.157 6.99 6.99 0 0 0 2.39 2.017 6.986 6.986 0 0 1 4.736-.678 6.986 6.986 0 0 1 2.194 5.187 6.983 6.983 0 0 0 2.056-.153 6.99 6.99 0 0 0 1.293-.774 6.983 6.983 0 0 1 .845-2.287 6.99 6.99 0 0 0-3.26-3.098z"/>
              </svg>
            ) : (
              <svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                <path d="M8 5h8M5 8l7 7-7 7"/>
              </svg>
            )}
          </button>

          <select
            className="speed-select"
            value={traversalState.speed || 1}
            onChange={(e) => {
              const speed = parseFloat(e.target.value);
              // Speed change handled via controls
            }}
          >
            <option value={0.5}>0.5x</option>
            <option value={1}>1x</option>
            <option value={2}>2x</option>
            <option value={3}>3x</option>
          </select>
        </div>

        <div className="progress-bar">
          <div
            className="progress-fill"
            style={{ width: `${progress}%` }}
            aria-valuemin="0"
            aria-valuemax="100"
            aria-valuenow={progress}
            title={`Progress: ${progress}%`}
          />
        </div>
      </div>

      {/* Reduced motion notice */}
      {reducedMotion && (
        <p className="reduced-motion-notice">
          Reduced motion mode. Use controls to step through traversal.
        </p>
      )}

      {/* SVG Canvas */}
      <svg
        className="binary-tree-svg"
        viewBox={computeSvgViewBox(layout, data)}
        aria-label="Binary tree visualization"
        role="img"
      >
        {/* Edges */}
        {edges.length > 0 && (
          <g className="binary-tree-edges">
            {edges.map((edge, i) => (
              <line
                key={`edge-${edge.source}-${edge.target}`}
                x1={getNodeX(layout, edge.source)}
                y1={getNodeY(layout, edge.source)}
                x2={getNodeX(layout, edge.target)}
                y2={getNodeY(layout, edge.target)}
                stroke="var(--border)"
                strokeWidth={EDGE_STROKE_WIDTH}
                strokeOpacity={EDGE_STROKE_OPACITY}
                strokeLinecap="round"
              />
            ))}
          </g>
        )}

        {/* Nodes */}
        {renderStates.map((node, i) => (
          <g
            key={`node-${node.id}`}
            className="binary-tree-node"
          >
            <circle
              cx={node.x}
              cy={node.y}
              r={NODE_RADIUS}
              fill={getNodeFillColor(node.state)}
              stroke="var(--foreground)"
              strokeWidth={NODE_STROKE_WIDTH}
            />
            <text
              x={node.x}
              y={node.y + 4}
              textAnchor="middle"
              fontSize={TEXT_FONT_SIZE}
              fontWeight="600"
              fill="var(--card-foreground)"
              fillOpacity={node.state === "current" ? 0.9 : 1}
            >
              {node.value}
            </text>
          </g>
        ))}

        {/* Current node highlight */}
        {currentNodeId && !isCompleteState && (
          <g className="binary-tree-current-highlight">
            {renderStates.map((node) => {
              if (node.id === currentNodeId) {
                return (
                  <circle
                    cx={node.x}
                    cy={node.y}
                    r={NODE_RADIUS + 8}
                    fill="none"
                    stroke="var(--primary)"
                    strokeWidth={3}
                    strokeDasharray="4 4"
                    opacity={0.6}
                  />
                );
              }
              return null;
            })}
          </g>
        )}
      </svg>

      {/* Step explanation */}
      <div className="step-explanation" aria-live="polite" role="status">
        {traversalState.stepExplanations?.[currentStepIndex] || "Select a traversal algorithm to begin"}
      </div>

      {/* Progress indicator */}
      <div className="progress-indicator">
        Step {currentStepIndex + 1} of {totalSteps}
        {totalSteps > 0 && `<span className="progress-percentage">{progress}%</span>`}
      </div>

      {/* Controls status */}
      <div className="controls-status">
        {traversalState.isPlaying ? "Playing" : "Paused"}
        {currentNodeId && <span className="current-node-indicator">Current: {currentNodeId}</span>}
      </div>
    </div>
  );
}
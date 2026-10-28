/**
 * Flowchart Visualization Component
 *
 * Renders a directed graph/flowchart using SVG with nodes and edges.
 * Supports responsive layout and distinct node states.
 */

"use client";

import { useRef, useState, useEffect } from "react";
import { cn } from "@/lib/utils";
import { BinaryTreeNode } from "./genui-types";

/** Node state types */
export type NodeState = "unvisited" | "current" | "visited" | "error";

/** Flowchart node data */
export interface FlowchartNode {
  id: string;
  label: string;
  state?: NodeState;
  [key: string]: unknown;
}

/** Flowchart edge data */
export interface FlowchartEdge {
  source: string;
  target: string;
  label?: string;
  [key: string]: unknown;
}

/** Flowchart visualization props */
export interface FlowchartVisualizationProps {
  data: {
    nodes: FlowchartNode[];
    edges: FlowchartEdge[];
  };
  controls?: any;
  state?: any;
  reducedMotion?: boolean;
}

/** Compute node positions using a simple force-directed approach */
function computeFlowchartPositions(
  nodes: FlowchartNode[],
  edges: FlowchartEdge[]
): Map<string, { x: number; y: number }> {
  const positions = new Map<string, { x: number; y: number }>();

  if (nodes.length === 0) return positions;

  // Simple grid layout
  const gridSize = Math.ceil(Math.sqrt(nodes.length));
  const cellWidth = 200;
  const cellHeight = 100;

  nodes.forEach((node, index) => {
    const col = index % gridSize;
    const row = Math.floor(index / gridSize);
    positions.set(node.id, {
      x: 100 + col * cellWidth,
      y: 50 + row * cellHeight,
    });
  });

  return positions;
}

/** Flowchart Visualization Component */
export function FlowchartVisualization({
  data,
  controls,
  state,
  reducedMotion,
}: FlowchartVisualizationProps) {
  const [positions, setPositions] = useState<Map<string, { x: number; y: number }>>(new Map());
  const [edges, setEdges] = useState<Array<{ source: string; target: string }>>([]);

  // Compute positions when data changes
  useEffect(() => {
    if (!data?.nodes?.length) return;

    const nodePositions = computeFlowchartPositions(data.nodes, data.edges || []);
    setPositions(nodePositions);

    // Set edges
    const edgeList = (data.edges || []).map(edge => ({
      source: edge.source,
      target: edge.target,
    }));
    setEdges(edgeList);
  }, [data]);

  // Get edge list
  const edgeList = edges;

  // Get positions for specific nodes
  function getNodeX(nodeId: string): number {
    return positions.get(nodeId)?.x ?? 0;
  }

  function getNodeY(nodeId: string): number {
    return positions.get(nodeId)?.y ?? 0;
  }

  // Get node state color
  function getNodeColor(state: NodeState | undefined): string {
    switch (state) {
      case "current":
        return "var(--primary)";
      case "visited":
        return "var(--secondary)";
      case "error":
        return "var(--rose-500)";
      case "unvisited":
      default:
        return "var(--card)";
    }
  }

  // Get node fill color
  function getNodeFill(state: NodeState | undefined): string {
    switch (state) {
      case "current":
        return "var(--primary)";
      case "visited":
        return "rgba(var(--secondary), 0.3)";
      case "error":
        return "rgba(var(--rose-500), 0.2)";
      case "unvisited":
      default:
        return "var(--card)";
    }
  }

  // Disable states
  const canReset = data.edges.length > 0;

  return (
    <div className="flowchart-visualization"
         aria-label="Flowchart visualization">
      <div className="flowchart-controls">
        <button
          onClick={() => {/* Reset */}
          }
          disabled={!canReset}
          title="Reset"
          aria-label="Reset"
          className="control-button"
        >
          ↻
        </button>
      </div>

      {/* Edges */}
      {edgeList.length > 0 && (
        <g className="flowchart-edges">
          {edgeList.map((edge, i) => (
            <line
              key={`edge-${edge.source}-${edge.target}`}
              x1={getNodeX(edge.source)}
              y1={getNodeY(edge.source)}
              x2={getNodeX(edge.target)}
              y2={getNodeY(edge.target)}
              stroke="var(--border)"
              strokeWidth={EDGE_STROKE_WIDTH}
              strokeOpacity={EDGE_STROKE_OPACITY}
              strokeLinecap="round"
            />
          ))}
        </g>
      )}

      {/* Nodes */}
      {data.nodes.map((node, i) => (
        <g
          key={`node-${node.id}`}
          className="flowchart-node"
        >
          <circle
            cx={getNodeX(node.id)}
            cy={getNodeY(node.id)}
            r={24}
            fill={getNodeFill(node.state)}
            stroke="var(--foreground)"
            strokeWidth={NODE_STROKE_WIDTH}
          />
          <text
            x={getNodeX(node.id)}
            y={getNodeY(node.id) + 5}
            textAnchor="middle"
            fontSize={TEXT_FONT_SIZE}
            fontWeight="600"
            fill="var(--card-foreground)"
          >
            {node.label}
          </text>
        </g>
      ))}

      {/* Current node highlight */}
      {state?.currentNodeId && (
        <g className="flowchart-current-highlight">
          {data.nodes.map((node) => {
            if (node.id === state.currentNodeId) {
              return (
                <circle
                  cx={getNodeX(node.id)}
                  cy={getNodeY(node.id)}
                  r={24 + 8}
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
    </div>
  );
}
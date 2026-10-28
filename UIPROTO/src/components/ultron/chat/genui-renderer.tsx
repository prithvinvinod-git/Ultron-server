/**
 * GenUI Renderer Dispatcher
 *
 * Receives a validated GenUiResponse and dispatches to the correct
 * visualization component based on visualization.type.
 *
 * This component renders the visualization with its controls and state,
 * handling traversal algorithms, playback, and step-by-step navigation
 * entirely on the client without LLM involvement.
 */

"use client";

import { useEffect, useRef } from "react";
import { useReducedMotion } from "@/hooks/use-mobile";
import { GenUiVisualizationType, GenUiResponse, GenUiStateModel } from "./types";
import { BinaryTreeVisualization } from "./binary-tree-visualization";
import { ChartVisualization } from "./chart-visualization";
import { FlowchartVisualization } from "./flowchart-visualization";
import { TimelineVisualization } from "./timeline-visualization";
import { EquationVisualization } from "./equation-visualization";
import { VectorVisualization } from "./vector-visualization";
import { MatrixVisualization } from "./matrix-visualization";
import { PhysicsSimulationVisualization } from "./physics-simulation-visualization";
import { InteractiveTableVisualization } from "./interactive-table-visualization";
import { ImagePanelVisualization } from "./image-panel-visualization";
import { BinaryTreeNode, BinaryTreeData } from "./genui-types";

/** Map of visualization types to their renderer components */
const visualizationRenderers: Record<GenUiVisualizationType, React.ComponentType<any>> = {
  binary_tree: BinaryTreeVisualization,
  chart: ChartVisualization,
  flowchart: FlowchartVisualization,
  timeline: TimelineVisualization,
  equation: EquationVisualization,
  vector: VectorVisualization,
  matrix: MatrixVisualization,
  physics_simulation: PhysicsSimulationVisualization,
  interactive_table: InteractiveTableVisualization,
  image_panel: ImagePanelVisualization,
};

/** GenUI Renderer Component */
export function GenUiRenderer({ genui }: { genui: GenUiResponse }) {
  const reducedMotion = useReducedMotion();
  const visualization = genui.visualization;
  const type = visualization.type;
  const data = visualization.data;
  const controls = visualization.controls || {};
  const state = visualization.state || {};
  const title = genui.title;
  const explanation = genui.explanation;

  // Ref to trigger re-render when data changes
  const dataRef = useRef(genui);
  dataRef.current = genui;

  useEffect(() => {
    // Clean up any active timers/animations on unmount
    return () => {
      // Components handle their own cleanup internally
    };
  }, []);

  // If we don't have a recognized visualization type, fall back to text
  if (!type || !visualizationRenderers[type]) {
    return null;
  }

  const RendererComponent = visualizationRenderers[type];

  return (
    <div className="genui-visualization-container" aria-label={title}>
      <div className="genui-header" role="region" aria-labelledby="genui-title-{type}">
        <h3 id={`genui-title-${type}`} className="genui-title">
          {title}
        </h3>
        <p className="genui-explanation">{explanation}</p>
      </div>

      <RendererComponent
        data={data}
        controls={controls as any}
        state={state as any}
        reducedMotion={reducedMotion}
      />
    </div>
  );
}

/** Render a specific visualization type or show fallback */
export function useGenUiRenderer(
  genui: GenUiResponse | undefined
): JSX.Element | null {
  if (!genui) return null;

  const visualization = genui.visualization;
  if (!visualization || !visualization.type) return null;

  const type = visualization.type;
  const Renderer = visualizationRenderers[type];

  if (!Renderer) {
    // Unknown visualization type - silently fall back
    return null;
  }

  return (
    <Renderer
      data={visualization.data}
      controls={visualization.controls || {}}
      state={visualization.state || {}}
      reducedMotion={useReducedMotion()}
    />
  );
}
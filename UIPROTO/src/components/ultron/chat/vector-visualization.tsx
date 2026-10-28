/**
 * Vector Visualization Component
 *
 * Renders a vector diagram with magnitude, direction, and optional label.
 */

"use client";

import { useRef, useState, useEffect } from "react";
import { cn } from "@/lib/utils";

/** Vector data specification */
export interface VectorData {
  magnitude: number;
  direction: number; // in degrees
  label?: string;
}

/** Vector visualization props */
export interface VectorVisualizationProps {
  data: VectorData;
  controls?: any;
  state?: any;
  reducedMotion?: boolean;
}

/** Vector Visualization Component */
export function VectorVisualization({
  data,
  controls,
  state,
  reducedMotion,
}: VectorVisualizationProps) {
  const [magnitude, setMagnitude] = useState(data.magnitude || 0);
  const [direction, setDirection] = useState(data.direction || 0);
  const [label, setLabel] = useState(data.label || "");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMagnitude(data.magnitude || 0);
    setDirection(data.direction || 0);
    setLabel(data.label || "");
    setMounted(true);
  }, [data]);

  if (!mounted) {
    return <div className="vector-placeholder">Loading vector...</div>;
  }

  // Calculate vector endpoint
  const radians = (direction * Math.PI) / 180;
  const endX = magnitude * Math.cos(radians);
  const endY = magnitude * Math.sin(radians);

  const arrowHeadLength = 15;
  const arrowHeadAngle = 30 * (Math.PI / 180);

  // Arrowhead points
  const leftX = endX - arrowHeadLength * Math.cos(Math.PI - arrowHeadAngle - radians);
  const leftY = endY - arrowHeadLength * Math.sin(Math.PI - arrowHeadAngle - radians);
  const rightX = endX - arrowHeadLength * Math.cos(Math.PI + arrowHeadAngle - radians);
  const rightY = endY - arrowHeadLength * Math.sin(Math.PI + arrowHeadAngle - radians);

  return (
    <div className="vector-visualization" aria-label="Vector visualization">
      <div className="vector-header">
        <span className="vector-title">Vector</span>
      </div>

      <div className="vector-canvas"
           style={{ position: "relative", width: 300, height: 250 }}
           aria-label="Vector diagram">
        {/* Arrow line */}
        <line
          x1={150}
          y1={125}
          x2={150 + endX}
          y2={125 - endY}
          stroke="var(--primary)"
          strokeWidth={2}
          strokeLinecap="round"
        />

        {/* Arrowhead */}
        <g stroke="var(--primary)" strokeWidth={2}>
          <line x1={150 + endX} y1={125 - endY} x2={leftX} y2={leftY} />
          <line x1={150 + endX} y1={125 - endY} x2={rightX} y2={rightY} />
        </g>

        {/* Magnitude label */}
        <text
          x={150 + endX + 10}
          y={125 - endY - 10}
          fontSize={12}
          fill="var(--foreground)"
        >
          {magnitude}
        </text>

        {/* Direction label */}
        <text
          x={150 + endX + 10}
          y={125 - endY + 20}
          fontSize={12}
          fill="var(--foreground)"
        >
          {direction}°
        </text>

        {/* Origin point */}
        <circle cx={150} cy={125} r={4} fill="var(--primary)" />
      </div>

      <div className="vector-details mt-2">
        <div className="detail-row">
          <span className="detail-label">Magnitude</span>
          <span className="detail-value">{magnitude}</span>
        </div>
        {label && (
          <div className="detail-row">
            <span className="detail-label">Label</span>
            <span className="detail-value">{label}</span>
          </div>
        )}
        <div className="detail-row">
          <span className="detail-label">Direction</span>
          <span className="detail-value">{direction}°</span>
        </div>
      </div>
    </div>
  );
}
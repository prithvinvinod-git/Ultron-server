/**
 * Matrix Visualization Component
 *
 * Renders a 2D matrix of numeric values as a grid with color-coded cells.
 */

"use client";

import { useRef, useState, useEffect } from "react";
import { cn } from "@/lib/utils";

/** Matrix data specification */
export interface MatrixData {
  rows: number;
  cols: number;
  values: number[][];
}

/** Matrix visualization props */
export interface MatrixVisualizationProps {
  data: MatrixData;
  controls?: any;
  state?: any;
  reducedMotion?: boolean;
}

/** Matrix cell color based on value */
function getCellColor(value: number): string {
  // Color gradient from cool to warm based on value
  const normalized = Math.max(-1, Math.min(1, value));
  const hue = normalized < 0 ? 210 + normalized * 120 : 30 + normalized * 90;
  return `hsl(${hue}, 70%, ${60 + normalized * 20}%)`;
}

/** Matrix Visualization Component */
export function MatrixVisualization({
  data,
  controls,
  state,
  reducedMotion,
}: MatrixVisualizationProps) {
  const [rows, setRows] = useState(data.rows || 1);
  const [cols, setCols] = useState(data.cols || 1);
  const [values, setValues] = useState(data.values || []);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setRows(data.rows || 1);
    setCols(data.cols || 1);
    setValues(data.values || []);
    setMounted(true);
  }, [data]);

  if (!mounted) {
    return <div className="matrix-placeholder">Loading matrix...</div>;
  }

  // Pad values if needed
  const paddedValues = data.values || Array.from({ length: rows }, () =>
    Array.from({ length: cols }, () => 0)
  );

  return (
    <div className="matrix-visualization" aria-label="Matrix visualization">
      <div className="matrix-header">
        <span className="matrix-title">Matrix</span>
      </div>

      <div className="matrix-grid"
           style={{
             display: "grid",
             gridTemplateColumns: `repeat(${cols}, 60px)`,
             gap: 2
           }}
           aria-label="Matrix grid">
        {paddedValues.map((row, rowIdx) =>
          row.map((value, colIdx) => (
            <div
              key={`${rowIdx}-${colIdx}`}
              className="matrix-cell"
              style={{ backgroundColor: getCellColor(value) }}
              aria-label={`Matrix [${rowIdx},${colIdx}]: ${value}`}
            >
              {value}
            </div>
          ))
        )}
      </div>

      <div className="matrix-details mt-2">
        <div className="detail-row">
          <span className="detail-label">Rows</span>
          <span className="detail-value">{rows}</span>
        </div>
        <div className="detail-row">
          <span className="detail-label">Cols</span>
          <span className="detail-value">{cols}</span>
        </div>
      </div>
    </div>
  );
}
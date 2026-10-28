/**
 * Equation Visualization Component
 *
 * Renders LaTeX-formatted equations. Uses simple text rendering since
 * KaTeX/MathJax would require additional dependencies.
 */

"use client";

import { useRef, useState, useEffect } from "react";

/** Equation data specification */
export interface EquationData {
  latex: string;
}

/** Equation visualization props */
export interface EquationVisualizationProps {
  data: EquationData;
  controls?: any;
  state?: any;
  reducedMotion?: boolean;
}

/** Equation Visualization Component */
export function EquationVisualization({
  data,
  controls,
  state,
  reducedMotion,
}: EquationVisualizationProps) {
  const [latex, setLatex] = useState(data.latex || "");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted) {
    return <div className="equation-placeholder">Loading equation...</div>;
  }

  // Simple LaTeX rendering - display the LaTeX source with math formatting hints
  // In production, this would use KaTeX or MathJax
  const renderedLatex = latex.replace(
    /\\\((.*?)\\\)/gs,
    '<span className="math-inline">$1</span>'
  ).replace(
    /\\\[(.*?)\\]/gs,
    '<span className="math-display">$1</span>'
  );

  return (
    <div className="equation-visualization" aria-label="Equation visualization">
      <div className="equation-header">
        <span className="equation-title">Equation</span>
      </div>

      <div className="equation-content"
           aria-live="polite"
           role="status">
        <pre className="latex-source text-[12px] leading-relaxed whitespace-pre wrap">
          {latex}
        </pre>
        <div
          className="math-rendered mt-2"
          dangerouslySetInnerHTML={{ __html: renderedLatex }}
        />
      </div>
    </div>
  );
}
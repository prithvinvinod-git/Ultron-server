/**
 * Interactive Table Visualization Component
 *
 * Renders a sortable/filterable table with column headers and rows.
 */

"use client";

import { useRef, useState, useEffect } from "react";
import { cn } from "@/lib/utils";

/** Column definition */
export interface TableColumn {
  header: string;
  field: string;
  [key: string]: unknown;
}

/** Row data */
export interface TableRow {
  [key: string]: unknown;
}

/** Interactive table data */
export interface InteractiveTableData {
  columns: TableColumn[];
  rows: TableRow[];
}

/** Interactive table visualization props */
export interface InteractiveTableVisualizationProps {
  data: InteractiveTableData;
  controls?: any;
  state?: any;
  reducedMotion?: boolean;
}

/** Interactive Table Visualization Component */
export function InteractiveTableVisualization({
  data,
  controls,
  state,
  reducedMotion,
}: InteractiveTableVisualizationProps) {
  const [columns, setColumns] = useState(data.columns || []);
  const [rows, setRows] = useState(data.rows || []);
  const [sortedColumn, setSortedColumn] = useState<string | null>(null);
  const [sortedDesc, setSortedDesc] = useState<boolean>(false);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setColumns(data.columns || []);
    setRows(data.rows || []);
    setMounted(true);
  }, [data]);

  if (!mounted) {
    return <div className="table-placeholder">Loading table...</div>;
  }

  // Get sorted rows
  const sortedRows = useMemo(() => {
    if (!sortedColumn) return rows;
    return [...rows].sort((a, b) => {
      const aVal = a[sortedColumn];
      const bVal = b[sortedColumn];
      if (typeof aVal === "string" && typeof bVal === "string") {
        return sortedDesc ? bVal.localeCompare(aVal) : aVal.localeCompare(bVal);
      }
      return sortedDesc ? Number(bVal) - Number(aVal) : Number(aVal) - Number(bVal);
    });
  }, [sortedColumn, sortedDesc, rows]);

  // Column class for sorting indicator
  function getColumnClass(field: string): string {
    if (sortedColumn === field) {
      return sortedDesc ? "sorted-desc" : "sorted-asc";
    }
    return "";
  }

  return (
    <div className="interactive-table-visualization" aria-label="Interactive table">
      <div className="table-header">
        <div className="table-row">
          {columns.map((col, i) => (
            <div
              key={col.field}
              className={`table-header-col ${getColumnClass(col.field)}`}
              onClick={() => setSortedColumn(col.field).then(() => setSortedDesc(prev => !prev))}
              role="button"
              tabIndex={0}
              aria-label={`Sort by ${col.header}`}
            >
              {col.header}
              {sortedColumn === col.field && (
                <svg className="sort-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                  {sortedDesc ? (
                    <path d="M6 9l6 6 6-6" />
                  ) : (
                    <path d="M6 15l6-6 6-6" />
                  )}
                </svg>
              )}
            </div>
          ))}
        </div>
      </div>

      <div className="table-body" aria-label="Table body">
        <div className="table-row" style={{ display: "grid", gridTemplateColumns: `repeat(${columns.length}, 1fr)` }}>
          {sortedRows.map((row, rowIdx) => (
            <div key={rowIdx} className="table-cell" style={{}}>
              {columns.map((col, colIdx) => (
                <span key={col.field} style={{}}>
                  {row[col.field] ?? ""}
                </span>
              ))}
            </div>
          ))}
        </div>
      </div>

      <div className="table-footer">
        <div className="detail-row">
          <span className="detail-label">Rows</span>
          <span className="detail-value">{rows.length}</span>
        </div>
        <div className="detail-row">
          <span className="detail-label">Columns</span>
          <span className="detail-value">{columns.length}</span>
        </div>
      </div>
    </div>
  );
}
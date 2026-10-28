/**
 * Chart Visualization Component
 *
 * Renders various chart types (bar, line, pie, area, scatter) using Recharts.
 * This is a generic chart renderer for the GenUI system.
 */

"use client";

import { useEffect, useRef, useState } from "react";
import { LineChart, Line, CartesianGrid, Tooltip, ResponsiveContainer, XAxis, YAxis } from "recharts";
import { BarChart, Bar, Cell } from "recharts";
import { PieChart, Pie, Cell as PieCell, Responsive as ResponsivePie } from "recharts";
import { AreaChart, Area, XAxis as AreaXAxis, YAxis as AreaYAxis, CartesianGrid as AreaCartesianGrid } from "recharts";
import { ScatterChart, Cell as ScatterCell, XAxis as ScatterXAxis, YAxis as ScatterYAxis } from "recharts";
import { booleanPropType } from "prop-types";

/** Chart type enum */
export type ChartType = "bar" | "line" | "pie" | "area" | "scatter";

/** Chart data point */
export interface ChartPoint {
  name: string;
  value: number;
  [key: string]: unknown;
}

/** Chart specification */
export interface ChartData {
  type: ChartType;
  data: ChartPoint[];
}

/** Chart visualization props */
export interface ChartVisualizationProps {
  data: ChartData;
  controls?: any;
  state?: any;
  reducedMotion?: boolean;
}

/** Chart Visualization Component */
export function ChartVisualization({
  data,
  controls,
  state,
  reducedMotion,
}: ChartVisualizationProps) {
  const [mounted, setMounted] = useState(false);
  const chartRef = useRef<any>(null);

  useEffect(() => {
    setMounted(true);
    return () => {
      // Cleanup chart references
    };
  }, []);

  const chartType = data.type;
  const points = data.data || [];

  if (!points.length || !mounted) {
    return (
      <div className="chart-placeholder">
        <span className="text-muted-foreground">
          No chart data available
        </span>
      </div>
    );
  }

  // Render different chart types
  switch (chartType) {
    case "bar": {
      return (
        <ResponsiveContainer width="100%" height={300}>
          <BarChart data={points} margin={{ top: 20, right: 30, left: 0, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="name" stroke={undefined} label={{ value: "Category" }} />
            <YAxis label={{ value: "Value" }} />
            <Tooltip />
            <Bar dataKey="value" fill="var(--primary)" stroke={undefined} radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      );
    }

    case "line": {
      return (
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={points} margin={{ top: 20, right: 30, left: 0, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="name" stroke={undefined} label={{ value: "Category" }} />
            <YAxis label={{ value: "Value" }} />
            <Tooltip />
            <Line type="monotone" dataKey="value" stroke="var(--primary)" strokeWidth={2} />
            <Dot dataKey="value" fill="var(--primary)" r={4} />
          </LineChart>
        </ResponsiveContainer>
      );
    }

    case "pie": {
      return (
        <ResponsivePie width="100%" height={300}>
          <Pie
            data={points}
            dataKey="value"
            fill="#8884d8"
            label
            labelLine={true}
          >
            {points.map((point, index) => (
              <PieCell key={point.name} name={point.name} value={point.value} />
            ))}
          </Pie>
        </ResponsivePie>
      );
    }

    case "area": {
      return (
        <ResponsiveContainer width="100%" height={300}>
          <AreaChart data={points} margin={{ top: 20, right: 30, left: 0, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="name" stroke={undefined} label={{ value: "Category" }} />
            <YAxis label={{ value: "Value" }} />
            <Tooltip />
            <Area type="monotone" dataKey="value" fill="var(--primary)" opacity={0.6} />
            <Dot dataKey="value" fill="var(--primary)" r={4} />
          </AreaChart>
        </ResponsiveContainer>
      );
    }

    case "scatter": {
      return (
        <ResponsiveContainer width="100%" height={300}>
          <ScatterChart data={points} margin={{ top: 20, right: 30, left: 0, bottom: 5 }}>
            <XAxis dataKey="name" stroke={undefined} label={{ value: "X" }} />
            <YAxis label={{ value: "Y" }} />
            <Tooltip />
            <Cell dataKey="value" r={5} fill="var(--primary)" />
          </ScatterChart>
        </ResponsiveContainer>
      );
    }

    default: {
      return (
        <div className="chart-placeholder">
          <span className="text-muted-foreground">
            Unknown chart type: {chartType}
          </span>
        </div>
      );
    }
  }
}
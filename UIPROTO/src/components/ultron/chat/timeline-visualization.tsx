/**
 * Timeline Visualization Component
 *
 * Renders chronological events as a timeline with dates and labels.
 */

"use client";

import { useRef, useState, useEffect } from "react";
import { cn } from "@/lib/utils";

/** Timeline event data */
export interface TimelineEvent {
  label: string;
  date: string;
  description?: string;
  [key: string]: unknown;
}

/** Timeline data specification */
export interface TimelineData {
  events: TimelineEvent[];
}

/** Timeline visualization props */
export interface TimelineVisualizationProps {
  data: TimelineData;
  controls?: any;
  state?: any;
  reducedMotion?: boolean;
}

/** Timeline Visualization Component */
export function TimelineVisualization({
  data,
  controls,
  state,
  reducedMotion,
}: TimelineVisualizationProps) {
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    setEvents(data.events || []);
  }, [data]);

  if (!mounted || !events.length) {
    return (
      <div className="timeline-placeholder">
        <span className="text-muted-foreground">
          No timeline data available
        </span>
      </div>
    );
  }

  // Sort events by date
  const sortedEvents = [...events].sort((a, b) =>
    a.date.localeCompare(b.date)
  );

  return (
    <div className="timeline-visualization" aria-label="Timeline visualization">
      <div className="timeline-header">
        <span className="timeline-title">Timeline</span>
      </div>

      <div className="timeline-container">
        {sortedEvents.map((event, i) => (
          <div
            key={event.label}
            className="timeline-event"
            role="button"
            tabIndex={0}
            onClick={() => {/* Navigate to event detail */}
            }
          >
            <div className="timeline-dot" />
            <div className="timeline-content">
              <div className="timeline-label">{event.label}</div>
              {event.description && (
                <div className="timeline-description">{event.description}</div>
              )}
              <div className="timeline-date">{formatDate(event.date)}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Format date string */
function formatDate(dateStr: string): string {
  try {
    const date = new Date(dateStr);
    if (isNaN(date.getTime())) return dateStr;
    return date.toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return dateStr;
  }
}
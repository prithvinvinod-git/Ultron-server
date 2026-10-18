/**
 * /metrics diagnostics (T508).
 *
 * `GET /metrics` already exposes the Prometheus text exposition (§32); rather
 * than add a second JSON metric source the console parses that text itself
 * (D048). The parser is deliberately small — enough for the exposition format
 * this server emits — and is unit-tested for comments, labels and empty input.
 */

import { useMemo } from "react";
import { Gauge } from "lucide-react";

import { useMetrics } from "@/lib/queries";

export interface MetricSample {
  name: string;
  labels: Record<string, string>;
  value: number;
}

function parseLabels(inner: string): Record<string, string> {
  const labels: Record<string, string> = {};
  const pattern = /([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"/g;
  let match = pattern.exec(inner);
  while (match) {
    const key = match[1] ?? "";
    const raw = match[2] ?? "";
    labels[key] = raw.replace(/\\(["\\n])/g, (_, ch: string) => (ch === "n" ? "\n" : ch));
    match = pattern.exec(inner);
  }
  return labels;
}

/** Parse a Prometheus text exposition into samples; unparsable lines are dropped. */
export function parsePrometheus(text: string): MetricSample[] {
  const samples: MetricSample[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;

    let name: string;
    let labels: Record<string, string> = {};
    let rest: string;
    const braceOpen = line.indexOf("{");
    if (braceOpen !== -1) {
      const braceClose = line.indexOf("}", braceOpen);
      if (braceClose === -1) continue;
      name = line.slice(0, braceOpen).trim();
      labels = parseLabels(line.slice(braceOpen + 1, braceClose));
      rest = line.slice(braceClose + 1);
    } else {
      const space = line.indexOf(" ");
      if (space === -1) continue;
      name = line.slice(0, space);
      rest = line.slice(space);
    }
    if (!name) continue;

    const valueToken = rest.trim().split(/\s+/)[0] ?? "";
    const value = Number.parseFloat(valueToken);
    if (Number.isNaN(value)) continue;
    samples.push({ name, labels, value });
  }
  return samples;
}

function labelText(labels: Record<string, string>): string {
  const entries = Object.entries(labels);
  return entries.length ? `{${entries.map(([key, value]) => `${key}=${value}`).join(", ")}}` : "";
}

function formatValue(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(3);
}

export function MetricsPanel() {
  const { data, isLoading, error } = useMetrics();
  const samples = useMemo(() => (data ? parsePrometheus(data) : []), [data]);
  const grouped = useMemo(() => {
    const map = new Map<string, MetricSample[]>();
    for (const sample of samples) {
      const list = map.get(sample.name);
      if (list) list.push(sample);
      else map.set(sample.name, [sample]);
    }
    return [...map.entries()];
  }, [samples]);

  return (
    <section className="hud-panel">
      <div className="panel-heading">
        <h2>METRICS</h2>
        <span>{samples.length} samples</span>
        <Gauge size={13} />
      </div>

      {error ? <div className="panel-empty">metrics unavailable — disabled or unscrapable</div> : null}
      {isLoading && !data ? <div className="panel-empty">loading…</div> : null}
      {data && samples.length === 0 ? <div className="panel-empty">no metrics exposed</div> : null}

      {grouped.length ? (
        <div className="metric-list">
          {grouped.slice(0, 12).map(([name, list]) => (
            <div key={name} className="metric-group">
              <div className="metric-name">{name}</div>
              {list.slice(0, 4).map((sample, index) => (
                <div key={`${name}-${index}`} className="event-row">
                  <span className="event-type">{formatValue(sample.value)}</span>
                  <small>{labelText(sample.labels)}</small>
                </div>
              ))}
              {list.length > 4 ? <small className="metric-more">+{list.length - 4} more</small> : null}
            </div>
          ))}
        </div>
      ) : null}

      <div className="panel-foot">
        <span>{grouped.length} metric names</span>
        <span>PROMETHEUS</span>
      </div>
    </section>
  );
}

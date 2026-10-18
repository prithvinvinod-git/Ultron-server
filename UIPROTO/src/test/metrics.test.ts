import { describe, expect, it } from "vitest";

import { parsePrometheus } from "@/components/ultron/metrics";

describe("parsePrometheus", () => {
  it("returns nothing for empty input", () => {
    expect(parsePrometheus("")).toEqual([]);
    expect(parsePrometheus("\n\n  \n")).toEqual([]);
  });

  it("skips comments, HELP and TYPE lines", () => {
    const text = [
      "# HELP ultron_requests_total Total requests.",
      "# TYPE ultron_requests_total counter",
      "ultron_requests_total 42",
    ].join("\n");
    expect(parsePrometheus(text)).toEqual([{ name: "ultron_requests_total", labels: {}, value: 42 }]);
  });

  it("parses labelled samples", () => {
    const text = 'http_requests_total{method="GET",code="200"} 17';
    expect(parsePrometheus(text)).toEqual([
      { name: "http_requests_total", labels: { method: "GET", code: "200" }, value: 17 },
    ]);
  });

  it("parses float values and tolerates trailing timestamps", () => {
    const text = "process_seconds 0.125 1699999999";
    expect(parsePrometheus(text)).toEqual([
      { name: "process_seconds", labels: {}, value: 0.125 },
    ]);
  });

  it("drops lines that are not samples", () => {
    const text = ["garbage-without-value", "{", "metric_without_value", "ok_metric 1"].join("\n");
    expect(parsePrometheus(text)).toEqual([{ name: "ok_metric", labels: {}, value: 1 }]);
  });
});

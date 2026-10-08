import { describe, expect, it } from "vitest";

import type { MetricsTimeseriesPoint } from "@/types/admin";
import { bucketLabels, bucketOffsetMinutes, fillTimeseries, formatOffset } from "@/utils/metricsBuckets";

function row(bucket: string, queries: number): MetricsTimeseriesPoint {
  return {
    bucket,
    queries,
    failed: 0,
    answered: queries,
    unconfirmed: 0,
    out_of_scope: 0,
    doc_delivered: 0,
    direct_answer_rate: 1,
    needs_information_rate: 0,
    out_of_scope_rate: 0,
    p95_ms: 100,
    llm_tokens: 0,
    rerank_tokens: 0,
  };
}

describe("metricsBuckets", () => {
  it("reads the backend bucket offset", () => {
    expect(bucketOffsetMinutes("2026-08-20T00:00:00Z")).toBe(0);
    expect(bucketOffsetMinutes("2026-08-20T00:00:00+08:00")).toBe(480);
    expect(bucketOffsetMinutes("2026-08-20T00:00:00-05:30")).toBe(-330);
    expect(bucketOffsetMinutes("2026-08-20T00:00:00")).toBe(0);
    expect(formatOffset(480)).toBe("UTC+08:00");
    expect(formatOffset(-330)).toBe("UTC-05:30");
  });

  it("fills missing days with zero buckets across the requested range", () => {
    const filled = fillTimeseries(
      [row("2026-08-20T00:00:00+00:00", 5), row("2026-08-23T00:00:00+00:00", 2)],
      { from: "2026-08-19T10:00:00Z", to: "2026-08-24T09:00:00Z", granularity: "day" },
    );
    expect(filled.map((item) => [item.bucket, item.queries, item.filled])).toEqual([
      ["2026-08-19T00:00:00+00:00", 0, true],
      ["2026-08-20T00:00:00+00:00", 5, false],
      ["2026-08-21T00:00:00+00:00", 0, true],
      ["2026-08-22T00:00:00+00:00", 0, true],
      ["2026-08-23T00:00:00+00:00", 2, false],
      ["2026-08-24T00:00:00+00:00", 0, true],
    ]);
  });

  it("aligns generated buckets to a non-UTC backend offset and hourly granularity", () => {
    const daily = fillTimeseries(
      [row("2026-08-20T00:00:00+08:00", 1)],
      { from: "2026-08-18T20:00:00Z", to: "2026-08-20T03:00:00Z", granularity: "day" },
    );
    expect(daily.map((item) => item.bucket)).toEqual([
      "2026-08-19T00:00:00+08:00",
      "2026-08-20T00:00:00+08:00",
    ]);

    const hourly = fillTimeseries(
      [row("2026-08-20T05:00:00Z", 3)],
      { from: "2026-08-20T03:30:00Z", to: "2026-08-20T06:10:00Z", granularity: "hour" },
    );
    expect(hourly.map((item) => item.bucket)).toEqual([
      "2026-08-20T03:00:00Z",
      "2026-08-20T04:00:00Z",
      "2026-08-20T05:00:00Z",
      "2026-08-20T06:00:00Z",
    ]);
  });

  it("keeps every day across a backend DST change instead of overwriting a neighbour", () => {
    // Europe/Berlin: +02:00 until 2026-10-25, +01:00 afterwards.
    const filled = fillTimeseries(
      [
        row("2026-10-24T00:00:00+02:00", 1),
        row("2026-10-25T00:00:00+02:00", 2),
        row("2026-10-26T00:00:00+01:00", 3),
        row("2026-10-27T00:00:00+01:00", 4),
      ],
      { from: "2026-10-23T22:00:00Z", to: "2026-10-27T12:00:00Z", granularity: "day" },
    );
    expect(filled.map((item) => [item.bucket.slice(0, 10), item.queries, item.filled])).toEqual([
      ["2026-10-24", 1, false],
      ["2026-10-25", 2, false],
      ["2026-10-26", 3, false],
      ["2026-10-27", 4, false],
    ]);
  });

  it("keeps hourly buckets distinct across the repeated DST hour", () => {
    const filled = fillTimeseries(
      [row("2026-10-25T02:00:00+02:00", 1), row("2026-10-25T02:00:00+01:00", 2)],
      { from: "2026-10-25T00:00:00Z", to: "2026-10-25T01:30:00Z", granularity: "hour" },
    );
    expect(filled.filter((item) => !item.filled).map((item) => item.queries)).toEqual([1, 2]);
  });

  it("keeps returned buckets that fall outside the requested range", () => {
    const filled = fillTimeseries(
      [row("2026-08-10T00:00:00Z", 4)],
      { from: "2026-08-12T00:00:00Z", to: "2026-08-13T00:00:00Z", granularity: "day" },
    );
    expect(filled[0]).toMatchObject({ bucket: "2026-08-10T00:00:00Z", queries: 4, filled: false });
    expect(filled).toHaveLength(3);
  });

  it("returns nothing when the backend has no rows", () => {
    expect(fillTimeseries([], { from: "2026-08-12T00:00:00Z", to: "2026-08-13T00:00:00Z", granularity: "day" })).toEqual([]);
  });

  it("labels buckets in their own time zone", () => {
    expect(bucketLabels("2026-10-02T00:00:00+00:00", "day")).toEqual({ date: "10-02", axis: "10-02", title: "2026-10-02 周五" });
    expect(bucketLabels("2026-10-02T23:00:00Z", "hour")).toEqual({ date: "10-02", axis: "23:00", title: "10-02 23:00–00:00" });
  });
});

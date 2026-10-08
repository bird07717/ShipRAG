import type { MetricsTimeseriesPoint } from "@/types/admin";

export type TrendGranularity = "day" | "hour";

export interface FilledTimeseriesPoint extends MetricsTimeseriesPoint {
  /** True when the backend returned no row for this bucket (zero traffic). */
  filled: boolean;
}

export const GRANULARITY_MS: Record<TrendGranularity, number> = {
  day: 86_400_000,
  hour: 3_600_000,
};

// Guards against pathological ranges; 92 days × 24 hours is well below this.
const MAX_BUCKETS = 2_400;
const OFFSET_PATTERN = /(Z|[+-]\d{2}:?\d{2})$/;

/**
 * Minutes east of UTC encoded in an ISO bucket string. The backend truncates
 * buckets in the database session time zone, so buckets carry that offset.
 */
export function bucketOffsetMinutes(bucket: string): number {
  const match = OFFSET_PATTERN.exec(bucket);
  if (!match || match[1] === "Z") return 0;
  const digits = match[1]!.replace(":", "");
  const sign = digits.startsWith("-") ? -1 : 1;
  return sign * (Number(digits.slice(1, 3)) * 60 + Number(digits.slice(3, 5)));
}

/**
 * Start (epoch ms) of the bucket containing `ms`, using the backend's bucket
 * offset so day buckets begin at the server's local midnight.
 */
export function alignBucketStart(ms: number, granularity: TrendGranularity, offsetMinutes: number): number {
  const step = GRANULARITY_MS[granularity];
  const offsetMs = offsetMinutes * 60_000;
  return Math.floor((ms + offsetMs) / step) * step - offsetMs;
}

export function formatOffset(minutes: number): string {
  const sign = minutes < 0 ? "-" : "+";
  const absolute = Math.abs(minutes);
  const hours = String(Math.floor(absolute / 60)).padStart(2, "0");
  return `UTC${sign}${hours}:${String(absolute % 60).padStart(2, "0")}`;
}

function formatBucket(ms: number, offsetMinutes: number, zulu: boolean): string {
  const shifted = new Date(ms + offsetMinutes * 60_000).toISOString().slice(0, 19);
  if (zulu) return `${shifted}Z`;
  const sign = offsetMinutes < 0 ? "-" : "+";
  const absolute = Math.abs(offsetMinutes);
  const suffix = `${String(Math.floor(absolute / 60)).padStart(2, "0")}:${String(absolute % 60).padStart(2, "0")}`;
  return `${shifted}${sign}${suffix}`;
}

function emptyPoint(bucket: string): FilledTimeseriesPoint {
  return {
    bucket,
    queries: 0,
    failed: 0,
    answered: 0,
    unconfirmed: 0,
    out_of_scope: 0,
    doc_delivered: 0,
    direct_answer_rate: 0,
    needs_information_rate: 0,
    out_of_scope_rate: 0,
    p95_ms: 0,
    llm_tokens: 0,
    rerank_tokens: 0,
    filled: true,
  };
}

/**
 * The backend only returns buckets that contain traces. Fill the missing
 * buckets with zeros so the axis is continuous and quiet days stay visible,
 * aligning generated buckets to the same offset the backend used.
 */
export function fillTimeseries(
  points: MetricsTimeseriesPoint[],
  range: { from: string; to: string; granularity: TrendGranularity },
): FilledTimeseriesPoint[] {
  if (!points.length) return [];
  const step = GRANULARITY_MS[range.granularity];
  const first = points[0]!.bucket;
  const offsetMinutes = bucketOffsetMinutes(first);
  const zulu = first.endsWith("Z");
  const align = (ms: number) => alignBucketStart(ms, range.granularity, offsetMinutes);
  // Day buckets are matched by their own local date, so a backend time zone
  // with DST (offset changes mid-range) cannot shift a day onto its
  // neighbour; hour buckets stay whole-hour instants across DST changes.
  const keyOf = (bucket: string, ms: number) =>
    range.granularity === "day" ? bucket.slice(0, 10) : String(align(ms));

  const byKey = new Map<string, { point: MetricsTimeseriesPoint; ms: number }>();
  for (const point of points) {
    const ms = new Date(point.bucket).getTime();
    if (Number.isFinite(ms)) byKey.set(keyOf(point.bucket, ms), { point, ms });
  }
  const instants = [...byKey.values()].map((entry) => entry.ms).sort((a, b) => a - b);
  if (!instants.length) return [];
  const fromMs = new Date(range.from).getTime();
  const toMs = new Date(range.to).getTime();
  const start = Math.min(Number.isFinite(fromMs) ? align(fromMs) : align(instants[0]!), align(instants[0]!));
  const endExclusive = Math.max(Number.isFinite(toMs) ? toMs : 0, instants[instants.length - 1]! + 1);

  const filled: Array<{ ms: number; point: FilledTimeseriesPoint }> = [];
  const used = new Set<string>();
  for (let ms = start; ms < endExclusive && filled.length < MAX_BUCKETS; ms += step) {
    const bucket = formatBucket(ms, offsetMinutes, zulu);
    const key = keyOf(bucket, ms);
    const match = byKey.get(key);
    if (match && !used.has(key)) {
      used.add(key);
      filled.push({ ms: match.ms, point: { ...match.point, filled: false } });
    } else if (!match) {
      filled.push({ ms, point: emptyPoint(bucket) });
    }
  }
  // Never drop a returned bucket the grid did not reach (e.g. MAX_BUCKETS).
  for (const [key, entry] of byKey) {
    if (!used.has(key)) filled.push({ ms: entry.ms, point: { ...entry.point, filled: false } });
  }
  return filled.sort((a, b) => a.ms - b.ms).map((entry) => entry.point);
}

const WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

/** Axis and tooltip labels in the bucket's own (backend) time zone. */
export function bucketLabels(
  bucket: string,
  granularity: TrendGranularity,
): { date: string; axis: string; title: string } {
  const year = Number(bucket.slice(0, 4));
  const month = Number(bucket.slice(5, 7));
  const day = Number(bucket.slice(8, 10));
  const date = bucket.slice(5, 10);
  if (granularity === "hour") {
    const hour = bucket.slice(11, 13);
    const next = String((Number(hour) + 1) % 24).padStart(2, "0");
    return { date, axis: `${hour}:00`, title: `${date} ${hour}:00–${next}:00` };
  }
  const weekday = WEEKDAYS[new Date(Date.UTC(year, month - 1, day)).getUTCDay()] ?? "";
  return { date, axis: date, title: `${bucket.slice(0, 10)} ${weekday}` };
}

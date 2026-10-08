<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";

import { niceAxis } from "@/utils/chartScale";

export interface TrendPoint {
  bucket: string;
  /** Short x-axis label. */
  label: string;
  /** Full bucket description for the hover tooltip. */
  title: string;
  queries: number;
  direct: number;
  needsInformation: number;
  outOfScope: number;
  failed: number;
  /** Greetings, conversation turns, cancellations and unclassified history. */
  other: number;
  /** null when the bucket has no in-scope technical sample. */
  directAnswerRate: number | null;
  needsInformationRate: number | null;
  p95Ms: number | null;
}

export type TrendLineMetric = "needsInformationRate" | "directAnswerRate" | "p95Ms" | "none";
type SeriesKey = "direct" | "needsInformation" | "outOfScope" | "other" | "failed";

const props = withDefaults(defineProps<{ points: TrendPoint[]; lineMetric?: TrendLineMetric }>(), {
  lineMetric: "needsInformationRate",
});
const emit = defineEmits<{ select: [point: TrendPoint] }>();

// Stacked bottom → top; failures sit on top so they stay visible.
const SERIES: { key: SeriesKey; label: string }[] = [
  { key: "direct", label: "直接回答" },
  { key: "needsInformation", label: "待补充信息" },
  { key: "outOfScope", label: "范围外" },
  { key: "other", label: "寒暄与对话等" },
  { key: "failed", label: "执行失败" },
];
const LINE_LABELS: Record<Exclude<TrendLineMetric, "none">, string> = {
  needsInformationRate: "待补充信息率",
  directAnswerRate: "直接回答率",
  p95Ms: "技术回答 P95 耗时",
};

const HEIGHT = 260;
const hidden = reactive<Record<SeriesKey, boolean>>({
  direct: false,
  needsInformation: false,
  outOfScope: false,
  other: false,
  failed: false,
});
const hoveredIndex = ref<number | null>(null);
const plot = ref<HTMLElement>();
const width = ref(720);
let observer: ResizeObserver | undefined;

onMounted(() => {
  if (!plot.value || typeof ResizeObserver === "undefined") return;
  observer = new ResizeObserver(([entry]) => {
    const next = Math.round(entry?.contentRect.width ?? 0);
    if (next > 0) width.value = next;
  });
  observer.observe(plot.value);
});
onBeforeUnmount(() => observer?.disconnect());

const visibleSeries = computed(() => SERIES.filter((series) => !hidden[series.key]));
const hasLine = computed(() => props.lineMetric !== "none");
const lineLabel = computed(() => (props.lineMetric === "none" ? "" : LINE_LABELS[props.lineMetric]));

function lineValue(point: TrendPoint): number | null {
  if (props.lineMetric === "none") return null;
  return point[props.lineMetric];
}

function formatLine(value: number | null): string {
  if (value === null) return "—";
  if (props.lineMetric === "p95Ms") return value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${Math.round(value)} ms`;
  return `${(value * 100).toFixed(1)}%`;
}

function roundedTop(x: number, y: number, w: number, h: number, radius: number): string {
  const r = Math.min(radius, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

const layout = computed(() => {
  const points = props.points;
  const margin = { top: 16, right: hasLine.value ? 56 : 16, bottom: 30, left: 44 };
  const plotWidth = Math.max(width.value - margin.left - margin.right, 40);
  const plotHeight = HEIGHT - margin.top - margin.bottom;
  const baseline = margin.top + plotHeight;
  const count = Math.max(points.length, 1);
  const slot = plotWidth / count;
  const barWidth = Math.min(Math.max(slot * 0.64, 2), 40);

  const totals = points.map((point) => visibleSeries.value.reduce((sum, series) => sum + point[series.key], 0));
  const { step, top: yMax } = niceAxis(Math.max(...totals, 0));
  const scaleY = (value: number) => (value / yMax) * plotHeight;
  const yTicks = Array.from({ length: Math.round(yMax / step) + 1 }, (_, i) => ({
    value: i * step,
    y: baseline - scaleY(i * step),
  }));

  const columns = points.map((point, index) => {
    const center = margin.left + slot * (index + 0.5);
    const x = center - barWidth / 2;
    let cursor = baseline;
    const stacked = visibleSeries.value
      .filter((series) => point[series.key] > 0)
      .map((series) => {
        const height = scaleY(point[series.key]);
        cursor -= height;
        return { key: series.key, x, y: cursor, width: barWidth, height };
      });
    const segments = stacked.map((segment, i) => ({
      ...segment,
      path: i === stacked.length - 1
        ? roundedTop(segment.x, segment.y, segment.width, segment.height, 3)
        : `M${segment.x},${segment.y}h${segment.width}v${segment.height}h${-segment.width}Z`,
    }));
    return { point, index, center, hitX: margin.left + slot * index, hitWidth: slot, segments };
  });

  // Line axis: rates use a fixed set of readable ceilings, latency a nice step.
  const values = points.map(lineValue);
  const maxValue = Math.max(...values.map((value) => value ?? 0), 0);
  const lineMax = props.lineMetric === "p95Ms"
    ? Math.max(niceAxis(maxValue).top, 1000)
    : [0.05, 0.1, 0.2, 0.25, 0.5, 1].find((ceiling) => ceiling >= maxValue) ?? 1;
  const scaleLine = (value: number) => baseline - (Math.min(value, lineMax) / lineMax) * plotHeight;
  let path = "";
  let penDown = false;
  const dots: { x: number; y: number; index: number }[] = [];
  values.forEach((value, index) => {
    if (value === null) {
      penDown = false;
      return;
    }
    const x = columns[index]!.center;
    const y = scaleLine(value);
    path += `${penDown ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
    penDown = true;
    dots.push({ x, y, index });
  });
  const lineTicks = [0, 0.5, 1].map((ratio) => ({
    y: baseline - ratio * plotHeight,
    label: formatLine(lineMax * ratio),
  }));

  const labelEvery = Math.max(Math.ceil(count / Math.max(Math.floor(plotWidth / 64), 1)), 1);
  return { margin, plotWidth, plotHeight, baseline, columns, yTicks, path, dots, lineTicks, labelEvery };
});

const hovered = computed(() => (hoveredIndex.value === null ? null : layout.value.columns[hoveredIndex.value] ?? null));
const tooltipStyle = computed(() => {
  if (!hovered.value) return {};
  const flip = hovered.value.center > width.value * 0.6;
  return {
    left: `${hovered.value.center}px`,
    transform: flip ? "translateX(calc(-100% - 14px))" : "translateX(14px)",
  };
});

function toggleSeries(key: SeriesKey): void {
  // Keep at least one series visible so the chart never goes blank.
  if (!hidden[key] && visibleSeries.value.length === 1) return;
  hidden[key] = !hidden[key];
}

function select(point: TrendPoint): void {
  if (point.queries > 0) emit("select", point);
}

function columnLabel(point: TrendPoint): string {
  return `${point.title}：${point.queries} 次请求，直接回答 ${point.direct}，待补充信息 ${point.needsInformation}，执行失败 ${point.failed}`;
}
</script>

<template>
  <div class="trend-chart">
    <div class="legend">
      <button
        v-for="series in SERIES"
        :key="series.key"
        type="button"
        class="legend-item"
        :class="{ off: hidden[series.key] }"
        :aria-pressed="!hidden[series.key]"
        @click="toggleSeries(series.key)"
      >
        <i class="swatch" :class="`seg-${series.key}`" />{{ series.label }}
      </button>
      <span v-if="hasLine" class="legend-item static"><i class="swatch line" />{{ lineLabel }}（右轴）</span>
      <span class="legend-hint">点击图例可隐藏分类 · 点击柱形查看该时段链路</span>
    </div>
    <div ref="plot" class="plot" @mouseleave="hoveredIndex = null">
      <svg
        :width="width"
        :height="HEIGHT"
        :viewBox="`0 0 ${width} ${HEIGHT}`"
        class="chart-svg"
        role="img"
        aria-label="外部请求结果分布与趋势图"
      >
        <g class="grid">
          <g v-for="tick in layout.yTicks" :key="tick.value">
            <line
              :x1="layout.margin.left"
              :x2="layout.margin.left + layout.plotWidth"
              :y1="tick.y"
              :y2="tick.y"
              :class="tick.value === 0 ? 'baseline' : 'gridline'"
            />
            <text :x="layout.margin.left - 8" :y="tick.y + 4" class="axis-label left">{{ tick.value }}</text>
          </g>
          <template v-if="hasLine">
            <text
              v-for="tick in layout.lineTicks"
              :key="tick.label"
              :x="layout.margin.left + layout.plotWidth + 8"
              :y="tick.y + 4"
              class="axis-label right"
            >
              {{ tick.label }}
            </text>
          </template>
        </g>
        <rect
          v-if="hovered"
          :x="hovered.hitX"
          :y="layout.margin.top"
          :width="hovered.hitWidth"
          :height="layout.plotHeight"
          class="hover-band"
        />
        <g v-for="column in layout.columns" :key="column.point.bucket" class="column">
          <path
            v-for="segment in column.segments"
            :key="segment.key"
            :d="segment.path"
            class="segment"
            :class="[`seg-${segment.key}`, { dim: hovered && hovered.index !== column.index }]"
          />
          <text
            v-if="column.index % layout.labelEvery === 0"
            :x="column.center"
            :y="layout.baseline + 18"
            class="axis-label bottom"
          >
            {{ column.point.label }}
          </text>
        </g>
        <g v-if="hasLine" class="line-layer">
          <path :d="layout.path" class="rate-line" />
          <circle
            v-for="dot in layout.dots"
            v-show="layout.dots.length <= 45 || hovered?.index === dot.index"
            :key="dot.index"
            :cx="dot.x"
            :cy="dot.y"
            :r="hovered?.index === dot.index ? 4.5 : 3"
            class="rate-dot"
          />
        </g>
        <rect
          v-for="column in layout.columns"
          :key="`hit-${column.point.bucket}`"
          :x="column.hitX"
          :y="layout.margin.top"
          :width="column.hitWidth"
          :height="layout.plotHeight"
          class="bar-hit"
          :class="{ clickable: column.point.queries > 0 }"
          :data-bucket="column.point.bucket"
          :tabindex="column.point.queries > 0 ? 0 : -1"
          :role="column.point.queries > 0 ? 'button' : undefined"
          :aria-label="columnLabel(column.point)"
          @mouseenter="hoveredIndex = column.index"
          @focus="hoveredIndex = column.index"
          @blur="hoveredIndex = null"
          @click="select(column.point)"
          @keydown.enter.prevent="select(column.point)"
          @keydown.space.prevent="select(column.point)"
        />
      </svg>
      <div
        v-if="hovered"
        class="chart-tooltip"
        :style="tooltipStyle"
        role="presentation"
      >
        <b>{{ hovered.point.title }}</b>
        <div class="tooltip-total">
          <span>请求</span><strong>{{ hovered.point.queries.toLocaleString() }}</strong>
        </div>
        <div
          v-for="series in SERIES"
          :key="series.key"
          class="tooltip-row"
          :class="{ off: hidden[series.key] }"
        >
          <span><i class="swatch" :class="`seg-${series.key}`" />{{ series.label }}</span>
          <strong>{{ hovered.point[series.key].toLocaleString() }}</strong>
        </div>
        <div class="tooltip-divider" />
        <div class="tooltip-row">
          <span>直接回答率</span><strong>{{ hovered.point.directAnswerRate === null ? "—" : `${(hovered.point.directAnswerRate * 100).toFixed(1)}%` }}</strong>
        </div>
        <div class="tooltip-row">
          <span>待补充信息率</span><strong>{{ hovered.point.needsInformationRate === null ? "—" : `${(hovered.point.needsInformationRate * 100).toFixed(1)}%` }}</strong>
        </div>
        <div class="tooltip-row">
          <span>P95 耗时</span><strong>{{ hovered.point.p95Ms === null ? "—" : `${hovered.point.p95Ms.toLocaleString()} ms` }}</strong>
        </div>
        <small>{{ hovered.point.queries > 0 ? "点击查看该时段链路" : "该时段没有外部请求" }}</small>
      </div>
    </div>
  </div>
</template>

<style scoped>
.trend-chart {
  display: grid;
  gap: 10px;
  min-width: 0;
}

.legend {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 14px;
}

.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 0;
  border: 0;
  color: var(--text-secondary);
  background: none;
  font-size: 12px;
  white-space: nowrap;
  cursor: pointer;
  transition: opacity var(--transition-fast);
}

.legend-item.static {
  cursor: default;
}

.legend-item.off {
  opacity: 0.4;
  text-decoration: line-through;
}

.legend-hint {
  margin-left: auto;
  color: var(--text-tertiary);
  font-size: 12px;
}

.swatch {
  display: inline-block;
  flex-shrink: 0;
  width: 10px;
  height: 10px;
  border-radius: 3px;
}

.swatch.line {
  height: 3px;
  border-radius: 2px;
  background: var(--chart-line);
}

.plot {
  position: relative;
  min-width: 0;
}

.chart-svg {
  display: block;
  max-width: 100%;
  overflow: visible;
  font-variant-numeric: tabular-nums;
}

.gridline {
  stroke: var(--border-subtle);
}

.baseline {
  stroke: var(--border-strong);
}

.axis-label {
  fill: var(--text-tertiary);
  font-size: 11px;
}

.axis-label.left {
  text-anchor: end;
}

.axis-label.right {
  fill: var(--chart-line);
  text-anchor: start;
}

.axis-label.bottom {
  text-anchor: middle;
}

.hover-band {
  fill: var(--bg-hover);
}

.segment {
  transition: opacity var(--transition-fast);
}

.segment.dim {
  opacity: 0.45;
}

.seg-direct { fill: var(--chart-direct); background: var(--chart-direct); }
.seg-needsInformation { fill: var(--chart-needs-info); background: var(--chart-needs-info); }
.seg-outOfScope { fill: var(--chart-out-of-scope); background: var(--chart-out-of-scope); }
.seg-other { fill: var(--chart-other); background: var(--chart-other); }
.seg-failed { fill: var(--chart-failed); background: var(--chart-failed); }

.rate-line {
  fill: none;
  stroke: var(--chart-line);
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
  pointer-events: none;
}

.rate-dot {
  fill: var(--bg-surface);
  stroke: var(--chart-line);
  stroke-width: 2;
  pointer-events: none;
}

.bar-hit {
  fill: transparent;
  outline: none;
}

.bar-hit.clickable {
  cursor: pointer;
}

.bar-hit:focus-visible {
  stroke: var(--brand-primary);
  stroke-width: 2;
}

.chart-tooltip {
  position: absolute;
  top: 8px;
  z-index: 2;
  display: grid;
  gap: 4px;
  min-width: 180px;
  padding: 10px 12px;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  background: var(--bg-surface);
  box-shadow: var(--shadow-popover);
  font-size: 12px;
  pointer-events: none;
}

.chart-tooltip b {
  margin-bottom: 2px;
  color: var(--text-primary);
  font-size: 12px;
}

.tooltip-total,
.tooltip-row {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  color: var(--text-secondary);
}

.tooltip-row span {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.tooltip-row.off {
  opacity: 0.45;
}

.tooltip-total strong,
.tooltip-row strong {
  color: var(--text-primary);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.tooltip-divider {
  height: 1px;
  margin: 2px 0;
  background: var(--border-subtle);
}

.chart-tooltip small {
  margin-top: 2px;
  color: var(--text-tertiary);
}

@media (max-width: 720px) {
  .legend-hint {
    margin-left: 0;
    flex-basis: 100%;
  }
}
</style>

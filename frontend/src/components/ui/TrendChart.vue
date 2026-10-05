<script setup lang="ts">
import { computed } from "vue";

export interface TrendPoint {
  bucket: string;
  label: string;
  queries: number;
  needsInformationRate: number;
}

const props = defineProps<{ points: TrendPoint[] }>();
const emit = defineEmits<{ select: [point: TrendPoint] }>();

const layout = computed(() => {
  const points = props.points;
  const width = 720;
  const height = 200;
  const margin = { top: 12, right: 44, bottom: 24, left: 40 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const count = Math.max(points.length, 1);
  const gap = count > 40 ? 2 : 6;
  const barWidth = Math.max(3, (plotWidth - gap * (count - 1)) / count);
  const step = barWidth + gap;
  const maxQueries = Math.max(...points.map((p) => p.queries), 1);
  const maxRate = Math.max(...points.map((p) => p.needsInformationRate), 0.01);
  // "nice" ceiling for the query axis (1/2/5 × 10^n)
  const niceMax = niceCeiling(maxQueries);
  const gridCount = 4;

  const bars = points.map((point, index) => {
    const barHeight = (point.queries / niceMax) * plotHeight;
    return {
      point,
      x: margin.left + index * step,
      y: margin.top + plotHeight - barHeight,
      width: barWidth,
      height: barHeight,
    };
  });
  const gridlines = Array.from({ length: gridCount + 1 }, (_, i) => {
    const value = (niceMax / gridCount) * i;
    const y = margin.top + plotHeight - (value / niceMax) * plotHeight;
    return { y, value: Math.round(value) };
  });
  const linePoints = points
    .map((point, index) => {
      const x = margin.left + index * step + barWidth / 2;
      const y = margin.top + plotHeight - (point.needsInformationRate / maxRate) * plotHeight;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const rateTicks = [0, 0.5, 1].map((ratio) => ({
    y: margin.top + plotHeight - ratio * plotHeight,
    label: `${Math.round(maxRate * ratio * 100)}%`,
  }));
  // thin out x labels when bars get dense
  const labelEvery = Math.ceil(count / Math.max(Math.floor(plotWidth / 56), 1));
  return {
    width,
    height,
    margin,
    plotWidth,
    plotHeight,
    bars,
    gridlines,
    linePoints,
    rateTicks,
    labelEvery,
    maxRate,
  };
});

function niceCeiling(value: number): number {
  if (value <= 5) return 5;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  for (const factor of [1, 2, 5, 10]) {
    if (factor * magnitude >= value) return factor * magnitude;
  }
  return value;
}

const percent = (value: number) => `${(value * 100).toFixed(1)}%`;
</script>

<template>
  <div class="trend-chart">
    <div class="legend">
      <span class="legend-item"><i class="swatch bar" />查询量</span>
      <span class="legend-item"><i class="swatch line" />待补充信息率</span>
      <span class="legend-hint">点击柱形查看该时段链路追踪（Trace）</span>
    </div>
    <svg
      :viewBox="`0 0 ${layout.width} ${layout.height}`"
      class="chart-svg"
      role="img"
      aria-label="查询量与待补充信息率趋势图"
    >
      <!-- gridlines + query axis labels (left) -->
      <g v-for="line in layout.gridlines" :key="line.value">
        <line
          :x1="layout.margin.left"
          :x2="layout.margin.left + layout.plotWidth"
          :y1="line.y"
          :y2="line.y"
          class="gridline"
        />
        <text :x="layout.margin.left - 8" :y="line.y + 3" class="axis-label left">
          {{ line.value }}
        </text>
      </g>
      <!-- no-answer-rate axis labels (right) -->
      <text
        v-for="tick in layout.rateTicks"
        :key="tick.label"
        :x="layout.margin.left + layout.plotWidth + 8"
        :y="tick.y + 3"
        class="axis-label right"
      >
        {{ tick.label }}
      </text>
      <!-- clickable bars -->
      <g v-for="(bar, index) in layout.bars" :key="bar.point.bucket" class="bar-group">
        <title>
          {{
            `${bar.point.label}：${bar.point.queries} 次查询，待补充信息率 ${percent(bar.point.needsInformationRate)}`
          }}
        </title>
        <rect
          :x="bar.x"
          :y="bar.y"
          :width="bar.width"
          :height="bar.height"
          rx="2"
          class="bar"
          @click="emit('select', bar.point)"
        />
        <text
          v-if="index % layout.labelEvery === 0"
          :x="bar.x + bar.width / 2"
          :y="layout.height - 8"
          class="axis-label bottom"
        >
          {{ bar.point.label }}
        </text>
      </g>
      <!-- no-answer-rate line on top of bars -->
      <polyline :points="layout.linePoints" class="rate-line" />
    </svg>
  </div>
</template>

<style scoped>
.trend-chart {
  display: grid;
  gap: 8px;
}

.legend {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 16px;
  padding: 0 4px;
}

.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--text-secondary);
  font-size: 12px;
  white-space: nowrap;
}

.legend-hint {
  margin-left: auto;
  color: var(--text-tertiary);
  font-size: 12px;
}

.swatch {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 2px;
}

.swatch.bar {
  background: var(--brand-primary);
  opacity: 0.8;
}

.swatch.line {
  height: 2px;
  border-radius: 1px;
  background: var(--el-color-danger);
}

.chart-svg {
  display: block;
  width: 100%;
  height: auto;
}

.gridline {
  stroke: var(--border-subtle);
  stroke-dasharray: 3 4;
}

.axis-label {
  fill: var(--text-tertiary);
  font-size: 10px;
}

.axis-label.left {
  text-anchor: end;
}

.axis-label.right {
  text-anchor: start;
  fill: var(--el-color-danger);
  opacity: 0.7;
}

.axis-label.bottom {
  text-anchor: middle;
}

.bar {
  fill: var(--brand-primary);
  opacity: 0.7;
  cursor: pointer;
  transition: opacity var(--transition-fast);
}

.bar:hover {
  opacity: 1;
}

.rate-line {
  fill: none;
  stroke: var(--el-color-danger);
  stroke-width: 2;
  stroke-linejoin: round;
  pointer-events: none;
}
</style>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch, type Component } from "vue";
import {
  ArrowRight,
  ChatDotRound,
  CircleCheck,
  CircleCheckFilled,
  CircleCloseFilled,
  Clock,
  InfoFilled,
  Loading,
  QuestionFilled,
  Refresh,
  RefreshRight,
  SuccessFilled,
  TrendCharts,
  Warning,
  WarningFilled,
} from "@element-plus/icons-vue";
import { adminApi } from "@/api/admin";
import { useAdminStore } from "@/composables/adminStore";
import MetricCard from "@/components/ui/MetricCard.vue";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import EmptyState from "@/components/ui/EmptyState.vue";
import ListSkeleton from "@/components/ui/ListSkeleton.vue";
import TrendChart, { type TrendLineMetric, type TrendPoint } from "@/components/ui/TrendChart.vue";
import {
  GRANULARITY_MS,
  alignBucketStart,
  bucketLabels,
  bucketOffsetMinutes,
  fillTimeseries,
  formatOffset,
  type TrendGranularity,
} from "@/utils/metricsBuckets";
import type { AssistantMetrics, KnowledgeAssistantCheck, MetricsOverview, MetricsTimeseriesPoint, TraceSummary } from "@/types/admin";

type TrendRangeKey = "24h" | "7d" | "14d" | "30d" | "90d" | "custom";
interface TrendWindow { from: string; to: string; granularity: TrendGranularity; offsetMinutes: number }
const TREND_PRESETS: { key: Exclude<TrendRangeKey, "custom">; label: string; days: number }[] = [
  { key: "24h", label: "24 小时", days: 1 },
  { key: "7d", label: "7 天", days: 7 },
  { key: "14d", label: "14 天", days: 14 },
  { key: "30d", label: "30 天", days: 30 },
  { key: "90d", label: "90 天", days: 90 },
];
const TREND_LINE_OPTIONS: { value: TrendLineMetric; label: string }[] = [
  { value: "needsInformationRate", label: "折线：待补充信息率" },
  { value: "directAnswerRate", label: "折线：直接回答率" },
  { value: "p95Ms", label: "折线：P95 耗时" },
  { value: "none", label: "不显示折线" },
];
// Mirrors the backend's metrics range limit (rag/metrics.py _MAX_RANGE_DAYS).
const TREND_MAX_DAYS = 92;
const HOURLY_MAX_DAYS = 7;
// Stage degradation becomes a to-do only when it is both frequent and not a
// one-off: at least this many occurrences and this share of attempts.
const QUALITY_ALERT_MIN_COUNT = 3;
const QUALITY_ALERT_RATE = 0.05;
const DAY_MS = 86_400_000;

const store = useAdminStore();
const overview = ref<MetricsOverview | null>(null);
const assistantMetrics = ref<AssistantMetrics | null>(null);
const readiness = ref<KnowledgeAssistantCheck | null>(null);
const timeseries = ref<MetricsTimeseriesPoint[]>([]);
const unconfirmedTraces = ref<TraceSummary[]>([]);
const metricsError = ref("");
const metricsLoading = ref(false);
const refreshing = ref(false);
const lastRefreshedAt = ref<Date | null>(null);
const drilldown = ref<{ label: string; from: string; to: string } | null>(null);
const drilldownTraces = ref<TraceSummary[]>([]);
const drilldownLoading = ref(false);
const drilldownError = ref("");
const trendRange = ref<TrendRangeKey>("7d");
const customRange = ref<[Date, Date] | null>(null);
// Last custom range actually requested; restored when a new pick is rejected.
let appliedCustomRange: [Date, Date] | null = null;
// Bucket offset learned from the backend (database session time zone);
// UTC until the first response says otherwise.
const serverOffsetMinutes = ref(0);
const trendGranularity = ref<TrendGranularity>("day");
const trendLineMetric = ref<TrendLineMetric>("needsInformationRate");
const trendWindow = ref<TrendWindow | null>(null);
const trendLoading = ref(false);
const trendError = ref("");
let autoRefreshTimer: ReturnType<typeof setInterval> | undefined;
let active = true;
let loadGeneration = 0;
let drilldownGeneration = 0;
let trendGeneration = 0;
let lastLoadedKb: string | undefined;
const QUALITY_WINDOW_DAYS = 7;
const QUALITY_ENGINE = "RAG" as const;
const QUALITY_SOURCE = "ASSISTANT_API" as const;

const knowledge = computed(() => store.knowledgeBases.find((item) => item.id === store.selectedKnowledgeId));
const scopeLabel = computed(() => knowledge.value?.name || (store.selectedKnowledgeId ? "当前知识库" : "全部知识库"));
const lastRefreshedLabel = computed(() => lastRefreshedAt.value?.toLocaleTimeString("zh-CN", { hour12: false }) || "尚未加载");
const percent = (value: number, denominator: number) => denominator > 0 ? (value * 100).toFixed(1) + "%" : "—";
const latency = (value: number, samples: number) => samples > 0 ? value.toLocaleString() + " ms" : "—";
const assistantSource = computed(() => assistantMetrics.value
  ? assistantMetrics.value.sources.find((item) => item.source === "ASSISTANT_API")
    ?? { source: "ASSISTANT_API", turns: 0, users: 0, conversations: 0, failed: 0, error_rate: 0 }
  : undefined);
const directAnswerCount = computed(() => (overview.value?.response_type_counts.ANSWERED ?? 0) + (overview.value?.response_type_counts.DOC_DELIVERED ?? 0));
const availabilityLabel = computed(() => {
  if (!store.selectedKnowledgeId) return "请选择知识库查看上线状态";
  if (!readiness.value) return "上线状态待确认";
  if (!readiness.value.assistant_online) return "尚未对外上线";
  return readiness.value.ready ? "已上线 · 可接受业务提问" : "已上线 · 暂不可用";
});
const availabilityDescription = computed(() => {
  if (!readiness.value) return "索引就绪不等于已上线；需要同时检查模型、文档理解及发布状态。";
  if (!readiness.value.ready) return "仍有运行条件未满足，请打开上线检查清单处理。";
  return readiness.value.assistant_online ? "当前运行条件已通过。回答效果仍需人工复核。" : "运行条件已通过；保存推荐问题、试问后可确认上线。";
});
const availabilityState = computed<{ tone: "success" | "danger" | "info" | "neutral"; icon: Component }>(() => {
  if (!store.selectedKnowledgeId) return { tone: "neutral", icon: InfoFilled };
  if (!readiness.value) return { tone: "neutral", icon: QuestionFilled };
  if (readiness.value.assistant_online) {
    return readiness.value.ready ? { tone: "success", icon: CircleCheckFilled } : { tone: "danger", icon: WarningFilled };
  }
  return { tone: readiness.value.ready ? "info" : "neutral", icon: InfoFilled };
});
const blockedCount = computed(() => readiness.value?.checks?.filter((item) => item.status === "BLOCKED").length ?? readiness.value?.blockers.length ?? 0);
const attentionItems = computed(() => {
  const items: { key: string; label: string; value: string; tone: "danger" | "warning" | "info"; icon: Component; action: () => void }[] = [];
  if (blockedCount.value) items.push({ key: "blocked", label: "上线运行条件待处理", value: `${blockedCount.value} 项`, tone: "danger", icon: WarningFilled, action: openPublication });
  if (knowledge.value?.rebuild_required) items.push({ key: "rebuild", label: "文档已变更，需要重建索引", value: "去处理", tone: "warning", icon: RefreshRight, action: () => store.navigate("indexes") });
  if (store.buildingCount) items.push({ key: "building", label: "索引任务正在处理", value: `${store.buildingCount} 个`, tone: "info", icon: Loading, action: () => store.navigate("indexes") });
  if (overview.value && overview.value.failed > 0) items.push({ key: "failed", label: "近 7 天技术请求执行失败", value: `${overview.value.failed} 次`, tone: "danger", icon: CircleCloseFilled, action: openFailedTurns });
  const quality = overview.value?.v2_quality;
  if (quality) {
    if (quality.document_rerank_unconfigured > 0) {
      items.push({ key: "rerank-unconfigured", label: "检索未执行文档重排（Rerank 模型不可用）", value: `${quality.document_rerank_unconfigured} 次`, tone: "danger", icon: WarningFilled, action: openQualityDiagnostics });
    }
    if (quality.document_rerank_degraded >= QUALITY_ALERT_MIN_COUNT && quality.document_rerank_degraded_rate >= QUALITY_ALERT_RATE) {
      items.push({ key: "rerank-degraded", label: "文档重排调用失败后降级", value: percent(quality.document_rerank_degraded_rate, 1), tone: "warning", icon: WarningFilled, action: openQualityDiagnostics });
    }
    if (quality.query_interpreter_degraded >= QUALITY_ALERT_MIN_COUNT && quality.query_interpreter_degraded_rate >= QUALITY_ALERT_RATE) {
      items.push({ key: "query-degraded", label: "查询理解失败后使用兜底", value: percent(quality.query_interpreter_degraded_rate, 1), tone: "warning", icon: WarningFilled, action: openQualityDiagnostics });
    }
  }
  return items;
});
const recentTraces = computed(() => store.traces.filter((item) => !store.selectedKnowledgeId || item.kb_id === store.selectedKnowledgeId).slice(0, 5));

const trendSpanDays = computed(() => {
  if (trendRange.value !== "custom") return TREND_PRESETS.find((item) => item.key === trendRange.value)!.days;
  const [start, end] = customRange.value ?? [];
  return start && end ? Math.round((startOfDay(end).getTime() - startOfDay(start).getTime()) / DAY_MS) + 1 : 0;
});
const canUseHourly = computed(() => trendSpanDays.value > 0 && trendSpanDays.value <= HOURLY_MAX_DAYS);
// "24 小时" is always hourly; one day bucket would hide the shape of the day.
const showGranularityToggle = computed(() => canUseHourly.value && trendRange.value !== "24h");
const filledTimeseries = computed(() => trendWindow.value ? fillTimeseries(timeseries.value, trendWindow.value) : []);
const chartPoints = computed<TrendPoint[]>(() => {
  const granularity = trendWindow.value?.granularity ?? "day";
  return filledTimeseries.value.map((point, index, all) => {
    const labels = bucketLabels(point.bucket, granularity);
    const dateChanged = index === 0 || all[index - 1]!.bucket.slice(0, 10) !== point.bucket.slice(0, 10);
    const direct = point.answered + point.doc_delivered;
    const inScope = direct + point.unconfirmed;
    return {
      bucket: point.bucket,
      label: granularity === "hour" && dateChanged ? `${labels.date} ${labels.axis}` : labels.axis,
      title: labels.title,
      queries: point.queries,
      direct,
      needsInformation: point.unconfirmed,
      outOfScope: point.out_of_scope,
      failed: point.failed,
      other: Math.max(point.queries - point.failed - direct - point.unconfirmed - point.out_of_scope, 0),
      directAnswerRate: inScope > 0 ? direct / inScope : null,
      needsInformationRate: inScope > 0 ? point.unconfirmed / inScope : null,
      p95Ms: point.p95_ms > 0 ? point.p95_ms : null,
    };
  });
});
const trendSummary = computed(() => {
  const points = chartPoints.value;
  if (!points.length) return null;
  const total = points.reduce((sum, point) => sum + point.queries, 0);
  const direct = points.reduce((sum, point) => sum + point.direct, 0);
  const inScope = direct + points.reduce((sum, point) => sum + point.needsInformation, 0);
  const peak = points.reduce((best, point) => (point.queries > best.queries ? point : best), points[0]!);
  // The bucket containing "now" is still filling up; leave it out of the average.
  const granularity = trendWindow.value?.granularity ?? "day";
  const windowEnd = trendWindow.value ? new Date(trendWindow.value.to).getTime() : Infinity;
  const complete = points.filter((point) => new Date(point.bucket).getTime() + GRANULARITY_MS[granularity] <= windowEnd);
  const basis = complete.length ? complete : points;
  const average = basis.reduce((sum, point) => sum + point.queries, 0) / basis.length;
  return {
    total,
    average: average.toFixed(average >= 10 ? 0 : 1),
    averageLabel: granularity === "hour" ? "每小时平均" : "日均请求",
    averageNote: complete.length && complete.length < points.length ? (granularity === "hour" ? "不含当前小时" : "不含今天") : "",
    peak,
    failed: points.reduce((sum, point) => sum + point.failed, 0),
    directRate: percent(inScope > 0 ? direct / inScope : 0, inScope),
  };
});
const trendWindowLabel = computed(() => {
  if (!trendWindow.value) return "";
  const from = store.formatDate(trendWindow.value.from);
  const to = store.formatDate(trendWindow.value.to);
  return `${from} 至 ${to}`;
});
const trendZoneLabel = computed(() => timeseries.value[0] ? formatOffset(bucketOffsetMinutes(timeseries.value[0].bucket)) : "");

function calendarDayUtc(date: Date): number {
  return Date.UTC(date.getFullYear(), date.getMonth(), date.getDate());
}

function startOfDay(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function isFutureDate(date: Date): boolean {
  return date.getTime() > Date.now();
}

/*
 * Windows start on a bucket boundary in the backend's time zone, so the chart
 * never shows a partial leading bucket: presets cover the last N whole days
 * (today included) or N×24 whole hours; picked calendar dates are read as the
 * server's calendar days, so the bars match the selected dates exactly.
 */
function resolveTrendWindow(): TrendWindow | null {
  const now = Date.now();
  const offsetMinutes = serverOffsetMinutes.value;
  const granularity: TrendGranularity = trendRange.value === "24h"
    ? "hour"
    : canUseHourly.value ? trendGranularity.value : "day";
  const window = (from: number, to: number): TrendWindow | null => to > from
    ? { from: new Date(from).toISOString(), to: new Date(to).toISOString(), granularity, offsetMinutes }
    : null;
  if (trendRange.value !== "custom") {
    const days = trendSpanDays.value;
    const from = granularity === "hour"
      ? alignBucketStart(now, "hour", offsetMinutes) - (days * 24 - 1) * GRANULARITY_MS.hour
      : alignBucketStart(now, "day", offsetMinutes) - (days - 1) * DAY_MS;
    return window(from, now);
  }
  const [start, end] = customRange.value ?? [];
  if (!start || !end) return null;
  const offsetMs = offsetMinutes * 60_000;
  return window(calendarDayUtc(start) - offsetMs, Math.min(calendarDayUtc(end) + DAY_MS - offsetMs, now));
}

const diagnosticsOpen = ref(false);
const qualitySection = ref<HTMLElement>();
function openQualityDiagnostics() {
  diagnosticsOpen.value = true;
  void nextTick(() => qualitySection.value?.scrollIntoView?.({ behavior: "smooth", block: "start" }));
}

function openPublication() {
  if (store.selectedKnowledgeId) store.navigate("knowledge", "id=" + encodeURIComponent(store.selectedKnowledgeId));
  else store.navigate("knowledge");
}

function openFailedTurns() {
  if (!overview.value || !store.hasServiceCredential) return;
  const query = new URLSearchParams({
    tab: "failed-turns", engine: QUALITY_ENGINE, source: QUALITY_SOURCE,
    from: overview.value.range.from, to: overview.value.range.to,
  });
  store.navigate("logs", query.toString());
}

async function loadMetrics() {
  if (!active || !store.hasServiceCredential) return;
  const generation = ++loadGeneration;
  const knowledgeId = store.selectedKnowledgeId;
  metricsLoading.value = true;
  metricsError.value = "";
  if (lastLoadedKb !== knowledgeId) {
    overview.value = null; assistantMetrics.value = null; readiness.value = null;
    timeseries.value = []; trendWindow.value = null; trendGeneration++;
    unconfirmedTraces.value = []; lastRefreshedAt.value = null;
    drilldownGeneration++; drilldown.value = null;
  }
  lastLoadedKb = knowledgeId;
  const to = new Date();
  const from = new Date(to.getTime() - QUALITY_WINDOW_DAYS * 86_400_000);
  const qualityParams = { knowledgeId: knowledgeId || undefined, from: from.toISOString(), to: to.toISOString(), engine: QUALITY_ENGINE, source: QUALITY_SOURCE };
  const trendResult = loadTrend();
  const results = await Promise.allSettled([
    adminApi.getMetricsOverview(qualityParams),
    adminApi.listUnconfirmedTraces(knowledgeId || undefined, qualityParams),
    adminApi.getAssistantMetrics({ knowledgeId: knowledgeId || undefined, days: QUALITY_WINDOW_DAYS }),
    knowledgeId ? adminApi.getKnowledgeAssistant(knowledgeId) : Promise.resolve(null),
  ]);
  // A newer trend request (range change) supersedes this one; that is not a failure.
  const trendValid = (await trendResult) !== "failed";
  if (!active || generation !== loadGeneration || store.selectedKnowledgeId !== knowledgeId) return;
  const [summary, questions, assistant, check] = results;
  const summaryValid = summary?.status === "fulfilled" && typeof summary.value?.total_traces === "number";
  const questionsValid = questions?.status === "fulfilled" && Array.isArray(questions.value);
  const assistantValid = assistant?.status === "fulfilled" && Array.isArray(assistant.value?.sources);
  const checkValid = check?.status === "fulfilled" && (!knowledgeId || (check.value?.knowledge_id === knowledgeId && typeof check.value.ready === "boolean"));
  if (summaryValid) overview.value = summary.value;
  if (questionsValid) unconfirmedTraces.value = questions.value;
  if (assistantValid) assistantMetrics.value = assistant.value;
  if (checkValid && knowledgeId) readiness.value = check.value;
  else readiness.value = null;
  // A failed trend shows its own warning inside the trend panel; it only keeps
  // the refresh from counting as complete.
  const othersValid = [summaryValid, questionsValid, assistantValid, checkValid].every(Boolean);
  if (!othersValid) metricsError.value = "部分质量指标暂时无法加载；已显示的数据可能仍为上次结果，请重试。";
  else if (trendValid) lastRefreshedAt.value = new Date();
  metricsLoading.value = false;
}
async function loadTrend(): Promise<"ok" | "failed" | "stale"> {
  if (!active || !store.hasServiceCredential) return "stale";
  const window = resolveTrendWindow();
  if (!window) return "stale";
  const generation = ++trendGeneration;
  const knowledgeId = store.selectedKnowledgeId;
  const isCurrent = () => active && generation === trendGeneration && store.selectedKnowledgeId === knowledgeId;
  trendLoading.value = true;
  trendError.value = "";
  try {
    const points = await adminApi.getMetricsTimeseries({
      knowledgeId: knowledgeId || undefined,
      from: window.from,
      to: window.to,
      granularity: window.granularity,
      engine: QUALITY_ENGINE,
      source: QUALITY_SOURCE,
    });
    if (!isCurrent()) return "stale";
    if (!Array.isArray(points)) throw new Error("invalid timeseries payload");
    const detected = points[0] ? bucketOffsetMinutes(points[0].bucket) : null;
    if (detected !== null && detected !== window.offsetMinutes) {
      // First contact with a non-UTC backend: realign the window once.
      serverOffsetMinutes.value = detected;
      return await loadTrend();
    }
    timeseries.value = points;
    trendWindow.value = window;
    return "ok";
  } catch {
    if (!isCurrent()) return "stale";
    trendError.value = "趋势数据暂时无法加载，已显示的图表可能仍为上次结果，请重试。";
    return "failed";
  } finally {
    if (active && generation === trendGeneration) trendLoading.value = false;
  }
}
function onTrendRangeChange() {
  if (trendRange.value === "custom") {
    if (!customRange.value) {
      const today = startOfDay(new Date());
      customRange.value = [new Date(today.getTime() - 6 * DAY_MS), today];
    }
    appliedCustomRange = customRange.value;
  } else trendGranularity.value = trendRange.value === "24h" ? "hour" : "day";
  if (!canUseHourly.value) trendGranularity.value = "day";
  void loadTrend();
}
function onCustomRangeChange() {
  if (trendSpanDays.value > TREND_MAX_DAYS) {
    // Keep the picker consistent with the chart: restore the last applied range.
    trendError.value = `时间范围不能超过 ${TREND_MAX_DAYS} 天，已恢复为上次的范围。`;
    customRange.value = appliedCustomRange;
    return;
  }
  appliedCustomRange = customRange.value;
  if (!canUseHourly.value) trendGranularity.value = "day";
  void loadTrend();
}
function autoRefresh() { if (store.hasServiceCredential && !metricsLoading.value) void loadMetrics(); }
async function refreshDashboard() {
  if (!store.hasServiceCredential || refreshing.value) return;
  refreshing.value = true;
  try {
    await Promise.all([store.refreshOperationalState(), store.loadSystemInfo()]);
    await loadMetrics();
  }
  catch (cause) { if (active) metricsError.value = cause instanceof Error ? cause.message : "刷新失败，请重试"; }
  finally { if (active) refreshing.value = false; }
}
async function openBucketDrilldown(point: TrendPoint) {
  const generation = ++drilldownGeneration;
  const knowledgeId = store.selectedKnowledgeId;
  const span = GRANULARITY_MS[trendWindow.value?.granularity ?? "day"];
  const bucketEnd = new Date(new Date(point.bucket).getTime() + span).toISOString();
  drilldown.value = { label: point.title, from: point.bucket, to: bucketEnd };
  drilldownTraces.value = []; drilldownLoading.value = true; drilldownError.value = "";
  try {
    const items = await adminApi.listTraces(knowledgeId || undefined, { from: point.bucket, to: bucketEnd, engine: QUALITY_ENGINE, source: QUALITY_SOURCE, limit: 100 });
    if (active && generation === drilldownGeneration && store.selectedKnowledgeId === knowledgeId) drilldownTraces.value = items;
  } catch (cause) { if (active && generation === drilldownGeneration) drilldownError.value = cause instanceof Error ? cause.message : "链路加载失败"; }
  finally { if (active && generation === drilldownGeneration) drilldownLoading.value = false; }
}
onMounted(() => { void loadMetrics(); autoRefreshTimer = setInterval(autoRefresh, 60_000); });
onUnmounted(() => { active = false; loadGeneration++; drilldownGeneration++; trendGeneration++; clearInterval(autoRefreshTimer); });
watch(() => store.selectedKnowledgeId, () => void loadMetrics());
watch(() => store.hasServiceCredential, (available) => {
  if (available) void loadMetrics();
  else { loadGeneration++; trendGeneration++; metricsLoading.value = false; trendLoading.value = false; }
});
</script>

<template>
  <div class="dashboard">
    <section class="dashboard-toolbar">
      <div class="scope-actions">
        <el-select
          v-model="store.selectedKnowledgeId"
          aria-label="统计知识库"
          placeholder="全部知识库"
          @change="store.selectKnowledge()"
        >
          <el-option label="全部知识库" value="" />
          <el-option
            v-for="kb in store.knowledgeBases"
            :key="kb.id"
            :label="kb.name"
            :value="kb.id"
          />
        </el-select>
        <span class="refresh-meta">{{ scopeLabel }} · 上次完整刷新 {{ lastRefreshedLabel }}</span>
      </div>
      <el-button
        :icon="Refresh"
        :loading="refreshing"
        :disabled="!store.hasServiceCredential"
        @click="refreshDashboard"
      >
        刷新状态
      </el-button>
    </section>
    <p class="scope-note">
      <el-icon><InfoFilled /></el-icon>
      <span>仅统计业务端 Assistant，不混入内部试问。每 60 秒刷新；直接回答率不是答案正确率。</span>
    </p>

    <el-alert
      v-if="metricsError"
      :title="metricsError"
      type="warning"
      :closable="false"
      show-icon
    />

    <section class="status-layout">
      <article class="panel availability-panel" :class="`tone-${availabilityState.tone}`">
        <div class="availability-head">
          <span class="availability-icon"><el-icon><component :is="availabilityState.icon" /></el-icon></span>
          <div>
            <span class="eyebrow">能否对外提供服务</span>
            <h3>{{ availabilityLabel }}</h3>
          </div>
        </div>
        <p>{{ availabilityDescription }}</p>
        <div class="status-meta">
          <span><b>{{ knowledge?.document_count ?? '—' }}</b> 份文档</span>
          <span>{{ store.activeIndex ? '当前索引 v' + store.activeIndex.version : '未确认活动索引' }}</span>
        </div>
        <el-button type="primary" plain @click="openPublication">打开上线检查清单</el-button>
      </article>
      <article class="panel attention-panel">
        <div class="attention-head">
          <span class="eyebrow">现在需要处理什么</span>
          <span v-if="attentionItems.length" class="attention-count">{{ attentionItems.length }} 项待办</span>
        </div>
        <button
          v-for="item in attentionItems"
          :key="item.key"
          type="button"
          class="attention-row"
          :class="`tone-${item.tone}`"
          @click="item.action()"
        >
          <span class="attention-icon"><el-icon :class="{ 'is-loading': item.key === 'building' }"><component :is="item.icon" /></el-icon></span>
          <span class="attention-label">{{ item.label }}</span>
          <b>{{ item.value }}</b>
          <el-icon class="attention-arrow"><ArrowRight /></el-icon>
        </button>
        <div v-if="!attentionItems.length" class="attention-empty" :class="{ known: overview && readiness }">
          <el-icon><component :is="overview && readiness ? SuccessFilled : QuestionFilled" /></el-icon>
          <p class="muted">{{ overview && readiness ? '当前未发现上述运行待办；这不代表所有回答都正确。' : '数据或知识库状态未确认，暂不能判断是否有运行待办。' }}</p>
        </div>
      </article>
    </section>

    <section class="usage-section">
      <header class="section-heading">
        <div>
          <h3>
            近 7 天使用情况
            <el-tooltip content="外部请求含寒暄和对话；直接回答率与耗时只使用技术样本（不含寒暄、无需检索的对话）。" placement="top">
              <el-icon class="heading-hint" aria-label="统计口径"><InfoFilled /></el-icon>
            </el-tooltip>
          </h3>
        </div>
        <el-button text @click="store.navigate('logs', 'tab=assistant-answers')">查看回答与优化 →</el-button>
      </header>
      <ListSkeleton
        v-if="metricsLoading && !overview && !assistantMetrics"
        variant="metrics"
        :count="4"
      />
      <div v-else class="metric-grid">
        <MetricCard
          label="外部请求轮次"
          :value="assistantSource ? assistantSource.turns.toLocaleString() : '—'"
          :context="assistantSource ? '外部用户 ' + assistantSource.users + ' · 会话 ' + assistantSource.conversations : '调用数据未加载'"
          :icon="ChatDotRound"
        />
        <MetricCard
          label="直接回答率"
          :value="overview ? percent(overview.direct_answer_rate, overview.in_scope_traces) : '—'"
          :context="overview ? '直接回答 ' + directAnswerCount + ' / 范围内技术回答 ' + overview.in_scope_traces : '技术回答数据未加载'"
          :icon="CircleCheck"
          tone="success"
        />
        <MetricCard
          label="外部执行失败率"
          :value="assistantSource ? percent(assistantSource.error_rate, assistantSource.turns) : '—'"
          :context="assistantSource ? '执行失败 ' + assistantSource.failed + ' / 外部请求 ' + assistantSource.turns : '不是答案质量评分'"
          :icon="Warning"
          :tone="assistantSource && assistantSource.error_rate > 0.05 ? 'danger' : 'default'"
        />
        <MetricCard
          label="技术回答 P95 耗时"
          :value="overview ? latency(overview.latency.p95_ms, overview.latency.samples) : '—'"
          :context="overview ? '95% 的技术样本不超过此时长 · 样本 ' + overview.latency.samples : '不含寒暄、无需检索的对话'"
          :icon="Clock"
          :tone="overview && overview.latency.p95_ms > 10000 ? 'warning' : 'default'"
        />
      </div>
      <p v-if="assistantSource?.turns === 0" class="empty-note">近 7 天暂无外部调用；无样本的比例和耗时显示“—”，不表示零错误或零耗时。</p>
    </section>

    <section class="panel trend-panel">
      <header class="panel-header">
        <div>
          <h3>请求趋势</h3>
          <span class="sub">外部 Assistant · RAG<template v-if="trendWindowLabel"> · {{ trendWindowLabel }}</template><template v-if="trendZoneLabel"> · 按 {{ trendZoneLabel }} 分桶</template></span>
        </div>
        <div class="trend-controls">
          <el-radio-group
            v-model="trendRange"
            size="small"
            aria-label="趋势时间范围"
            @change="onTrendRangeChange"
          >
            <el-radio-button v-for="preset in TREND_PRESETS" :key="preset.key" :value="preset.key">
              {{ preset.label }}
            </el-radio-button>
            <el-radio-button value="custom">自定义</el-radio-button>
          </el-radio-group>
          <el-date-picker
            v-if="trendRange === 'custom'"
            v-model="customRange"
            type="daterange"
            size="small"
            class="trend-date-picker"
            unlink-panels
            range-separator="至"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            :clearable="false"
            :disabled-date="isFutureDate"
            @change="onCustomRangeChange"
          />
          <el-radio-group
            v-if="showGranularityToggle"
            v-model="trendGranularity"
            size="small"
            aria-label="趋势时间粒度"
            @change="loadTrend"
          >
            <el-radio-button value="day">按天</el-radio-button>
            <el-radio-button value="hour">按小时</el-radio-button>
          </el-radio-group>
          <el-select
            v-model="trendLineMetric"
            size="small"
            class="trend-line-select"
            aria-label="折线指标"
          >
            <el-option
              v-for="option in TREND_LINE_OPTIONS"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
        </div>
      </header>
      <div v-loading="trendLoading && chartPoints.length > 0" class="panel-body trend-body">
        <div v-if="trendSummary" class="trend-summary">
          <div><span>区间请求</span><b>{{ trendSummary.total.toLocaleString() }}</b></div>
          <div><span>{{ trendSummary.averageLabel }}</span><b>{{ trendSummary.average }}</b><small v-if="trendSummary.averageNote">{{ trendSummary.averageNote }}</small></div>
          <div><span>峰值</span><b>{{ trendSummary.peak.queries.toLocaleString() }}</b><small>{{ trendSummary.peak.title }}</small></div>
          <div><span>直接回答率</span><b>{{ trendSummary.directRate }}</b><small>范围内技术回答</small></div>
          <div :class="{ danger: trendSummary.failed > 0 }"><span>执行失败</span><b>{{ trendSummary.failed.toLocaleString() }}</b></div>
        </div>
        <el-alert
          v-if="trendError"
          :title="trendError"
          type="warning"
          :closable="false"
          show-icon
        />
        <ListSkeleton v-if="trendLoading && !chartPoints.length" variant="chart" />
        <TrendChart
          v-else-if="chartPoints.length"
          :points="chartPoints"
          :line-metric="trendLineMetric"
          @select="openBucketDrilldown"
        />
        <EmptyState
          v-else-if="!trendError && trendWindow"
          :icon="TrendCharts"
          title="该时段没有外部请求"
          description="可以切换到更长的时间范围，或确认业务端是否已接入 Assistant API。"
        />
      </div>
    </section>

    <details class="panel advanced-details review-details">
      <summary>值得人工复核的问题<span>近期待补充条件的回答 · 展开查看</span></summary>
      <header class="panel-header"><span class="sub">以下回答需要用户补充条件，不直接等同于知识库缺口或错误答案。</span><el-button text @click="store.navigate('logs', 'tab=assistant-answers')">进入回答复核 →</el-button></header>
      <el-table
        v-if="unconfirmedTraces.length"
        :data="unconfirmedTraces.slice(0, 5)"
        class="clickable-table"
        @row-click="(row: TraceSummary) => store.openTrace(row.trace_id)"
      >
        <el-table-column
          prop="question"
          label="用户问题"
          min-width="240"
          show-overflow-tooltip
        />
        <el-table-column label="结果" width="120"><template #default>待补充条件</template></el-table-column>
        <el-table-column prop="user_id" label="用户" width="140" />
        <el-table-column label="时间" min-width="165"><template #default="scope">{{ store.formatDate(scope.row.created_at) }}</template></el-table-column>
      </el-table>
      <EmptyState
        v-else-if="overview && !metricsError"
        :icon="ChatDotRound"
        title="当前没有待补充信息的问题"
        description="仅展示近 7 天外部 Assistant 的技术回答。仍可进入回答复核查看其他问题。"
      />
      <p v-else class="empty-note">{{ metricsLoading ? '正在加载问题记录…' : '问题记录暂不可确认，请刷新后查看。' }}</p>
    </details>

    <details
      class="panel advanced-details"
      :open="diagnosticsOpen"
      @toggle="diagnosticsOpen = ($event.target as HTMLDetailsElement).open"
    >
      <summary>更多指标与诊断<span>阶段质量、回答分布、用户明细、请求重放和链路记录</span></summary>
      <div class="advanced-body">
        <section v-if="overview" ref="qualitySection" class="quality-section">
          <h4>阶段质量（RAG V2）</h4>
          <p class="muted">外部 Assistant · RAG · 近 7 天。读取每轮链路记录中的 V2 阶段结果；降级表示该阶段失败后使用了兜底，回答仍会返回，但质量可能下降。超过 {{ QUALITY_ALERT_RATE * 100 }}% 且不少于 {{ QUALITY_ALERT_MIN_COUNT }} 次时列入待办。</p>
          <div v-if="overview.v2_quality" class="detail-grid">
            <div><span>查询理解降级率</span><b>{{ percent(overview.v2_quality.query_interpreter_degraded_rate, overview.v2_quality.query_interpreter_total) }}</b><small>{{ overview.v2_quality.query_interpreter_degraded }} / {{ overview.v2_quality.query_interpreter_total }} · 有查询理解记录的轮次</small></div>
            <div><span>文档重排降级率</span><b>{{ percent(overview.v2_quality.document_rerank_degraded_rate, overview.v2_quality.document_rerank_attempted) }}</b><small>{{ overview.v2_quality.document_rerank_degraded }} / {{ overview.v2_quality.document_rerank_attempted }} · 实际调用重排的检索</small></div>
            <div :class="{ 'is-danger': overview.v2_quality.document_rerank_unconfigured > 0 }"><span>未执行重排（模型不可用）</span><b>{{ overview.v2_quality.document_rerank_unconfigured }}</b><small>候选 ≥2 但没有可用 Rerank 模型的检索次数</small></div>
          </div>
          <p v-else class="muted">当前后端版本未提供阶段质量统计，请更新后端后查看。</p>
        </section>
        <section v-if="overview">
          <h4>技术回答结果</h4>
          <p class="muted">外部 Assistant · RAG · 近 7 天；请求 {{ overview.total_traces }} 次，产品范围内已分类 {{ overview.in_scope_traces }} 次。</p>
          <div class="detail-grid">
            <div><span>待补充信息率</span><b>{{ percent(overview.needs_information_rate, overview.in_scope_traces) }}</b><small>{{ overview.response_type_counts.UNCONFIRMED ?? 0 }} / {{ overview.in_scope_traces }} · 范围内已分类回答</small></div>
            <div><span>范围外请求率</span><b>{{ percent(overview.out_of_scope_rate, overview.classified_traces) }}</b><small>{{ overview.response_type_counts.OUT_OF_SCOPE ?? 0 }} / {{ overview.classified_traces }} · 已分类回答</small></div>
            <div><span>技术请求失败率</span><b>{{ percent(overview.failure_rate, overview.total_traces) }}</b><small>{{ overview.failed }} / {{ overview.total_traces }} · 技术请求</small></div>
            <div><span>技术回答 P50 耗时</span><b>{{ latency(overview.latency.p50_ms, overview.latency.samples) }}</b><small>中位数 · 样本 {{ overview.latency.samples }}</small></div>
          </div>
          <h4>回答依据分布</h4>
          <p class="muted">仅统计 {{ overview.answer_basis_traces }} 条带依据分类的回答；依据类型不等于答案正确率。</p>
          <div class="detail-grid">
            <div><span>文档知识</span><b>{{ percent(overview.product_knowledge_rate, overview.answer_basis_traces) }}</b><small>{{ overview.answer_mode_counts.PRODUCT_KNOWLEDGE ?? 0 }} / {{ overview.answer_basis_traces }}</small></div>
            <div><span>文档 + 通用解释</span><b>{{ percent(overview.product_explained_rate, overview.answer_basis_traces) }}</b><small>{{ overview.answer_mode_counts.PRODUCT_EXPLAINED ?? 0 }} / {{ overview.answer_basis_traces }}</small></div>
            <div><span>通用工程扩展</span><b>{{ percent(overview.product_general_rate, overview.answer_basis_traces) }}</b><small>{{ overview.answer_mode_counts.PRODUCT_GENERAL ?? 0 }} / {{ overview.answer_basis_traces }}</small></div>
          </div>
        </section>
        <section v-if="assistantMetrics">
          <h4>调用与留存诊断</h4>
          <p class="muted">近 {{ assistantMetrics.days }} 天的调用统计；会话留存为当前状态，不是七天新增量。</p>
          <p>幂等重放 {{ assistantMetrics.idempotency.assistant_replays }} 次 / 受保护请求 {{ assistantMetrics.idempotency.assistant_protected }} 次 · 留存会话 {{ assistantMetrics.retention.active }} · 待清理 {{ assistantMetrics.retention.expired }} · 留存 {{ assistantMetrics.retention.retention_days }} 天</p>
          <p v-if="assistantMetrics.turn_error_codes.length">执行错误：{{ assistantMetrics.turn_error_codes.map((item) => item.code + ' × ' + item.count).join('；') }}</p>
          <el-table v-if="assistantMetrics.top_users.length" :data="assistantMetrics.top_users">
            <el-table-column
              prop="user_id"
              label="外部用户"
              min-width="220"
              show-overflow-tooltip
            /><el-table-column prop="turns" label="调用轮次" width="110" /><el-table-column prop="failed" label="失败" width="90" />
          </el-table>
        </section>
        <section>
          <h4>最近链路记录</h4><p class="muted">所选知识库的最近记录，可能包含内部试问与调试台；不属于上方外部运营指标的同一统计口径。</p>
          <el-table
            v-if="recentTraces.length"
            :data="recentTraces"
            class="clickable-table"
            @row-click="(row: TraceSummary) => store.openTrace(row.trace_id)"
          >
            <el-table-column
              prop="question"
              label="问题"
              min-width="240"
              show-overflow-tooltip
            /><el-table-column prop="source" label="来源" width="160" /><el-table-column label="状态" width="130"><template #default="scope"><StatusBadge :status="scope.row.status" /></template></el-table-column>
          </el-table>
          <p v-else class="muted">暂无记录</p>
        </section>
      </div>
    </details>

    <el-dialog
      :model-value="!!drilldown"
      :title="'时段技术链路 · ' + (drilldown?.label || '')"
      width="min(920px, 94vw)"
      @close="drilldownGeneration++; drilldown = null"
    >
      <p class="muted">外部 Assistant · RAG · {{ drilldown ? store.formatDate(drilldown.from) : '' }} 至 {{ drilldown ? store.formatDate(drilldown.to) : '' }}，最多展示 100 条；点击行查看链路详情。</p>
      <el-alert
        v-if="drilldownError"
        :title="drilldownError"
        type="warning"
        :closable="false"
      />
      <el-table
        v-loading="drilldownLoading"
        :data="drilldownTraces"
        class="clickable-table"
        @row-click="(row: TraceSummary) => store.openTrace(row.trace_id)"
      >
        <el-table-column
          prop="question"
          label="问题"
          min-width="260"
          show-overflow-tooltip
        /><el-table-column prop="user_id" label="用户" width="140" /><el-table-column label="状态" width="130"><template #default="scope"><StatusBadge :status="scope.row.status" /></template></el-table-column><el-table-column label="时间" width="170"><template #default="scope">{{ store.formatDate(scope.row.created_at) }}</template></el-table-column>
      </el-table>
    </el-dialog>
  </div>
</template>


<style scoped>
.dashboard { display: grid; gap: 20px; min-width: 0; }
.dashboard > * { min-width: 0; }

/* ---------- Scope toolbar ---------- */
.dashboard-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.scope-actions { display: flex; align-items: center; gap: 12px; min-width: 0; flex-wrap: wrap; }
.scope-actions .el-select { width: 220px; }
.refresh-meta { color: var(--text-tertiary); font-size: 12px; }
.scope-note { display: flex; align-items: flex-start; gap: 6px; margin: -10px 0 0; color: var(--text-secondary); font-size: 12px; line-height: 1.6; }
.scope-note .el-icon { flex-shrink: 0; margin-top: 3px; color: var(--text-tertiary); }

/* ---------- Availability & attention ---------- */
.status-layout { display: grid; grid-template-columns: 1.1fr 1fr; gap: 20px; }
.availability-panel, .attention-panel { position: relative; padding: 22px; overflow: hidden; }
.availability-panel::before { position: absolute; inset: 0 auto 0 0; width: 4px; background: var(--tone-color); content: ""; }
.availability-panel { --tone-color: var(--text-tertiary); --tone-bg: var(--bg-subtle); }
.availability-panel.tone-success { --tone-color: var(--success); --tone-bg: var(--success-bg); }
.availability-panel.tone-danger { --tone-color: var(--error); --tone-bg: var(--error-bg); }
.availability-panel.tone-info { --tone-color: var(--info); --tone-bg: var(--info-bg); }
.availability-head { display: flex; align-items: center; gap: 14px; margin-bottom: 12px; }
.availability-icon { display: grid; width: 44px; height: 44px; flex-shrink: 0; place-items: center; border-radius: 50%; color: var(--tone-color); background: var(--tone-bg); font-size: 24px; }
.eyebrow { display: block; margin-bottom: 4px; color: var(--text-tertiary); font-size: 12px; }
.availability-panel h3 { margin: 0; color: var(--text-primary); font-size: 20px; line-height: 1.35; }
.availability-panel.tone-danger h3 { color: var(--error); }
.availability-panel p, .attention-panel p { color: var(--text-secondary); font-size: 13px; line-height: 1.7; }
.status-meta { display: flex; flex-wrap: wrap; gap: 8px; margin: 16px 0; }
.status-meta span { padding: 2px 10px; border-radius: 999px; color: var(--text-secondary); background: var(--bg-subtle); font-size: 12px; }
.status-meta b { color: var(--text-primary); font-variant-numeric: tabular-nums; }
.attention-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 6px; }
.attention-head .eyebrow { margin: 0; }
.attention-count { padding: 0 8px; border-radius: 999px; color: var(--error); background: var(--error-bg); font-size: 12px; font-weight: 500; line-height: 20px; }
.attention-row { --tone-color: var(--info); --tone-bg: var(--info-bg); display: flex; align-items: center; gap: 12px; padding: 11px 8px; margin: 0 -8px; width: calc(100% + 16px); border: 0; border-bottom: 1px solid var(--border-subtle); border-radius: var(--radius-sm); background: transparent; text-align: left; color: var(--text-primary); cursor: pointer; font-size: 13px; transition: background-color var(--transition-fast); }
.attention-row:last-of-type { border-bottom: 0; }
.attention-row:hover, .attention-row:focus-visible { background: var(--bg-hover); outline: none; }
.attention-row.tone-danger { --tone-color: var(--error); --tone-bg: var(--error-bg); }
.attention-row.tone-warning { --tone-color: var(--warning); --tone-bg: var(--warning-bg); }
.attention-icon { display: grid; width: 28px; height: 28px; flex-shrink: 0; place-items: center; border-radius: var(--radius-sm); color: var(--tone-color); background: var(--tone-bg); font-size: 15px; }
.attention-label { flex: 1; min-width: 0; }
.attention-row b { color: var(--tone-color); font-size: 12px; font-variant-numeric: tabular-nums; flex-shrink: 0; }
.attention-arrow { color: var(--text-tertiary); transition: transform var(--transition-fast); }
.attention-row:hover .attention-arrow { transform: translateX(2px); }
.attention-empty { display: flex; align-items: flex-start; gap: 10px; padding: 14px; margin-top: 8px; border-radius: var(--radius-md); background: var(--bg-subtle); }
.attention-empty .el-icon { flex-shrink: 0; margin-top: 3px; color: var(--text-tertiary); font-size: 18px; }
.attention-empty.known { background: var(--success-bg); }
.attention-empty.known .el-icon { color: var(--success); }

/* ---------- Usage ---------- */
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin: 4px 0 14px; }
.section-heading h3 { display: flex; align-items: center; gap: 6px; margin: 0; font-size: 16px; }
.heading-hint { color: var(--text-tertiary); font-size: 14px; cursor: help; }
.empty-note { padding: 16px 18px; color: var(--text-tertiary); font-size: 13px; }

/* ---------- Trend ---------- */
.trend-panel .panel-header { align-items: flex-start; }
.trend-controls { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 8px; }
.trend-date-picker { width: 260px; }
.trend-controls .trend-line-select { width: 168px; }
.trend-body { display: grid; gap: 16px; min-width: 0; }
.trend-summary { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 1px; overflow: hidden; border: 1px solid var(--border-subtle); border-radius: var(--radius-md); background: var(--border-subtle); }
.trend-summary > div { display: grid; gap: 2px; padding: 10px 14px; background: var(--bg-surface); }
.trend-summary span, .trend-summary small { color: var(--text-tertiary); font-size: 12px; }
.trend-summary small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.trend-summary b { color: var(--text-primary); font-size: 18px; font-weight: 600; font-variant-numeric: tabular-nums; }
.trend-summary .danger b { color: var(--error); }

/* ---------- Collapsible details ---------- */
.advanced-details > summary { padding: 18px; cursor: pointer; color: var(--text-primary); font-size: 14px; }
.advanced-details > summary span { margin-left: 12px; color: var(--text-tertiary); font-size: 12px; }
.advanced-body { display: grid; gap: 24px; padding: 0 18px 18px; }
.advanced-body h4 { margin: 16px 0 8px; }
.advanced-body p { font-size: 12px; line-height: 1.7; }
.detail-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin: 14px 0; }
.detail-grid > div { display: grid; gap: 6px; padding: 14px; border-radius: var(--radius-sm); background: var(--bg-subtle); }
.detail-grid span, .detail-grid small { color: var(--text-secondary); font-size: 12px; }
.detail-grid b { font-size: 20px; font-variant-numeric: tabular-nums; }
.detail-grid > div.is-danger { background: var(--error-bg); }
.detail-grid > div.is-danger b { color: var(--error); }
.quality-section { scroll-margin-top: 16px; }
:deep(.clickable-table .el-table__row) { cursor: pointer; }

@media (max-width: 1100px) {
  .trend-summary { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
@media (max-width: 760px) {
  .status-layout { grid-template-columns: 1fr; }
  .section-heading { align-items: flex-start; flex-direction: column; }
  .scope-actions { width: 100%; }
  .scope-actions .el-select { flex: 1; min-width: 150px; }
  .trend-controls { justify-content: flex-start; }
  .trend-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .advanced-details > summary span { display: block; margin: 6px 0 0; }
}
</style>

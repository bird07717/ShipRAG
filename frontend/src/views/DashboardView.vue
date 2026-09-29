<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { Box, ChatDotRound, Clock, Files, Warning } from "@element-plus/icons-vue";

import { adminApi } from "@/api/admin";
import { useAdminStore } from "@/composables/adminStore";
import MetricCard from "@/components/ui/MetricCard.vue";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import EmptyState from "@/components/ui/EmptyState.vue";
import ListSkeleton from "@/components/ui/ListSkeleton.vue";
import TrendChart, { type TrendPoint } from "@/components/ui/TrendChart.vue";
import type {
  AssistantMetrics,
  MetricsOverview,
  MetricsTimeseriesPoint,
  TraceSummary,
} from "@/types/admin";

const store = useAdminStore();

const overview = ref<MetricsOverview | null>(null);
const assistantMetrics = ref<AssistantMetrics | null>(null);
const timeseries = ref<MetricsTimeseriesPoint[]>([]);
const unconfirmedTraces = ref<TraceSummary[]>([]);
const metricsError = ref("");
const metricsLoading = ref(false);
const refreshing = ref(false);

const drilldown = ref<{ label: string; from: string; to: string } | null>(null);
const drilldownTraces = ref<TraceSummary[]>([]);
const drilldownLoading = ref(false);

const lastRefreshedAt = ref<Date | null>(null);
let autoRefreshTimer: ReturnType<typeof setInterval> | null = null;

const QUALITY_WINDOW_DAYS = 7;
const QUALITY_ENGINE = "RAG" as const;
const QUALITY_SOURCE = "ASSISTANT_API" as const;

const lastRefreshedLabel = computed(() =>
  lastRefreshedAt.value
    ? lastRefreshedAt.value.toLocaleTimeString("zh-CN", { hour12: false })
    : "—",
);

const percent = (value: number) => `${(value * 100).toFixed(1)}%`;

// Threshold-based tone for operations cards: rates above 5% turn danger,
// external P95 above 10s turns warning. Thresholds live here so the whole
// panel follows one definition.
const ERROR_RATE_DANGER = 0.05;
const LATENCY_WARNING_MS = 10_000;

const assistantErrorTone = computed(() =>
  assistantSource.value.turns > 0 && assistantSource.value.error_rate > ERROR_RATE_DANGER
    ? ("danger" as const)
    : ("default" as const),
);

const assistantLatencyTone = computed(() =>
  assistantSource.value.latency.p95_ms > LATENCY_WARNING_MS
    ? ("warning" as const)
    : ("default" as const),
);

const qualityErrorTone = computed(() =>
  overview.value && overview.value.failure_rate > ERROR_RATE_DANGER
    ? ("danger" as const)
    : ("default" as const),
);

const directAnswerCount = computed(
  () =>
    (overview.value?.response_type_counts.ANSWERED ?? 0) +
    (overview.value?.response_type_counts.DOC_DELIVERED ?? 0),
);

// Bucket span inferred from the first two points (day granularity is what
// we request; hour would come back as 1h gaps). Falls back to 1 day.
const bucketSpanMs = computed(() => {
  const [first, second] = timeseries.value;
  if (first && second) {
    const span = new Date(second.bucket).getTime() - new Date(first.bucket).getTime();
    if (span > 0) return span;
  }
  return 86_400_000;
});

const chartPoints = computed<TrendPoint[]>(() => {
  const hourly = bucketSpanMs.value <= 3_600_000;
  return timeseries.value.map((point) => ({
    bucket: point.bucket,
    label: hourly
      ? point.bucket.slice(5, 13).replace("T", " ")
      : point.bucket.slice(5, 10),
    queries: point.queries,
    needsInformationRate: point.needs_information_rate,
  }));
});

// Online Assistant surface: fall back to zeros before the first external
// call lands so the panel always renders a complete shape.
const emptyLatency = { p50_ms: 0, p95_ms: 0, p99_ms: 0, avg_ms: 0, samples: 0 };
const assistantSource = computed(() => {
  const found = assistantMetrics.value?.sources.find((item) => item.source === "ASSISTANT_API");
  return (
    found ?? {
      source: "ASSISTANT_API",
      turns: 0,
      failed: 0,
      error_rate: 0,
      conversations: 0,
      users: 0,
      latency: emptyLatency,
    }
  );
});

const assistantInternalTurns = computed(
  () => assistantMetrics.value?.sources.find((item) => item.source === "INTERNAL_CHAT")?.turns ?? 0,
);

const assistantTopError = computed(() => {
  const [first] = assistantMetrics.value?.turn_error_codes ?? [];
  return first ? `${first.code} × ${first.count}` : "—";
});

const lastLoadedKb = ref<string | null | undefined>(undefined);

async function loadMetrics() {
  metricsLoading.value = true;
  const knowledgeId = store.selectedKnowledgeId;
  metricsError.value = "";
  // Only blank the panels on the first load or a knowledge-base switch;
  // the 60s auto-refresh swaps data in place so the dashboard never blinks.
  if (lastLoadedKb.value !== knowledgeId) {
    overview.value = null;
    assistantMetrics.value = null;
    timeseries.value = [];
    unconfirmedTraces.value = [];
  }
  lastLoadedKb.value = knowledgeId;
  const to = new Date();
  const from = new Date(to.getTime() - QUALITY_WINDOW_DAYS * 86_400_000);
  const qualityParams = {
    knowledgeId: knowledgeId || undefined,
    from: from.toISOString(),
    to: to.toISOString(),
    engine: QUALITY_ENGINE,
    source: QUALITY_SOURCE,
  };
  const results = await Promise.allSettled([
    adminApi.getMetricsOverview(qualityParams),
    adminApi.getMetricsTimeseries(qualityParams),
    adminApi.listUnconfirmedTraces(knowledgeId || undefined, qualityParams),
    adminApi.getAssistantMetrics({ knowledgeId: knowledgeId || undefined }),
  ]);
  if (store.selectedKnowledgeId !== knowledgeId) return;
  // Shape guards prevent an unexpected gateway payload from crashing the dashboard.
  if (
    results[0].status === "fulfilled" &&
    results[0].value &&
    typeof results[0].value.total_traces === "number"
  ) {
    overview.value = results[0].value;
  }
  if (results[1].status === "fulfilled" && Array.isArray(results[1].value)) {
    timeseries.value = results[1].value;
  }
  if (results[2].status === "fulfilled" && Array.isArray(results[2].value)) {
    unconfirmedTraces.value = results[2].value;
  }
  if (
    results[3].status === "fulfilled" &&
    results[3].value &&
    Array.isArray(results[3].value?.sources) &&
    typeof results[3].value?.idempotency?.assistant_protected === "number"
  ) {
    assistantMetrics.value = results[3].value;
  }
  if (overview.value === null || results.some((result) => result.status === "rejected")) {
    metricsError.value = "部分质量指标暂时无法加载，请稍后重试；其他管理功能不受影响。";
  }
  metricsLoading.value = false;
  lastRefreshedAt.value = new Date();
}

function autoRefresh(): void {
  if (store.hasServiceCredential && !metricsLoading.value) {
    void loadMetrics();
  }
}

async function refreshDashboard(): Promise<void> {
  refreshing.value = true;
  try {
    await store.refreshOperationalState();
    await loadMetrics();
    store.notice = "运行状态已刷新";
  } finally {
    refreshing.value = false;
  }
}

async function openBucketDrilldown(point: TrendPoint) {
  const bucketStart = new Date(point.bucket);
  const bucketEnd = new Date(bucketStart.getTime() + bucketSpanMs.value);
  drilldown.value = { label: point.label, from: point.bucket, to: bucketEnd.toISOString() };
  drilldownTraces.value = [];
  drilldownLoading.value = true;
  try {
    drilldownTraces.value = await adminApi.listTraces(store.selectedKnowledgeId || undefined, {
      from: point.bucket,
      to: bucketEnd.toISOString(),
      engine: QUALITY_ENGINE,
      source: QUALITY_SOURCE,
      limit: 100,
    });
  } finally {
    drilldownLoading.value = false;
  }
}

onMounted(() => {
  void loadMetrics();
  autoRefreshTimer = setInterval(autoRefresh, 60_000);
});
onUnmounted(() => {
  if (autoRefreshTimer) clearInterval(autoRefreshTimer);
});
watch(() => store.selectedKnowledgeId, () => void loadMetrics());
</script>

<template>
  <div class="dashboard">
    <section class="panel dashboard-header">
      <header class="panel-header">
        <div>
          <h3>运营总览</h3>
          <span class="sub">所有面板按所选知识库过滤 · 上次刷新 {{ lastRefreshedLabel }}（每 60 秒自动刷新）</span>
        </div>
        <div class="scope-actions">
          <el-select
            v-model="store.selectedKnowledgeId"
            placeholder="选择知识库"
            @change="store.selectKnowledge()"
          >
            <el-option
              v-for="kb in store.knowledgeBases"
              :key="kb.id"
              :label="kb.name"
              :value="kb.id"
            />
          </el-select>
          <el-button :loading="refreshing" @click="refreshDashboard">刷新状态</el-button>
        </div>
      </header>
    </section>

    <section v-if="assistantMetrics" class="panel">
      <header class="panel-header">
        <div>
          <h3>Assistant 调用（近 {{ assistantMetrics.days }} 天）</h3>
          <el-button text @click="store.navigate('logs', 'tab=assistant-answers')">查看回答与优化</el-button>
          <span class="sub">外部软件后台的调用运营视图 — 按来源、用户、错误率、幂等重放与会话留存聚合</span>
        </div>
      </header>
      <div class="metric-grid">
        <MetricCard
          label="外部调用轮次"
          :value="assistantSource.turns.toLocaleString()"
          :context="`内部测试 ${assistantInternalTurns.toLocaleString()} 轮 · 外部会话 ${assistantSource.conversations} 个`"
          :icon="ChatDotRound"
        />
        <MetricCard
          label="外部调用错误率"
          :value="percent(assistantSource.error_rate)"
          :context="`失败 ${assistantSource.failed} / ${assistantSource.turns} · TOP ${assistantTopError}`"
          :icon="Warning"
          :tone="assistantErrorTone"
        />
        <MetricCard
          label="外部 P95 延迟"
          :value="`${assistantSource.latency.p95_ms.toLocaleString()} ms`"
          :context="`P50 ${assistantSource.latency.p50_ms.toLocaleString()} ms · 样本 ${assistantSource.latency.samples.toLocaleString()} / ${assistantSource.turns.toLocaleString()} 次`"
          :icon="Clock"
          :tone="assistantLatencyTone"
        />
        <MetricCard
          label="幂等保护 / 重放"
          :value="(assistantMetrics?.idempotency.assistant_replays ?? 0).toLocaleString()"
          :context="`受保护请求 ${(assistantMetrics?.idempotency.assistant_protected ?? 0).toLocaleString()} 次`"
          :icon="Box"
        />
        <MetricCard
          label="外部用户"
          :value="assistantSource.users.toLocaleString()"
          :context="assistantMetrics?.top_users.length ? `最活跃 ${assistantMetrics.top_users[0]?.turns ?? 0} 轮` : '暂无外部调用'"
          :icon="Files"
        />
        <MetricCard
          label="会话留存"
          :value="(assistantMetrics?.retention.active ?? 0).toLocaleString()"
          :context="`待清理 ${assistantMetrics?.retention.expired ?? 0} · 留存 ${assistantMetrics?.retention.retention_days ?? 0} 天`"
          :icon="Clock"
        />
      </div>
      <el-table
        v-if="assistantMetrics?.top_users.length"
        class="assistant-user-table"
        :data="assistantMetrics?.top_users ?? []"
      >
        <el-table-column
          prop="user_id"
          label="外部用户"
          min-width="220"
          show-overflow-tooltip
        />
        <el-table-column prop="turns" label="调用轮次" width="110" />
        <el-table-column prop="failed" label="失败" width="90" />
        <el-table-column label="最近调用" min-width="170">
          <template #default="scope">{{ store.formatDate(scope.row.last_seen) }}</template>
        </el-table-column>
      </el-table>
    </section>

    <ListSkeleton v-if="metricsLoading && !overview" variant="metrics" />
    <el-alert
      v-else-if="!overview && metricsError"
      :title="metricsError"
      type="warning"
      :closable="false"
      show-icon
    />
    <div v-else-if="overview" class="quality-block">
      <p class="quality-scope">质量统计范围：外部 Assistant API · RAG · 近 7 天</p>
      <div class="metric-grid">
        <MetricCard
          label="直接回答率"
          :value="percent(overview.direct_answer_rate)"
          :context="`直接回答 ${directAnswerCount} / 产品范围内 ${overview.in_scope_traces}`"
          :icon="ChatDotRound"
        />
        <MetricCard
          label="待补充信息率"
          :value="percent(overview.needs_information_rate)"
          :context="`待补充 ${overview.response_type_counts.UNCONFIRMED ?? 0} / 产品范围内 ${overview.in_scope_traces}`"
          :icon="Warning"
        />
        <MetricCard
          label="范围外请求率"
          :value="percent(overview.out_of_scope_rate)"
          :context="`范围外 ${overview.response_type_counts.OUT_OF_SCOPE ?? 0} / 已分类 ${overview.classified_traces}`"
          :icon="Files"
        />
        <MetricCard
          label="失败率"
          :value="percent(overview.failure_rate)"
          :context="`失败 ${overview.failed} / 请求 ${overview.total_traces}`"
          :icon="Clock"
          :tone="qualityErrorTone"
        />
        <MetricCard
          label="P95 延迟"
          :value="`${overview.latency.p95_ms.toLocaleString()} ms`"
          :context="`P50 ${overview.latency.p50_ms.toLocaleString()} ms · 样本 ${overview.latency.samples.toLocaleString()} / ${overview.total_traces.toLocaleString()} 次`"
          :icon="Box"
        />
      </div>
    </div>

    <section v-if="overview" class="panel">
      <header class="panel-header">
        <div>
          <h3>回答依据分布</h3>
          <span class="sub">外部 Assistant API · RAG · 近 7 天 · 仅统计带 PRODUCT_* 依据分类的回答</span>
        </div>
      </header>
      <div class="metric-grid basis-grid">
        <MetricCard
          label="文档知识"
          :value="percent(overview.product_knowledge_rate)"
          :context="`${overview.answer_mode_counts.PRODUCT_KNOWLEDGE ?? 0} / ${overview.answer_basis_traces} 条已分类回答`"
          :icon="Files"
        />
        <MetricCard
          label="文档 + 通用解释"
          :value="percent(overview.product_explained_rate)"
          :context="`${overview.answer_mode_counts.PRODUCT_EXPLAINED ?? 0} / ${overview.answer_basis_traces} 条已分类回答`"
          :icon="ChatDotRound"
        />
        <MetricCard
          label="通用工程扩展"
          :value="percent(overview.product_general_rate)"
          :context="`${overview.answer_mode_counts.PRODUCT_GENERAL ?? 0} / ${overview.answer_basis_traces} 条已分类回答`"
          :icon="Box"
        />
      </div>
    </section>

    <section v-if="chartPoints.length" class="panel">
      <header class="panel-header">
        <div>
          <h3>查询量与待补充信息率</h3>
          <span class="sub">外部 Assistant API · RAG · 最近 7 天 · 点击柱形下钻同范围链路</span>
        </div>
      </header>
      <TrendChart :points="chartPoints" @select="openBucketDrilldown" />
    </section>

    <section class="panel workflow-panel">
      <el-collapse>
        <el-collapse-item name="admin-tasks">
          <template #title>
            <div class="collapse-title">
              <h3>管理待办</h3>
              <span class="sub">
                处理中 {{ store.buildingCount }} · RAG
                {{ store.activeIndex ? `在线 v${store.activeIndex.version}` : "未就绪" }}
              </span>
            </div>
          </template>
          <div class="workflow-grid">
            <button type="button" @click="store.navigate('indexes')">
              <span>处理中</span><strong>{{ store.buildingCount }}</strong>
              <small>{{ store.buildingCount ? "文档正在处理" : "当前没有处理任务" }}</small>
            </button>
            <button type="button" @click="store.navigate('indexes')">
              <span>RAG 在线</span>
              <strong class="state-text">{{ store.activeIndex ? `v${store.activeIndex.version}` : "未就绪" }}</strong>
              <small>技术构建完成后由管理员明确切换</small>
            </button>
          </div>
        </el-collapse-item>
      </el-collapse>
    </section>

    <section class="panel">
      <header class="panel-header">
        <div>
          <h3>待补充信息的问题</h3>
          <span class="sub">仅表示回答需要更多条件，不直接等同于知识库覆盖缺口；点击查看 Trace 归因</span>
        </div>
      </header>
      <el-table
        v-if="unconfirmedTraces.length"
        class="clickable-table"
        :data="unconfirmedTraces"
        @row-click="(row: any) => store.openTrace(row.trace_id)"
      >
        <el-table-column
          prop="question"
          label="问题"
          min-width="280"
          show-overflow-tooltip
        />
        <el-table-column prop="response_type" label="响应" width="150">
          <template #default="scope">
            <StatusBadge :status="scope.row.response_type ?? 'UNKNOWN'" />
          </template>
        </el-table-column>
        <el-table-column prop="user_id" label="用户" width="140">
          <template #default="scope">{{ scope.row.user_id ?? "—" }}</template>
        </el-table-column>
        <el-table-column label="时间" min-width="170">
          <template #default="scope">{{ store.formatDate(scope.row.created_at) }}</template>
        </el-table-column>
      </el-table>
      <EmptyState
        v-else
        :icon="Warning"
        title="近 7 天没有待补充信息的问题"
        description="外部 Assistant 的 RAG 回答被判为需要补充条件（UNCONFIRMED）时，会出现在这里。"
      />
    </section>

    <section class="panel">
      <header class="panel-header">
        <div>
          <h3>近期活动</h3>
          <span class="sub">最近的调试台（Playground）与对话（Chat）查询，点击行查看链路追踪（Trace）。</span>
        </div>
      </header>
      <el-table
        v-if="store.traces.length"
        class="clickable-table"
        :data="store.traces.slice(0, 5)"
        @row-click="(row: any) => store.openTrace(row.trace_id)"
      >
        <el-table-column
          prop="question"
          label="查询"
          min-width="280"
          show-overflow-tooltip
        />
        <el-table-column prop="mode" label="模式" width="120" />
        <el-table-column label="状态" width="130">
          <template #default="scope"><StatusBadge :status="scope.row.status" /></template>
        </el-table-column>
        <el-table-column label="延迟" width="110">
          <template #default="scope">
            <span class="mono">{{ scope.row.latency.total_ms ?? "—" }} ms</span>
          </template>
        </el-table-column>
        <el-table-column label="时间" min-width="170">
          <template #default="scope">{{ store.formatDate(scope.row.created_at) }}</template>
        </el-table-column>
      </el-table>
      <EmptyState
        v-else
        :icon="Clock"
        title="暂无最近查询"
        description="在 RAG 调试台（RAG Playground）或问答测试（DemoChat）中发起查询后，这里会展示最近的活动。"
      />
    </section>

    <el-dialog
      :model-value="drilldown !== null"
      :title="drilldown ? `链路追踪（Trace）— ${drilldown.label}` : ''"
      width="720px"
      @update:model-value="drilldown = null"
    >
      <el-table
        v-loading="drilldownLoading"
        class="clickable-table"
        :data="drilldownTraces"
        max-height="480"
        @row-click="(row: any) => store.openTrace(row.trace_id)"
      >
        <el-table-column
          prop="question"
          label="问题"
          min-width="240"
          show-overflow-tooltip
        />
        <el-table-column label="响应" width="140">
          <template #default="scope">
            <StatusBadge :status="scope.row.response_type ?? scope.row.status" />
          </template>
        </el-table-column>
        <el-table-column label="延迟" width="100">
          <template #default="scope">
            <span class="mono">{{ scope.row.latency?.total_ms ?? "—" }} ms</span>
          </template>
        </el-table-column>
        <el-table-column prop="user_id" label="用户" width="120">
          <template #default="scope">{{ scope.row.user_id ?? "—" }}</template>
        </el-table-column>
      </el-table>
      <EmptyState
        v-if="!drilldownLoading && !drilldownTraces.length"
        title="该时段没有链路追踪"
        description="试试点击相邻的柱形。"
      />
    </el-dialog>
  </div>
</template>

<style scoped>
.dashboard {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: var(--space-content-gap);
  min-width: 0;
}

.dashboard > * {
  min-width: 0;
}

.dashboard-header .panel-header {
  border-bottom: none;
}

.collapse-title {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 14px 18px;
}

.collapse-title h3 {
  color: var(--text-primary);
  font-size: 15px;
  font-weight: 600;
}

.collapse-title .sub {
  color: var(--text-tertiary);
  font-size: 12px;
}

.workflow-panel :deep(.el-collapse) {
  border-top: none;
  border-bottom: none;
}

.workflow-panel :deep(.el-collapse-item__header) {
  border-bottom: none;
}

.workflow-panel :deep(.el-collapse-item__wrap) {
  border-bottom: none;
}

.workflow-panel :deep(.el-collapse-item__content) {
  padding-bottom: 4px;
}

.scope-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.scope-actions .el-select {
  width: 220px;
}

.workflow-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  padding: 16px 18px;
}

.workflow-grid button {
  display: grid;
  gap: 3px;
  padding: 14px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--bg-subtle);
  text-align: left;
  cursor: pointer;
}

.workflow-grid span,
.workflow-grid small {
  color: var(--text-tertiary);
  font-size: 12px;
}

.workflow-grid strong {
  color: var(--text-primary);
  font-size: 22px;
}

.workflow-grid .state-text {
  color: var(--brand-primary);
  font-size: 14px;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: var(--space-content-gap);
}

.basis-grid {
  padding: 0 18px 18px;
}

.quality-block {
  display: grid;
  gap: 8px;
}

.quality-scope {
  color: var(--text-tertiary);
  font-size: 12px;
}

.assistant-user-table {
  margin-top: var(--space-content-gap);
}

:deep(.clickable-table .el-table__row) {
  cursor: pointer;
}

@media (max-width: 760px) {
  .workflow-grid {
    grid-template-columns: 1fr;
  }
}
</style>

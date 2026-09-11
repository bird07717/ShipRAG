<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { Box, ChatDotRound, Clock, Files, Notebook, Warning } from "@element-plus/icons-vue";

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
  SupportMetrics,
  TraceSummary,
} from "@/types/admin";

const store = useAdminStore();

const overview = ref<MetricsOverview | null>(null);
const supportMetrics = ref<SupportMetrics | null>(null);
const assistantMetrics = ref<AssistantMetrics | null>(null);
const timeseries = ref<MetricsTimeseriesPoint[]>([]);
const noAnswerTraces = ref<TraceSummary[]>([]);
const metricsError = ref("");
const metricsLoading = ref(false);
const refreshing = ref(false);

const drilldown = ref<{ label: string; from: string; to: string } | null>(null);
const drilldownTraces = ref<TraceSummary[]>([]);
const drilldownLoading = ref(false);

const lastRefreshedAt = ref<Date | null>(null);
let autoRefreshTimer: ReturnType<typeof setInterval> | null = null;

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
    noAnswerRate: point.no_answer_rate,
  }));
});

// Online Assistant surface: fall back to zeros before the first external
// call lands so the panel always renders a complete shape.
const emptyLatency = { p50_ms: 0, p95_ms: 0, p99_ms: 0, avg_ms: 0 };
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
    supportMetrics.value = null;
    assistantMetrics.value = null;
    timeseries.value = [];
    noAnswerTraces.value = [];
  }
  lastLoadedKb.value = knowledgeId;
  const params = { knowledgeId: knowledgeId || undefined };
  const results = await Promise.allSettled([
    adminApi.getMetricsOverview(params),
    adminApi.getMetricsTimeseries(params),
    adminApi.listNoAnswerTraces(knowledgeId || undefined),
    adminApi.getSupportMetrics(params),
    adminApi.getAssistantMetrics(params),
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
    noAnswerTraces.value = results[2].value;
  }
  if (
    results[3].status === "fulfilled" &&
    results[3].value &&
    typeof results[3].value.support_turns === "number"
  ) {
    supportMetrics.value = results[3].value;
  }
  if (
    results[4].status === "fulfilled" &&
    Array.isArray(results[4].value?.sources) &&
    typeof results[4].value?.idempotency?.assistant_protected === "number"
  ) {
    assistantMetrics.value = results[4].value;
  }
  if (overview.value === null || results.some((result) => result.status === "rejected")) {
    metricsError.value = "部分质量指标暂时无法加载，请稍后重试；其他管理功能不受影响。";
  }
  metricsLoading.value = false;
  lastRefreshedAt.value = new Date();
}

function autoRefresh(): void {
  if (!metricsLoading.value) {
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
          :context="`P50 ${assistantSource.latency.p50_ms.toLocaleString()} ms · P99 ${assistantSource.latency.p99_ms.toLocaleString()} ms`"
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
    <div v-else-if="overview" class="metric-grid">
      <MetricCard
        label="无答案率（近 7 天）"
        :value="percent(overview.no_answer_rate)"
        :context="`UNCONFIRMED + OUT_OF_SCOPE / ${overview.classified_traces}`"
        :icon="Warning"
      />
      <MetricCard
        label="文档交付"
        :value="percent(supportMetrics?.document_delivery_rate ?? overview.doc_delivered_rate)"
        :context="supportMetrics ? `${supportMetrics.document_delivered} 次完整文档交付` : `零 LLM 快路径`"
        :icon="Notebook"
      />
      <MetricCard
        label="失败率"
        :value="percent(overview.failure_rate)"
        :context="`失败 ${overview.failed} / ${overview.total_traces} · 升级 ${supportMetrics?.escalation_required ?? 0} 次`"
        :icon="Clock"
        :tone="qualityErrorTone"
      />
      <MetricCard
        label="P95 延迟"
        :value="`${overview.latency.p95_ms.toLocaleString()} ms`"
        :context="`P50 ${overview.latency.p50_ms.toLocaleString()} ms · 查询 ${overview.total_traces.toLocaleString()} 次`"
        :icon="Box"
      />
      <MetricCard
        label="澄清率"
        :value="percent(supportMetrics?.clarification_rate ?? 0)"
        :context="`后续成功 ${percent(supportMetrics?.clarification_resolution_rate ?? 0)}`"
        :icon="ChatDotRound"
      />
    </div>

    <section v-if="chartPoints.length" class="panel">
      <header class="panel-header">
        <div>
          <h3>查询量与无答案率</h3>
          <span class="sub">最近 7 天 · 点击柱形下钻该时段的链路追踪</span>
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
                {{ store.activeIndex ? `在线 v${store.activeIndex.version}` : "未就绪" }} ·
                {{ store.knowledgeProfileSummary?.online_count ?? 0 }} 项 Profile 已上线
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
            <button type="button" @click="store.navigate('support-review')">
              <span>Profile 属性</span>
              <strong class="state-text">
                {{ store.knowledgeProfileSummary?.online_count ?? 0 }} 项已上线
              </strong>
              <small>
                {{ store.knowledgeProfileSummary?.pending_count ? `${store.knowledgeProfileSummary.pending_count} 项待上线` : "RAG 始终可用" }}
              </small>
            </button>
          </div>
        </el-collapse-item>
      </el-collapse>
    </section>

    <section class="panel">
      <header class="panel-header">
        <div>
          <h3>未回答问题</h3>
          <span class="sub">知识库覆盖缺口 — 无答案问题下钻，点击行查看链路追踪（Trace）归因</span>
        </div>
      </header>
      <el-table
        v-if="noAnswerTraces.length"
        class="clickable-table"
        :data="noAnswerTraces"
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
        title="近期没有无答案问题"
        description="当回答被判为未确认（UNCONFIRMED）时，问题会出现在这里，用于定位知识库覆盖缺口。"
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
  grid-template-columns: repeat(3, minmax(0, 1fr));
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

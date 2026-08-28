<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { Box, ChatDotRound, Clock, Files, Notebook, Warning } from "@element-plus/icons-vue";

import { adminApi } from "@/api/admin";
import { useAdminStore } from "@/composables/adminStore";
import MetricCard from "@/components/ui/MetricCard.vue";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import EmptyState from "@/components/ui/EmptyState.vue";
import TrendChart, { type TrendPoint } from "@/components/ui/TrendChart.vue";
import type {
  MetricsOverview,
  MetricsTimeseriesPoint,
  SupportMetrics,
  TraceSummary,
} from "@/types/admin";

const store = useAdminStore();

const overview = ref<MetricsOverview | null>(null);
const supportMetrics = ref<SupportMetrics | null>(null);
const timeseries = ref<MetricsTimeseriesPoint[]>([]);
const noAnswerTraces = ref<TraceSummary[]>([]);
const metricsError = ref("");

const drilldown = ref<{ label: string; from: string; to: string } | null>(null);
const drilldownTraces = ref<TraceSummary[]>([]);
const drilldownLoading = ref(false);

const percent = (value: number) => `${(value * 100).toFixed(1)}%`;

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

async function loadMetrics() {
  const knowledgeId = store.selectedKnowledgeId;
  metricsError.value = "";
  overview.value = null;
  supportMetrics.value = null;
  timeseries.value = [];
  noAnswerTraces.value = [];
  const params = { knowledgeId: knowledgeId || undefined };
  const results = await Promise.allSettled([
    adminApi.getMetricsOverview(params),
    adminApi.getMetricsTimeseries(params),
    adminApi.listNoAnswerTraces(knowledgeId || undefined),
    adminApi.getSupportMetrics(params),
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
  if (overview.value === null || results.some((result) => result.status === "rejected")) {
    metricsError.value = "部分质量指标暂时无法加载，请稍后重试；其他管理功能不受影响。";
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

onMounted(loadMetrics);
watch(() => store.selectedKnowledgeId, () => void loadMetrics());
</script>

<template>
  <div class="dashboard">
    <section class="panel workflow-panel">
      <header class="panel-header">
        <div>
          <h3>管理待办</h3>
          <span class="sub">当前知识库的构建、审核和发布状态</span>
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
          <el-button @click="store.refreshOperationalState()">刷新状态</el-button>
        </div>
      </header>
      <div class="workflow-grid">
        <button type="button" @click="store.navigate('indexes')">
          <span>处理中（Processing）</span><strong>{{ store.buildingCount }}</strong>
          <small>{{ store.buildingCount ? "文档正在处理" : "当前没有处理任务" }}</small>
        </button>
        <button type="button" @click="store.navigate('indexes')">
          <span>RAG 在线</span>
          <strong class="state-text">{{ store.activeIndex ? `v${store.activeIndex.version}` : "未就绪" }}</strong>
          <small>技术构建成功后自动切换</small>
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
    </section>
    <div v-if="overview" class="metric-grid">
      <MetricCard
        label="查询量（7天）"
        :value="overview.total_traces.toLocaleString()"
        :context="`${overview.unique_users} 个独立用户`"
        :icon="ChatDotRound"
      />
      <MetricCard
        label="无答案率（No-Answer Rate）"
        :value="percent(overview.no_answer_rate)"
        :context="`UNCONFIRMED + OUT_OF_SCOPE / ${overview.classified_traces}`"
        :icon="Warning"
      />
      <MetricCard
        label="失败率（Failure Rate）"
        :value="percent(overview.failure_rate)"
        :context="`失败 ${overview.failed} / ${overview.total_traces}`"
        :icon="Clock"
      />
      <MetricCard
        label="P95 延迟（Latency）"
        :value="`${overview.latency.p95_ms.toLocaleString()} ms`"
        :context="`P50 ${overview.latency.p50_ms.toLocaleString()} ms`"
        :icon="Box"
      />
      <MetricCard
        label="LLM Token 数（7天）"
        :value="overview.tokens.llm_total.toLocaleString()"
        :context="`Rerank ${overview.tokens.rerank_total.toLocaleString()}`"
        :icon="Files"
      />
      <MetricCard
        label="文档交付（Doc Delivered）"
        :value="percent(overview.doc_delivered_rate)"
        :context="`零 LLM 快路径占比`"
        :icon="Notebook"
      />
    </div>
    <el-alert
      v-else-if="metricsError"
      :title="metricsError"
      type="warning"
      :closable="false"
      show-icon
    />

    <section v-if="supportMetrics" class="panel">
      <header class="panel-header">
        <div>
          <h3>技术支持（Technical Support，7天）</h3>
          <span class="sub">只统计 Support V2 链路追踪（Trace）；Hit@1 与切换准确率在标注集离线评测中计算</span>
        </div>
      </header>
      <div class="metric-grid support-metric-grid">
        <MetricCard
          label="Support 轮次（Support Turns）"
          :value="supportMetrics.support_turns.toLocaleString()"
          :context="`首轮 ${supportMetrics.first_turns} · 后续 ${supportMetrics.followup_turns}`"
          :icon="ChatDotRound"
        />
        <MetricCard
          label="文档交付（Document Delivery）"
          :value="percent(supportMetrics.document_delivery_rate)"
          :context="`${supportMetrics.document_delivered} 次完整文档交付`"
          :icon="Notebook"
        />
        <MetricCard
          label="澄清（Clarification）"
          :value="percent(supportMetrics.clarification_rate)"
          :context="`后续成功 ${percent(supportMetrics.clarification_resolution_rate)}`"
          :icon="Warning"
        />
        <MetricCard
          label="升级处理（Escalation）"
          :value="percent(supportMetrics.escalation_rate)"
          :context="`Workflow 冲突 ${supportMetrics.workflow_conflicts} · P95 ${supportMetrics.followup_latency.p95_ms} ms`"
          :icon="Clock"
        />
      </div>
    </section>

    <section v-if="chartPoints.length" class="panel">
      <header class="panel-header">
        <div>
          <h3>查询量与无答案率（Query Volume &amp; No-Answer Rate）</h3>
          <span class="sub">最近 7 天 · 点击柱形下钻该时段的链路追踪（Trace）</span>
        </div>
      </header>
      <TrendChart :points="chartPoints" @select="openBucketDrilldown" />
    </section>

    <section class="panel">
      <header class="panel-header">
        <div>
          <h3>未回答问题（Unanswered Questions）</h3>
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
          label="问题（Question）"
          min-width="280"
          show-overflow-tooltip
        />
        <el-table-column prop="response_type" label="响应（Response）" width="150">
          <template #default="scope">
            <StatusBadge :status="scope.row.response_type ?? 'UNKNOWN'" />
          </template>
        </el-table-column>
        <el-table-column prop="user_id" label="用户（User）" width="140">
          <template #default="scope">{{ scope.row.user_id ?? "—" }}</template>
        </el-table-column>
        <el-table-column label="时间（Time）" min-width="170">
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
          <h3>近期活动（Recent Activity）</h3>
          <span class="sub">最近的调试台（Playground）与对话（Chat）查询，点击行查看链路追踪（Trace）。</span>
        </div>
      </header>
      <el-table
        v-if="store.traces.length"
        class="clickable-table"
        :data="store.traces.slice(0, 10)"
        @row-click="(row: any) => store.openTrace(row.trace_id)"
      >
        <el-table-column
          prop="question"
          label="查询（Query）"
          min-width="280"
          show-overflow-tooltip
        />
        <el-table-column prop="mode" label="模式（Mode）" width="120" />
        <el-table-column label="状态（Status）" width="130">
          <template #default="scope"><StatusBadge :status="scope.row.status" /></template>
        </el-table-column>
        <el-table-column label="延迟（Latency）" width="110">
          <template #default="scope">
            <span class="mono">{{ scope.row.latency.total_ms ?? "—" }} ms</span>
          </template>
        </el-table-column>
        <el-table-column label="时间（Time）" min-width="170">
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
          label="问题（Question）"
          min-width="240"
          show-overflow-tooltip
        />
        <el-table-column label="响应（Response）" width="140">
          <template #default="scope">
            <StatusBadge :status="scope.row.response_type ?? scope.row.status" />
          </template>
        </el-table-column>
        <el-table-column label="延迟（Latency）" width="100">
          <template #default="scope">
            <span class="mono">{{ scope.row.latency?.total_ms ?? "—" }} ms</span>
          </template>
        </el-table-column>
        <el-table-column prop="user_id" label="用户（User）" width="120">
          <template #default="scope">{{ scope.row.user_id ?? "—" }}</template>
        </el-table-column>
      </el-table>
      <EmptyState
        v-if="!drilldownLoading && !drilldownTraces.length"
        title="该时段没有链路追踪（Trace）"
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

:deep(.clickable-table .el-table__row) {
  cursor: pointer;
}

@media (max-width: 760px) {
  .workflow-grid {
    grid-template-columns: 1fr;
  }
}
</style>

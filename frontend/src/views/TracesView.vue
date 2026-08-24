<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { Refresh, Search, SetUp } from "@element-plus/icons-vue";

import { adminApi } from "@/api/admin";
import { useAdminStore } from "@/composables/adminStore";
import type { RagTrace, TraceSummary } from "@/types/admin";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import EmptyState from "@/components/ui/EmptyState.vue";
import CodeBlock from "@/components/ui/CodeBlock.vue";

const store = useAdminStore();

const traceSearch = ref("");
const activeTraceDetail = ref<RagTrace | null>(null);
const loadingDetail = ref(false);

const RESPONSE_TYPES = ["ANSWERED", "UNCONFIRMED", "OUT_OF_SCOPE", "DOC_DELIVERED"] as const;
const STATUSES = ["RUNNING", "COMPLETED", "FAILED", "CANCELLED"] as const;

const traceFilter = ref<{
  status: string;
  responseType: string;
  userId: string;
  range: [Date, Date] | null;
}>({
  status: "",
  responseType: "",
  userId: "",
  range: null,
});
const traces = ref<TraceSummary[]>([]);
const loadingTraces = ref(false);

async function loadTraces(): Promise<void> {
  loadingTraces.value = true;
  try {
    traces.value = await adminApi.listTraces(store.selectedKnowledgeId || undefined, {
      status: traceFilter.value.status || undefined,
      responseType: traceFilter.value.responseType || undefined,
      userId: traceFilter.value.userId.trim() || undefined,
      from: traceFilter.value.range?.[0]?.toISOString(),
      to: traceFilter.value.range?.[1]?.toISOString(),
      limit: 200,
    });
  } finally {
    loadingTraces.value = false;
  }
}

function resetFilters(): void {
  traceFilter.value = { status: "", responseType: "", userId: "", range: null };
  void loadTraces();
}

onMounted(() => {
  if (store.traces.length) {
    traces.value = store.traces;
  }
  void loadTraces();
});

const LATENCY_STEPS: Array<{ key: string; label: string }> = [
  { key: "query_rewrite_ms", label: "Query Rewrite" },
  { key: "query_embedding_ms", label: "Embedding" },
  { key: "bm25_retrieval_ms", label: "Vector / BM25 Search" },
  { key: "vector_retrieval_ms", label: "Vector Search" },
  { key: "fusion_ms", label: "Fusion" },
  { key: "rerank_ms", label: "Rerank" },
  { key: "context_expansion_ms", label: "Context Expansion" },
  { key: "prompt_ms", label: "Prompt Build" },
  { key: "llm_ms", label: "LLM Generation" },
  { key: "total_ms", label: "Total" },
];

const filteredTraces = computed(() => {
  const keyword = traceSearch.value.trim().toLowerCase();
  if (!keyword) return traces.value;
  return traces.value.filter((trace) => trace.question.toLowerCase().includes(keyword));
});

const timeline = computed(() => {
  const trace = activeTraceDetail.value;
  if (!trace) return [];
  return LATENCY_STEPS.filter((step) => trace.latency?.[step.key] !== undefined).map((step) => ({
    ...step,
    ms: trace.latency[step.key] as number,
  }));
});

const detailSources = computed(() => {
  const trace = activeTraceDetail.value;
  if (!Array.isArray(trace?.sources)) return [];
  return trace.sources as Array<Record<string, unknown>>;
});

const detailUsage = computed(() => {
  const usage = activeTraceDetail.value?.model_usage;
  if (!usage || typeof usage !== "object" || !("total_tokens" in (usage as object))) return null;
  return usage as { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number };
});

const citationWarning = computed(() => {
  const result = activeTraceDetail.value?.citation_result as
    | { citation_missing?: boolean; invalid_source_ids?: string[] }
    | undefined;
  if (!result || !result.citation_missing) return null;
  const invalid = result.invalid_source_ids ?? [];
  return invalid.length ? `答案缺少引用，且包含无效引用：${invalid.join("、")}` : "答案未引用任何来源";
});

const STAGE_LABELS: Record<string, string> = {
  prepare: "请求准备",
  query_embedding: "查询向量化",
  routing: "文档路由",
  rerank: "重排",
  llm: "答案生成",
};

const errorDescription = computed(() => {
  const error = activeTraceDetail.value?.error as
    | { message?: string; detail?: string; stage?: string; retryable?: boolean }
    | undefined;
  if (!error) return "";
  const parts: string[] = [];
  if (error.detail) parts.push(`上游详情：${error.detail}`);
  if (error.stage) parts.push(`失败阶段：${STAGE_LABELS[error.stage] ?? error.stage}`);
  if (error.retryable != null) parts.push(error.retryable ? "可重试" : "不可重试");
  return [error.message, ...parts].filter(Boolean).join(" · ");
});

async function selectTrace(traceId: string): Promise<void> {
  loadingDetail.value = true;
  try {
    activeTraceDetail.value = await store.openTraceSilent(traceId);
  } finally {
    loadingDetail.value = false;
  }
}
</script>

<template>
  <div class="trace-view">
    <!-- Trace list -->
    <section class="panel trace-list">
      <header class="panel-header">
        <div>
          <h3>Traces</h3>
          <span class="sub">{{ filteredTraces.length }} 条记录</span>
        </div>
      </header>
      <div class="panel-body list-toolbar">
        <el-select
          v-model="traceFilter.status"
          placeholder="状态"
          clearable
          class="filter-select"
          @change="loadTraces"
        >
          <el-option
            v-for="s in STATUSES"
            :key="s"
            :label="s"
            :value="s"
          />
        </el-select>
        <el-select
          v-model="traceFilter.responseType"
          placeholder="响应类型"
          clearable
          class="filter-select"
          @change="loadTraces"
        >
          <el-option
            v-for="type in RESPONSE_TYPES"
            :key="type"
            :label="type"
            :value="type"
          />
        </el-select>
        <el-input
          v-model="traceFilter.userId"
          placeholder="用户 ID"
          clearable
          class="filter-user"
          @keyup.enter="loadTraces"
          @clear="loadTraces"
        />
        <el-date-picker
          v-model="traceFilter.range"
          type="datetimerange"
          range-separator="→"
          start-placeholder="开始时间"
          end-placeholder="结束时间"
          class="filter-range"
        />
        <el-button :icon="Refresh" :loading="loadingTraces" @click="loadTraces">
          查询
        </el-button>
        <el-button text @click="resetFilters">重置</el-button>
        <el-input
          v-model="traceSearch"
          placeholder="在结果中搜索问题..."
          clearable
          :prefix-icon="Search"
          class="filter-search"
        />
      </div>
      <div v-loading="loadingDetail" class="trace-rows">
        <button
          v-for="trace in filteredTraces"
          :key="trace.trace_id"
          type="button"
          class="trace-row"
          :class="{ active: activeTraceDetail?.trace_id === trace.trace_id }"
          @click="selectTrace(trace.trace_id)"
        >
          <div class="trace-row-main">
            <b class="trace-question">{{ trace.question }}</b>
            <small class="trace-meta">
              {{ trace.mode }} · {{ store.formatDate(trace.created_at) }}
              <template v-if="trace.user_id"> · {{ trace.user_id }}</template>
              <template v-if="trace.error?.code"> · ⚠ {{ trace.error.code }}</template>
            </small>
          </div>
          <div class="trace-row-side">
            <StatusBadge
              v-if="trace.response_type"
              :status="trace.response_type"
            />
            <StatusBadge :status="trace.status" />
            <small class="mono">{{ trace.latency.total_ms ?? "—" }} ms</small>
          </div>
        </button>
        <EmptyState
          v-if="!filteredTraces.length"
          title="暂无 Trace"
          description="发起 Playground 查询或 Demo Chat 对话后，这里会记录完整链路。"
        />
      </div>
    </section>

    <!-- Trace detail -->
    <section class="panel trace-detail">
      <template v-if="activeTraceDetail">
        <header class="panel-header">
          <div>
            <h3 class="detail-question">{{ activeTraceDetail.question }}</h3>
            <div class="detail-meta">
              <StatusBadge :status="activeTraceDetail.status" />
              <StatusBadge
                v-if="activeTraceDetail.response_type"
                :status="activeTraceDetail.response_type"
              />
              <span class="muted">{{ activeTraceDetail.mode }}</span>
              <span v-if="activeTraceDetail.user_id" class="muted">
                用户 {{ activeTraceDetail.user_id }}
              </span>
              <span class="mono muted">{{ activeTraceDetail.trace_id }}</span>
            </div>
          </div>
        </header>
        <div class="panel-body">
          <el-alert
            v-if="activeTraceDetail.error?.code"
            :title="`错误：${activeTraceDetail.error.code}`"
            :description="errorDescription"
            type="error"
            :closable="false"
            show-icon
            class="detail-error"
          />
          <el-alert
            v-if="citationWarning"
            :title="citationWarning"
            type="warning"
            :closable="false"
            show-icon
            class="detail-error"
          />
          <h4 class="section-label">Answer</h4>
          <p class="detail-answer">{{ activeTraceDetail.answer || "（无答案）" }}</p>

          <h4 class="section-label">Sources（答案引用来源）</h4>
          <ul v-if="detailSources.length" class="source-list">
            <li v-for="source in detailSources" :key="String(source.source_id)">
              <b>{{ source.document }}</b>
              <small v-if="Array.isArray(source.section_path) && source.section_path.length">
                · {{ (source.section_path as string[]).join(" / ") }}
              </small>
              <small v-if="source.page != null"> · 第 {{ source.page }} 页</small>
            </li>
          </ul>
          <p v-else class="muted detail-empty">（答案未引用任何来源）</p>

          <div v-if="detailUsage" class="usage-row">
            <h4 class="section-label">Model Usage</h4>
            <span class="mono">
              prompt {{ detailUsage.prompt_tokens ?? 0 }} · completion
              {{ detailUsage.completion_tokens ?? 0 }} · total
              {{ detailUsage.total_tokens ?? 0 }} tokens
            </span>
          </div>

          <h4 class="section-label">Timeline</h4>
          <ol class="latency-timeline">
            <li v-for="step in timeline" :key="step.key" :class="{ total: step.key === 'total_ms' }">
              <span class="step-name">{{ step.label }}</span>
              <span class="step-bar" :style="{ width: `${Math.min(100, (step.ms / (timeline[timeline.length - 1]?.ms || 1)) * 100)}%` }" />
              <span class="step-ms mono">{{ step.ms }} ms</span>
            </li>
          </ol>

          <el-collapse class="detail-collapse">
            <el-collapse-item title="Retrieval" name="retrieval">
              <CodeBlock :code="store.formatJson(activeTraceDetail.retrieval_result)" max-height="280px" />
            </el-collapse-item>
            <el-collapse-item title="Rerank" name="rerank">
              <CodeBlock :code="store.formatJson(activeTraceDetail.rerank_result)" max-height="280px" />
            </el-collapse-item>
            <el-collapse-item title="Prompt" name="prompt">
              <CodeBlock :code="activeTraceDetail.prompt ?? '(empty)'" max-height="280px" />
            </el-collapse-item>
            <el-collapse-item title="Raw JSON" name="raw">
              <CodeBlock :code="store.formatJson(activeTraceDetail)" />
            </el-collapse-item>
          </el-collapse>
        </div>
      </template>
      <EmptyState
        v-else
        :icon="SetUp"
        title="未选择 Trace"
        description="从左侧列表选择一条记录，查看请求时间线、检索过程与生成详情。"
      />
    </section>
  </div>
</template>

<style scoped>
.trace-view {
  display: grid;
  grid-template-columns: minmax(320px, 420px) minmax(0, 1fr);
  gap: var(--space-content-gap);
  align-items: start;
}

.trace-list {
  overflow: hidden;
}

.list-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  padding-bottom: 12px;
}

.filter-select {
  width: 150px;
}

.filter-user {
  width: 140px;
}

.filter-range {
  width: 340px;
}

.filter-search {
  flex: 1;
  min-width: 160px;
}

.trace-rows {
  max-height: calc(100vh - 300px);
  overflow-y: auto;
  display: grid;
}

.trace-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 18px;
  border: 0;
  border-top: 1px solid var(--border-subtle);
  background: transparent;
  text-align: left;
  cursor: pointer;
  transition: background-color var(--transition-fast);
}

.trace-row:hover {
  background: var(--bg-hover);
}

.trace-row.active {
  background: var(--brand-subtle);
}

.trace-row-main {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.trace-question {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.trace-meta {
  overflow: hidden;
  color: var(--text-tertiary);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.trace-row-side {
  display: grid;
  gap: 4px;
  justify-items: end;
}

.trace-row-side small {
  color: var(--text-tertiary);
  font-size: 11px;
}

.detail-question {
  font-size: 15px;
  font-weight: 600;
  line-height: 1.4;
}

.detail-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 6px;
}

.detail-answer {
  color: var(--text-primary);
  font-size: 14px;
  line-height: 1.8;
  white-space: pre-wrap;
}

.section-label {
  margin: 0 0 8px;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 600;
}

.section-label + .detail-answer {
  margin-bottom: 16px;
}

.source-list {
  display: grid;
  gap: 4px;
  margin: 0 0 16px;
  padding: 0 0 0 2px;
  list-style: none;
}

.source-list li {
  color: var(--text-secondary);
  font-size: 13px;
}

.source-list small {
  color: var(--text-tertiary);
}

.detail-empty {
  margin: 0 0 16px;
  font-size: 13px;
}

.usage-row {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 16px;
}

.usage-row .section-label {
  margin: 0;
}

.usage-row span {
  color: var(--text-secondary);
  font-size: 12px;
}

.latency-timeline {
  display: grid;
  gap: 6px;
  margin: 0 0 16px;
  padding: 0;
  list-style: none;
}

.latency-timeline li {
  display: grid;
  grid-template-columns: 150px 1fr 70px;
  align-items: center;
  gap: 10px;
  font-size: 12px;
}

.latency-timeline li.total {
  padding-top: 6px;
  border-top: 1px solid var(--border-subtle);
  font-weight: 600;
}

.step-name {
  color: var(--text-secondary);
}

.step-bar {
  height: 4px;
  min-width: 2px;
  border-radius: 2px;
  background: var(--brand-primary);
  opacity: 0.75;
}

.step-ms {
  color: var(--text-secondary);
  text-align: right;
}

.detail-collapse {
  border-top: 1px solid var(--border-subtle);
}

@media (max-width: 1000px) {
  .trace-view {
    grid-template-columns: 1fr;
  }

  .trace-rows {
    max-height: 320px;
  }
}
</style>

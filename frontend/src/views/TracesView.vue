<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
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
const loadError = ref("");

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Trace 加载失败";
}

const RESPONSE_TYPES = [
  "ANSWERED",
  "UNCONFIRMED",
  "OUT_OF_SCOPE",
  "DOC_DELIVERED",
  "DOCUMENT_DELIVERED",
  "CLARIFICATION_REQUIRED",
  "NO_MATCH",
  "ESCALATION_REQUIRED",
] as const;
const STATUSES = ["RUNNING", "COMPLETED", "FAILED", "CANCELLED"] as const;
const SOURCES = [
  { value: "ASSISTANT_API", label: "线上 Assistant" },
  { value: "INTERNAL_CHAT", label: "DemoChat / 内部聊天" },
  { value: "PLAYGROUND", label: "调试台" },
] as const;
const sourceLabel: Record<(typeof SOURCES)[number]["value"], string> = Object.fromEntries(
  SOURCES.map((item) => [item.value, item.label]),
) as Record<(typeof SOURCES)[number]["value"], string>;

const traceFilter = ref<{
  status: string;
  responseType: string;
  source: "" | (typeof SOURCES)[number]["value"];
  userId: string;
  requestId: string;
  range: [Date, Date] | null;
}>({
  status: "",
  responseType: "",
  source: "",
  userId: "",
  requestId: "",
  range: null,
});
const traces = ref<TraceSummary[]>([]);
const loadingTraces = ref(false);

async function loadTraces(): Promise<void> {
  loadingTraces.value = true;
  loadError.value = "";
  try {
    traces.value = await adminApi.listTraces(store.selectedKnowledgeId || undefined, {
      status: traceFilter.value.status || undefined,
      responseType: traceFilter.value.responseType || undefined,
      source: traceFilter.value.source || undefined,
      userId: traceFilter.value.userId.trim() || undefined,
      requestId: traceFilter.value.requestId.trim() || undefined,
      from: traceFilter.value.range?.[0]?.toISOString(),
      to: traceFilter.value.range?.[1]?.toISOString(),
      limit: 200,
    });
  } catch (error) {
    traces.value = [];
    loadError.value = errorMessage(error);
  } finally {
    loadingTraces.value = false;
  }
}

function resetFilters(): void {
  traceFilter.value = {
    status: "",
    responseType: "",
    source: "",
    userId: "",
    requestId: "",
    range: null,
  };
  void loadTraces();
}

onMounted(() => {
  if (store.traces.length) {
    traces.value = store.traces;
  }
  void loadTraces();
});

watch(() => store.selectedKnowledgeId, () => void loadTraces());

const LATENCY_STEPS: Array<{ key: string; label: string }> = [
  { key: "v2_query_interpreter_ms", label: "查询理解（LLM 1）" },
  { key: "v2_document_retrieval_ms", label: "检索与文档级重排" },
  { key: "v2_document_context_ms", label: "全文上下文构建" },
  { key: "v2_answer_generator_ms", label: "答案生成（LLM 2）" },
  { key: "prepare_turn_ms", label: "轮次准备" },
  { key: "query_rewrite_ms", label: "Query Rewrite（遗留）" },
  { key: "query_embedding_ms", label: "Embedding（遗留）" },
  { key: "bm25_retrieval_ms", label: "Vector / BM25 Search（遗留）" },
  { key: "vector_retrieval_ms", label: "Vector Search（遗留）" },
  { key: "fusion_ms", label: "Fusion（遗留）" },
  { key: "rerank_ms", label: "Rerank（遗留）" },
  { key: "context_expansion_ms", label: "Context Expansion（遗留）" },
  { key: "prompt_ms", label: "Prompt Build（遗留）" },
  { key: "llm_ms", label: "LLM Generation（遗留）" },
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

type SupportTrace = {
  support_version: number;
  focus_document_id?: string;
  support_mode?: string;
  problem_resolution?: { method?: string };
  document_switch?: { query?: string };
  validation_result?: { valid?: boolean; code?: string };
  workflow_before?: unknown;
  workflow_after?: unknown;
  document_claims?: unknown;
  escalation?: unknown;
  failure?: { code?: string };
};

const supportTrace = computed(() => {
  const trace = activeTraceDetail.value?.retrieval_result;
  return trace && trace.support_version === 1 ? (trace as SupportTrace) : null;
});

type V2Generation = {
  pipeline?: string;
  status?: string;
  diagnostic_reason?: "LOW_INFORMATION" | "SCOPE_CONFLICT" | "NEEDS_KEY_CONDITION";
  input_guard?: string;
  query_interpreter?: { status?: string; reason?: string; latency_ms?: number };
  retrieval?: { degradations?: string[]; document_rerank?: { status?: string; reason?: string } };
  context?: { document_count?: number; image_count?: number; estimated_tokens?: number; mode?: string };
  answer?: {
    response_kind?: string;
    basis?: string;
    used_document_count?: number;
    used_image_count?: number;
    public_leak_repair_count?: number;
    unsupported_parameter_count?: number;
  };
};

const DIAGNOSTIC_REASON_LABELS: Record<string, string> = {
  LOW_INFORMATION: "输入信息不足",
  SCOPE_CONFLICT: "范围判断冲突（已按查询理解收口）",
  NEEDS_KEY_CONDITION: "缺少关键条件",
};

const v2Generation = computed(() => {
  const snapshot = activeTraceDetail.value?.runtime_snapshot as
    | { generation?: V2Generation }
    | undefined;
  const generation = snapshot?.generation;
  return generation?.pipeline === "V2" ? generation : null;
});

const v2Degradations = computed(() => {
  const items = v2Generation.value?.retrieval?.degradations ?? [];
  return items.filter(Boolean);
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
  loadError.value = "";
  try {
    activeTraceDetail.value = await store.openTraceSilent(traceId);
  } catch (error) {
    activeTraceDetail.value = null;
    loadError.value = errorMessage(error);
  } finally {
    loadingDetail.value = false;
  }
}
</script>

<template>
  <div class="trace-view">
    <el-alert
      v-if="loadError"
      class="trace-load-error"
      :title="loadError"
      type="error"
      show-icon
      @close="loadError = ''"
    />
    <!-- Trace list -->
    <section class="panel trace-list">
      <header class="panel-header">
        <div>
          <h3>链路追踪（Traces）</h3>
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
          v-model="traceFilter.source"
          placeholder="调用来源"
          clearable
          class="filter-select"
          @change="loadTraces"
        >
          <el-option
            v-for="item in SOURCES"
            :key="item.value"
            :label="item.label"
            :value="item.value"
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
        <el-input
          v-model="traceFilter.requestId"
          placeholder="请求 ID"
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
      <div v-loading="loadingTraces" class="trace-rows">
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
              {{ trace.source ? sourceLabel[trace.source] : "DemoChat / 内部聊天" }} ·
              {{ trace.engine ?? "RAG" }} ·
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
          v-if="!loadingTraces && !loadError && !filteredTraces.length"
          title="暂无链路追踪（Trace）"
          description="发起 RAG 调试台（Playground）查询或问答测试（DemoChat）对话后，这里会记录完整链路。"
        />
      </div>
    </section>

    <!-- Trace detail -->
    <section v-loading="loadingDetail" class="panel trace-detail">
      <template v-if="activeTraceDetail">
        <header class="panel-header">
          <div>
            <h3 class="detail-question">{{ activeTraceDetail.question }}</h3>
            <div class="detail-meta">
              <StatusBadge :status="activeTraceDetail.status" />
              <StatusBadge :status="activeTraceDetail.engine ?? 'RAG'" />
              <StatusBadge
                v-if="activeTraceDetail.response_type"
                :status="activeTraceDetail.response_type"
              />
              <span class="muted">{{ activeTraceDetail.mode }}</span>
              <span v-if="activeTraceDetail.user_id" class="muted">
                用户 {{ activeTraceDetail.user_id }}
              </span>
              <span class="mono muted">{{ activeTraceDetail.trace_id }}</span>
              <span class="mono muted">请求 {{ activeTraceDetail.request_id }}</span>
              <el-button
                v-if="activeTraceDetail.conversation_id"
                text
                size="small"
                @click="store.openConversation(activeTraceDetail.conversation_id!)"
              >
                查看会话回放
              </el-button>
            </div>
          </div>
        </header>
        <div class="panel-body">
          <el-alert
            v-if="activeTraceDetail.parent_trace_id"
            :title="`由 ${activeTraceDetail.fallback_from ?? 'SUPPORT'} 降级到当前引擎`"
            :description="`原因：${activeTraceDetail.fallback_reason ?? '未记录'} · 父链路（Trace）：${activeTraceDetail.parent_trace_id}`"
            type="info"
            :closable="false"
            show-icon
            class="detail-error fallback-link"
          >
            <template #default>
              <el-button
                text
                size="small"
                @click="selectTrace(activeTraceDetail.parent_trace_id!)"
              >
                查看父链路（Trace）
              </el-button>
            </template>
          </el-alert>
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
          <h4 class="section-label">回答（Answer）</h4>
          <p class="detail-answer">{{ activeTraceDetail.answer || "（无答案）" }}</p>
          <section v-if="v2Generation" class="support-trace">
            <h4>RAG V2 生成详情</h4>
            <dl>
              <template v-if="v2Generation.diagnostic_reason">
                <dt>诊断原因</dt>
                <dd>
                  {{ DIAGNOSTIC_REASON_LABELS[v2Generation.diagnostic_reason] ?? v2Generation.diagnostic_reason }}
                </dd>
              </template>
              <template v-if="v2Generation.input_guard">
                <dt>输入门禁</dt>
                <dd>{{ v2Generation.input_guard }}</dd>
              </template>
              <dt>查询理解</dt>
              <dd>
                {{ v2Generation.query_interpreter?.status ?? "—" }}
                <template v-if="v2Generation.query_interpreter?.reason">
                  （{{ v2Generation.query_interpreter.reason }}）
                </template>
              </dd>
              <dt>文档级重排</dt>
              <dd>
                {{ v2Generation.retrieval?.document_rerank?.status ?? "—" }}
                <template v-if="v2Generation.retrieval?.document_rerank?.reason">
                  （{{ v2Generation.retrieval?.document_rerank?.reason }}）
                </template>
              </dd>
              <dt>检索降级</dt>
              <dd>{{ v2Degradations.length ? v2Degradations.join("；") : "无" }}</dd>
              <dt>隐藏上下文</dt>
              <dd>
                {{ v2Generation.context?.document_count ?? 0 }} 份文档 ·
                {{ v2Generation.context?.image_count ?? 0 }} 张图片 ·
                约 {{ v2Generation.context?.estimated_tokens ?? 0 }} tokens
              </dd>
              <dt>回答形式</dt>
              <dd>
                {{ v2Generation.answer?.response_kind ?? "—" }} / {{ v2Generation.answer?.basis ?? "—" }}
                · 采用 {{ v2Generation.answer?.used_document_count ?? 0 }} 份文档、{{ v2Generation.answer?.used_image_count ?? 0 }} 张图片
              </dd>
              <dt>合规修复</dt>
              <dd>
                {{ v2Generation.answer?.public_leak_repair_count ?? 0 }} 次
                <template v-if="v2Generation.answer?.unsupported_parameter_count">
                  · 未支持参数观察 {{ v2Generation.answer.unsupported_parameter_count }} 项
                </template>
              </dd>
            </dl>
          </section>

          <section v-if="supportTrace" class="support-trace">
            <h4>技术支持决策（Technical Support · 遗留）</h4>
            <dl>
              <dt>聚焦文档</dt><dd class="mono">{{ supportTrace.focus_document_id ?? "—" }}</dd>
              <dt>支持模式</dt><dd>{{ supportTrace.support_mode ?? "—" }}</dd>
              <dt>路由/切换</dt><dd>{{ supportTrace.problem_resolution?.method ?? supportTrace.document_switch?.query ?? "—" }}</dd>
              <dt>校验结果</dt><dd>{{ supportTrace.validation_result?.valid === true ? "已通过" : supportTrace.validation_result?.valid === false ? `未通过（${supportTrace.validation_result.code ?? "UNKNOWN"}）` : "—" }}</dd>
            </dl>
            <CodeBlock :code="store.formatJson({ workflow_before: supportTrace.workflow_before, workflow_after: supportTrace.workflow_after, document_claims: supportTrace.document_claims, escalation: supportTrace.escalation, failure: supportTrace.failure })" max-height="220px" />
          </section>

          <h4 class="section-label">引用来源（Sources）</h4>
          <ul v-if="detailSources.length" class="source-list">
            <li v-for="source in detailSources" :key="String(source.source_id)">
              <b>{{ source.document }}</b>
              <small v-if="Array.isArray(source.section_path) && source.section_path.length">
                · {{ (source.section_path as string[]).join(" / ") }}
              </small>
              <small v-if="source.page != null"> · 第 {{ source.page }} 页</small>
            </li>
          </ul>
          <p v-else-if="v2Generation" class="muted detail-empty">
            （无公开来源：RAG V2 不公开引用与原文，候选详情见上方生成详情与运行快照）
          </p>
          <p v-else class="muted detail-empty">（答案未引用任何来源）</p>

          <div v-if="detailUsage" class="usage-row">
            <h4 class="section-label">模型使用量（Model Usage）</h4>
            <span class="mono">
              prompt {{ detailUsage.prompt_tokens ?? 0 }} · completion
              {{ detailUsage.completion_tokens ?? 0 }} · total
              {{ detailUsage.total_tokens ?? 0 }} tokens
            </span>
          </div>

          <h4 class="section-label">时间线（Timeline）</h4>
          <ol class="latency-timeline">
            <li v-for="step in timeline" :key="step.key" :class="{ total: step.key === 'total_ms' }">
              <span class="step-name">{{ step.label }}</span>
              <span class="step-bar" :style="{ width: `${Math.min(100, (step.ms / (timeline[timeline.length - 1]?.ms || 1)) * 100)}%` }" />
              <span class="step-ms mono">{{ step.ms }} ms</span>
            </li>
          </ol>

          <el-collapse class="detail-collapse">
            <el-collapse-item title="运行快照（模型、策略与索引）" name="runtime-snapshot">
              <CodeBlock :code="store.formatJson(activeTraceDetail.runtime_snapshot)" max-height="280px" />
            </el-collapse-item>
            <el-collapse-item title="检索（Retrieval）" name="retrieval">
              <CodeBlock :code="store.formatJson(activeTraceDetail.retrieval_result)" max-height="280px" />
            </el-collapse-item>
            <el-collapse-item title="重排序（Rerank · 遗留）" name="rerank">
              <CodeBlock :code="store.formatJson(activeTraceDetail.rerank_result)" max-height="280px" />
            </el-collapse-item>
            <el-collapse-item v-if="activeTraceDetail.prompt" title="提示词（Prompt · 遗留）" name="prompt">
              <CodeBlock :code="activeTraceDetail.prompt" max-height="280px" />
            </el-collapse-item>
            <el-collapse-item title="原始 JSON（Raw JSON）" name="raw">
              <CodeBlock :code="store.formatJson(activeTraceDetail)" />
            </el-collapse-item>
          </el-collapse>
        </div>
      </template>
      <EmptyState
        v-else
        :icon="SetUp"
        title="未选择链路追踪（Trace）"
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

.trace-load-error {
  grid-column: 1 / -1;
}

.fallback-link :deep(.el-alert__content) {
  min-width: 0;
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

.support-trace {
  margin: 16px 0;
  padding: 14px;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  background: var(--bg-subtle);
}

.support-trace h4 {
  margin: 0 0 10px;
  color: var(--text-primary);
  font-size: 13px;
}

.support-trace dl {
  display: grid;
  grid-template-columns: 96px minmax(0, 1fr);
  gap: 5px 10px;
  margin: 0 0 12px;
  font-size: 12px;
}

.support-trace dt { color: var(--text-tertiary); }
.support-trace dd { min-width: 0; margin: 0; color: var(--text-secondary); overflow-wrap: anywhere; }

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

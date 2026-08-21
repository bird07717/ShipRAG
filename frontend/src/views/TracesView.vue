<script setup lang="ts">
import { computed, ref } from "vue";
import { Search, SetUp } from "@element-plus/icons-vue";

import { useAdminStore } from "@/composables/adminStore";
import type { RagTrace } from "@/types/admin";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import EmptyState from "@/components/ui/EmptyState.vue";
import CodeBlock from "@/components/ui/CodeBlock.vue";

const store = useAdminStore();

const traceSearch = ref("");
const activeTraceDetail = ref<RagTrace | null>(null);
const loadingDetail = ref(false);

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
  if (!keyword) return store.traces;
  return store.traces.filter((trace) => trace.question.toLowerCase().includes(keyword));
});

const timeline = computed(() => {
  const trace = activeTraceDetail.value;
  if (!trace) return [];
  return LATENCY_STEPS.filter((step) => trace.latency?.[step.key] !== undefined).map((step) => ({
    ...step,
    ms: trace.latency[step.key] as number,
  }));
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
        <el-input
          v-model="traceSearch"
          placeholder="搜索问题..."
          clearable
          :prefix-icon="Search"
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
            </small>
          </div>
          <div class="trace-row-side">
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
              <span class="muted">{{ activeTraceDetail.mode }}</span>
              <span class="mono muted">{{ activeTraceDetail.trace_id }}</span>
            </div>
          </div>
        </header>
        <div class="panel-body">
          <h4 class="section-label">Answer</h4>
          <p class="detail-answer">{{ activeTraceDetail.answer || "（无答案）" }}</p>

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
  padding-bottom: 12px;
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

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { Refresh, Search, WarningFilled } from "@element-plus/icons-vue";

import { adminApi } from "@/api/admin";
import { useAdminStore } from "@/composables/adminStore";
import type {
  FailureSummary,
  RagTrace,
  RequestErrorLog,
  TaskRecord,
  TraceSummary,
} from "@/types/admin";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import EmptyState from "@/components/ui/EmptyState.vue";
import CodeBlock from "@/components/ui/CodeBlock.vue";
import MetricCard from "@/components/ui/MetricCard.vue";
import AnswerReviewsView from "@/views/AnswerReviewsView.vue";
import TracesView from "@/views/TracesView.vue";
import { createLatestRequest } from "@/utils/latestRequest";
import { clampedPage } from "@/utils/paging";
import ListPagination from "@/components/ui/ListPagination.vue";
import { replaceViewQuery } from "@/utils/viewQuery";
import { isTraceSource, TRACE_SOURCE_LABELS } from "@/utils/statusLabels";

const props = defineProps<{ navigationQuery?: string }>();
const store = useAdminStore();
const loadError = ref("");

const LOG_TABS = new Set(["assistant-answers", "request-errors", "failed-turns", "task-failures", "traces"]);
type TraceSource = "" | "ASSISTANT_API" | "INTERNAL_CHAT" | "PLAYGROUND";

function navigationState(query = ""): {
  tab: string;
  engine: "" | "SUPPORT" | "RAG";
  source: TraceSource;
  from: string;
  to: string;
} {
  const params = new URLSearchParams(query);
  const requestedTab = params.get("tab") ?? "request-errors";
  const requestedEngine = params.get("engine");
  const requestedSource = params.get("source");
  return {
    tab: LOG_TABS.has(requestedTab) ? requestedTab : "request-errors",
    engine: requestedEngine === "SUPPORT" || requestedEngine === "RAG" ? requestedEngine : "",
    source: isTraceSource(requestedSource) ? requestedSource : "",
    from: params.get("from") ?? "",
    to: params.get("to") ?? "",
  };
}

const initialNavigation = navigationState(props.navigationQuery);

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "管理日志加载失败";
}

/* ---------------- overview ---------------- */

const summary = ref<FailureSummary | null>(null);
const loadingSummary = ref(false);

const totalTraceFailures = computed(() =>
  summary.value?.trace_failures.reduce((total, group) => total + group.count, 0) ?? 0,
);
const totalRequestErrors = computed(() =>
  summary.value?.request_errors_by_day.reduce((total, item) => total + item.count, 0) ?? 0,
);
const topErrorCodes = computed(() => {
  const groups = summary.value?.trace_failures ?? [];
  return groups
    .slice()
    .sort((a, b) => b.count - a.count)
    .slice(0, 3)
    .map((group) => `${group.error_code} × ${group.count}`)
    .join(" · ");
});

async function loadSummary(): Promise<void> {
  loadingSummary.value = true;
  loadError.value = "";
  try {
    summary.value = await adminApi.getFailureSummary({
      knowledgeId: store.selectedKnowledgeId || undefined,
    });
  } catch (error) {
    summary.value = null;
    loadError.value = errorMessage(error);
  } finally {
    loadingSummary.value = false;
  }
}

/* ---------------- request errors ---------------- */

const requestErrorFilter = ref<{ requestId: string; path: string; statusCode: string }>({
  requestId: "",
  path: "",
  statusCode: "",
});
const requestErrors = ref<RequestErrorLog[]>([]);
const requestErrorTotal = ref(0);
const requestErrorPage = ref(1);
const requestErrorPageSize = 20;
const loadingRequestErrors = ref(false);
const loadingRequestErrorDetail = ref(false);
const selectedRequestError = ref<RequestErrorLog | null>(null);
const requestErrorDrawerOpen = ref(false);

async function loadRequestErrors(): Promise<void> {
  loadingRequestErrors.value = true;
  loadError.value = "";
  try {
    const result = await adminApi.listRequestErrors({
      requestId: requestErrorFilter.value.requestId || undefined,
      path: requestErrorFilter.value.path || undefined,
      statusCode: requestErrorFilter.value.statusCode
        ? Number(requestErrorFilter.value.statusCode)
        : undefined,
      limit: requestErrorPageSize,
      offset: (requestErrorPage.value - 1) * requestErrorPageSize,
    });
    requestErrors.value = result.items;
    requestErrorTotal.value = result.total;
  } catch (error) {
    requestErrors.value = [];
    requestErrorTotal.value = 0;
    loadError.value = errorMessage(error);
  } finally {
    loadingRequestErrors.value = false;
  }
}

function resetRequestErrorFilter(): void {
  requestErrorFilter.value = { requestId: "", path: "", statusCode: "" };
  requestErrorPage.value = 1;
  void loadRequestErrors();
}

async function openRequestError(row: RequestErrorLog): Promise<void> {
  selectedRequestError.value = row;
  requestErrorDrawerOpen.value = true;
  loadingRequestErrorDetail.value = true;
  loadError.value = "";
  try {
    selectedRequestError.value = await adminApi.getRequestError(row.id);
  } catch (error) {
    loadError.value = errorMessage(error);
  } finally {
    loadingRequestErrorDetail.value = false;
  }
}

const requestErrorDetailText = computed(() => {
  const detail = selectedRequestError.value?.detail;
  return detail && Object.keys(detail).length ? store.formatJson(detail) : "";
});

/* ---------------- failed support turns ---------------- */

const failedTraceFilter = ref<{ engine: "" | "SUPPORT" | "RAG"; errorCode: string; source: TraceSource; from: string; to: string }>({
  engine: initialNavigation.engine,
  errorCode: "",
  source: initialNavigation.source,
  from: initialNavigation.from,
  to: initialNavigation.to,
});
const failedTraceScope = computed(() => {
  const { source, from, to } = failedTraceFilter.value;
  return [source ? TRACE_SOURCE_LABELS[source] : "", from || to ? `${from ? store.formatDate(from) : "不限起始时间"} ～ ${to ? store.formatDate(to) : "不限结束时间"}` : ""].filter(Boolean).join(" · ");
});
const failedTraces = ref<TraceSummary[]>([]);
const loadingFailedTraces = ref(false);
const selectedFailedTrace = ref<RagTrace | null>(null);
const failedTraceDrawerOpen = ref(false);
const loadingFailedDetail = ref(false);
let failedTraceLoadGeneration = 0;

function invalidateFailedTraces(): void {
  failedTraceLoadGeneration++;
  failedTraces.value = [];
  loadingFailedTraces.value = false;
}

const failedTraceCodes = computed(() =>
  (summary.value?.trace_failures ?? []).map((group) => group.error_code),
);

// Failed turns and failed tasks page on the server (X-Total-Count).
const failedTracePage = ref(1);
const failedTracePageSize = ref(50);
const failedTraceTotal = ref(0);

/** Reload failed turns from the first page (filters, scope or tab changed). */
async function loadFailedTraces(): Promise<void> {
  failedTracePage.value = 1;
  await loadFailedTracePage();
}

async function loadFailedTracePage(): Promise<void> {
  const generation = ++failedTraceLoadGeneration;
  const knowledgeId = store.selectedKnowledgeId;
  const isCurrent = () => generation === failedTraceLoadGeneration && knowledgeId === store.selectedKnowledgeId;
  loadingFailedTraces.value = true;
  failedTraces.value = [];
  loadError.value = "";
  try {
    const result = await adminApi.listTracesPage(knowledgeId || undefined, {
      status: "FAILED",
      engine: failedTraceFilter.value.engine || undefined,
      errorCode: failedTraceFilter.value.errorCode || undefined,
      ...(failedTraceFilter.value.source ? { source: failedTraceFilter.value.source } : {}),
      ...(failedTraceFilter.value.from ? { from: failedTraceFilter.value.from } : {}),
      ...(failedTraceFilter.value.to ? { to: failedTraceFilter.value.to } : {}),
      limit: failedTracePageSize.value,
      offset: (failedTracePage.value - 1) * failedTracePageSize.value,
    });
    if (isCurrent()) {
      const target = clampedPage(failedTracePage.value, failedTracePageSize.value, result.items.length, result.total);
      if (target !== null) {
        failedTracePage.value = target;
        void loadFailedTracePage();
        return;
      }
      failedTraces.value = result.items;
      failedTraceTotal.value = result.total;
    }
  } catch (error) {
    if (isCurrent()) {
      failedTraces.value = [];
      failedTraceTotal.value = 0;
      loadError.value = errorMessage(error);
    }
  } finally {
    if (isCurrent()) loadingFailedTraces.value = false;
  }
}

async function openFailedTrace(traceId: string): Promise<void> {
  loadingFailedDetail.value = true;
  failedTraceDrawerOpen.value = true;
  loadError.value = "";
  try {
    selectedFailedTrace.value = await adminApi.getTrace(traceId);
  } catch (error) {
    selectedFailedTrace.value = null;
    loadError.value = errorMessage(error);
  } finally {
    loadingFailedDetail.value = false;
  }
}

const failedTraceError = computed(() => {
  const error = selectedFailedTrace.value?.error as
    | { code?: string; message?: string; detail?: string; stage?: string; retryable?: boolean }
    | undefined;
  return error;
});

type SupportFailureTrace = {
  support_version?: number;
  focus_document_id?: string;
  validation_result?: { valid?: boolean; code?: string };
  failure?: { code?: string; details?: Record<string, unknown> };
  workflow_before?: unknown;
  workflow_after?: unknown;
};

const failedSupportTrace = computed(() => {
  const result = selectedFailedTrace.value?.retrieval_result as SupportFailureTrace | undefined;
  return result?.support_version === 1 ? result : null;
});

/* ---------------- failed tasks ---------------- */

const failedTasks = ref<TaskRecord[]>([]);
const loadingFailedTasks = ref(false);
const selectedTask = ref<TaskRecord | null>(null);
const taskDrawerOpen = ref(false);

const failedTaskRequest = createLatestRequest();
const failedTaskPage = ref(1);
const failedTaskPageSize = ref(50);
const failedTaskTotal = ref(0);

/** Reload failed tasks from the first page. */
async function loadFailedTasks(): Promise<void> {
  failedTaskPage.value = 1;
  await loadFailedTaskPage();
}

async function loadFailedTaskPage(): Promise<void> {
  const request = failedTaskRequest.begin();
  loadingFailedTasks.value = true;
  loadError.value = "";
  try {
    const result = await adminApi.listTasksPage(
      {
        status: "FAILED",
        knowledgeId: store.selectedKnowledgeId || undefined,
        limit: failedTaskPageSize.value,
        offset: (failedTaskPage.value - 1) * failedTaskPageSize.value,
      },
      { signal: request.signal },
    );
    if (!request.isCurrent()) return;
    const target = clampedPage(failedTaskPage.value, failedTaskPageSize.value, result.items.length, result.total);
    if (target !== null) {
      failedTaskPage.value = target;
      void loadFailedTaskPage();
      return;
    }
    failedTasks.value = result.items;
    failedTaskTotal.value = result.total;
  } catch (error) {
    if (!request.isCurrent()) return;
    failedTasks.value = [];
    failedTaskTotal.value = 0;
    loadError.value = errorMessage(error);
  } finally {
    if (request.isCurrent()) loadingFailedTasks.value = false;
  }
}

/* ---------------- tabs ---------------- */

const activeTab = ref(initialNavigation.tab);

// Keep the open tab (and the failed-turn scope from a Dashboard deep link) in
// the URL so refresh/share/back return to the same place.
function syncLogsUrl(tab: string): void {
  const scope = tab === "failed-turns" ? failedTraceFilter.value : null;
  replaceViewQuery("logs", {
    tab,
    engine: scope?.engine,
    source: scope?.source,
    from: scope?.from,
    to: scope?.to,
  });
}

function handleTabChange(tab: string | number): void {
  const name = String(tab);
  syncLogsUrl(name);
  if (name !== "failed-turns") invalidateFailedTraces();
  if (name === "request-errors" && !requestErrors.value.length) void loadRequestErrors();
  if (name === "failed-turns" && !failedTraces.value.length && !loadingFailedTraces.value) void loadFailedTraces();
  if (name === "task-failures" && !failedTasks.value.length) void loadFailedTasks();
}

onMounted(() => {
  void loadSummary();
  handleTabChange(activeTab.value);
});
onUnmounted(() => {
  invalidateFailedTraces();
  failedTaskRequest.cancel();
});

watch(
  () => props.navigationQuery,
  (query) => {
    const next = navigationState(query);
    const current = failedTraceFilter.value;
    // Our own URL write-back (syncLogsUrl) comes back here unchanged.
    if (
      next.tab === activeTab.value
      && (next.tab !== "failed-turns" || (
        next.engine === current.engine && next.source === current.source
        && next.from === current.from && next.to === current.to
      ))
    ) return;
    invalidateFailedTraces();
    activeTab.value = next.tab;
    failedTraceFilter.value = { engine: next.engine, errorCode: "", source: next.source, from: next.from, to: next.to };
    if (next.tab === "failed-turns") void loadFailedTraces();
  },
);

watch(
  () => store.selectedKnowledgeId,
  () => {
    invalidateFailedTraces();
    void loadSummary();
    if (activeTab.value === "failed-turns") void loadFailedTraces();
    if (activeTab.value === "task-failures") void loadFailedTasks();
  },
);
</script>

<template>
  <div class="logs-view">
    <el-alert
      v-if="loadError"
      :title="loadError"
      type="error"
      show-icon
      @close="loadError = ''"
    />
    <!-- Overview: failure distribution for the review window -->
    <section v-loading="loadingSummary" class="panel">
      <header class="panel-header">
        <div>
          <h3>失败概览</h3>
          <span class="sub">
            最近 7 天 · 失败轮次 {{ totalTraceFailures }}（{{ summary?.scope.trace_failures === "KNOWLEDGE_BASE" ? "当前知识库" : "全局" }}）
            · 请求错误 {{ totalRequestErrors }}（全局）
            · 独立汇总，不随下方列表筛选变化
          </span>
        </div>
        <div class="scope-actions">
          <el-select
            v-model="store.selectedKnowledgeId"
            placeholder="全部知识库"
            clearable
            @change="store.selectKnowledge()"
          >
            <el-option
              v-for="kb in store.knowledgeBases"
              :key="kb.id"
              :label="kb.name"
              :value="kb.id"
            />
          </el-select>
          <el-button :icon="Refresh" :loading="loadingSummary" @click="loadSummary">刷新</el-button>
        </div>
      </header>
      <div class="panel-body overview-body">
        <div class="metric-row">
          <MetricCard
            label="失败轮次"
            :value="totalTraceFailures"
            :context="topErrorCodes || '无失败记录'"
            :icon="WarningFilled"
          />
          <MetricCard
            label="请求错误"
            :value="totalRequestErrors"
            :context="`${summary?.request_errors_by_day.length ?? 0} 天有错误记录`"
            :icon="WarningFilled"
          />
        </div>
        <div class="distribution-row">
          <el-table
            v-if="summary?.trace_failures.length"
            :data="summary.trace_failures"
            size="small"
            class="distribution-table"
          >
            <el-table-column prop="error_code" label="失败轮次 · 错误码" min-width="180" />
            <el-table-column prop="count" label="次数" width="80" />
            <el-table-column label="最近发生" width="160">
              <template #default="scope">{{ store.formatDate(scope.row.last_seen) }}</template>
            </el-table-column>
          </el-table>
          <el-table
            v-if="summary?.request_errors.length"
            :data="summary.request_errors"
            size="small"
            class="distribution-table"
          >
            <el-table-column
              prop="path"
              label="请求错误 · 端点"
              min-width="220"
              show-overflow-tooltip
            />
            <el-table-column prop="status_code" label="状态码" width="80" />
            <el-table-column prop="count" label="次数" width="80" />
            <el-table-column label="最近发生" width="160">
              <template #default="scope">{{ store.formatDate(scope.row.last_seen) }}</template>
            </el-table-column>
          </el-table>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-body tabs-body">
        <el-tabs v-model="activeTab" @tab-change="handleTabChange">
          <el-tab-pane label="Assistant 回答优化" name="assistant-answers" lazy>
            <AnswerReviewsView v-if="activeTab === 'assistant-answers' && store.selectedKnowledgeId" :key="store.selectedKnowledgeId" :knowledge-id="store.selectedKnowledgeId" />
            <p v-else>请先选择要检查的知识库。</p>
          </el-tab-pane>
          <!-- 1. Request errors -->
          <el-tab-pane label="请求错误" name="request-errors">
            <el-alert
              title="请求错误日志为全局口径，不随当前知识库筛选；请使用请求 ID（Request ID）或路径定位。"
              type="info"
              :closable="false"
              show-icon
            />
            <div class="tab-toolbar">
              <el-input
                v-model="requestErrorFilter.requestId"
                placeholder="请求 ID（request_id）精确查询"
                clearable
                class="tool-input"
                @keyup.enter="requestErrorPage = 1; loadRequestErrors()"
                @clear="requestErrorPage = 1; loadRequestErrors()"
              />
              <el-input
                v-model="requestErrorFilter.path"
                placeholder="路径包含..."
                clearable
                class="tool-input"
                @keyup.enter="requestErrorPage = 1; loadRequestErrors()"
                @clear="requestErrorPage = 1; loadRequestErrors()"
              />
              <el-select
                v-model="requestErrorFilter.statusCode"
                placeholder="状态码"
                clearable
                class="tool-select"
                @change="requestErrorPage = 1; loadRequestErrors()"
              >
                <el-option label="400" value="400" />
                <el-option label="401" value="401" />
                <el-option label="403" value="403" />
                <el-option label="404" value="404" />
                <el-option label="405" value="405" />
                <el-option label="409" value="409" />
                <el-option label="422" value="422" />
                <el-option label="500" value="500" />
                <el-option label="502" value="502" />
              </el-select>
              <el-button
                :icon="Search"
                :loading="loadingRequestErrors"
                @click="requestErrorPage = 1; loadRequestErrors()"
              >
                查询
              </el-button>
              <el-button text @click="resetRequestErrorFilter">重置</el-button>
            </div>
            <el-table
              v-loading="loadingRequestErrors"
              :data="requestErrors"
              size="small"
              class="clickable-table"
              @row-click="openRequestError"
            >
              <el-table-column label="时间" width="160">
                <template #default="scope">{{ store.formatDate(scope.row.created_at) }}</template>
              </el-table-column>
              <el-table-column label="方法" width="70">
                <template #default="scope">
                  <span class="mono">{{ scope.row.method }}</span>
                </template>
              </el-table-column>
              <el-table-column
                prop="path"
                label="路径"
                min-width="200"
                show-overflow-tooltip
              />
              <el-table-column label="状态" width="70">
                <template #default="scope">
                  <StatusBadge :status="String(scope.row.status_code)" />
                </template>
              </el-table-column>
              <el-table-column
                prop="error_code"
                label="错误码"
                min-width="160"
                show-overflow-tooltip
              />
              <el-table-column
                prop="message"
                label="信息"
                min-width="200"
                show-overflow-tooltip
              />
              <el-table-column label="请求 ID（Request ID）" width="200">
                <template #default="scope">
                  <span class="mono muted">{{ scope.row.request_id }}</span>
                </template>
              </el-table-column>
            </el-table>
            <div v-if="requestErrorTotal > requestErrorPageSize" class="pager-row">
              <el-pagination
                v-model:current-page="requestErrorPage"
                :page-size="requestErrorPageSize"
                :total="requestErrorTotal"
                layout="total, prev, pager, next"
                @current-change="loadRequestErrors"
              />
            </div>
            <EmptyState
              v-if="!loadingRequestErrors && !loadError && !requestErrors.length"
              title="没有请求错误"
              description="指定条件与时间范围内没有失败的 API 请求。"
            />
          </el-tab-pane>

          <!-- 2. Failed support turns -->
          <el-tab-pane label="失败轮次" name="failed-turns">
            <p v-if="failedTraceScope" class="muted">列表范围：{{ failedTraceScope }} · 共 {{ failedTraceTotal }} 条</p>
            <div class="tab-toolbar">
              <el-select
                v-model="failedTraceFilter.engine"
                placeholder="全部引擎"
                clearable
                class="tool-select-wide"
                @change="loadFailedTraces"
              >
                <el-option label="Profile 支持（Profile Support）" value="SUPPORT" />
                <el-option label="通用 RAG（Generic RAG）" value="RAG" />
              </el-select>
              <el-select
                v-model="failedTraceFilter.errorCode"
                placeholder="错误码"
                clearable
                class="tool-select-wide"
                @change="loadFailedTraces"
              >
                <el-option
                  v-for="code in failedTraceCodes"
                  :key="code"
                  :label="code"
                  :value="code"
                />
              </el-select>
              <el-button :icon="Refresh" :loading="loadingFailedTraces" @click="loadFailedTraces">
                刷新
              </el-button>
            </div>
            <el-table
              v-loading="loadingFailedTraces"
              :data="failedTraces"
              size="small"
              class="clickable-table"
              @row-click="(row: TraceSummary) => openFailedTrace(row.trace_id)"
            >
              <el-table-column label="时间" width="160">
                <template #default="scope">{{ store.formatDate(scope.row.created_at) }}</template>
              </el-table-column>
              <el-table-column label="引擎" width="100">
                <template #default="scope"><StatusBadge :status="scope.row.engine ?? 'RAG'" /></template>
              </el-table-column>
              <el-table-column
                prop="question"
                label="问题"
                min-width="220"
                show-overflow-tooltip
              />
              <el-table-column label="错误码" min-width="170">
                <template #default="scope">
                  <span class="error-code">{{ scope.row.error?.code ?? "UNKNOWN" }}</span>
                </template>
              </el-table-column>
              <el-table-column label="用户" width="120" show-overflow-tooltip>
                <template #default="scope">{{ scope.row.user_id ?? "—" }}</template>
              </el-table-column>
              <el-table-column label="延迟" width="90">
                <template #default="scope">
                  <span class="mono muted">{{ scope.row.latency?.total_ms ?? "—" }} ms</span>
                </template>
              </el-table-column>
            </el-table>
            <ListPagination
              v-model:page="failedTracePage"
              v-model:page-size="failedTracePageSize"
              :total="failedTraceTotal"
              @page-change="loadFailedTracePage"
              @size-change="loadFailedTraces"
            />
            <EmptyState
              v-if="!loadingFailedTraces && !loadError && !failedTraces.length"
              title="没有失败轮次"
              description="指定条件下没有失败的 Support 或 RAG 轮次。"
            />
          </el-tab-pane>

          <!-- 3. Failed tasks -->
          <el-tab-pane label="任务失败" name="task-failures">
            <div class="tab-toolbar">
              <span class="muted tab-hint">
                索引构建、GC 与恢复任务的失败记录（跨全部索引）· 共 {{ failedTaskTotal }} 条
              </span>
              <el-button :icon="Refresh" :loading="loadingFailedTasks" @click="loadFailedTasks">
                刷新
              </el-button>
            </div>
            <el-table
              v-loading="loadingFailedTasks"
              :data="failedTasks"
              size="small"
              class="clickable-table"
              @row-click="(row: TaskRecord) => { selectedTask = row; taskDrawerOpen = true; }"
            >
              <el-table-column label="时间" width="160">
                <template #default="scope">{{ store.formatDate(scope.row.created_at) }}</template>
              </el-table-column>
              <el-table-column label="阶段" width="140">
                <template #default="scope">{{ scope.row.stage ?? "—" }}</template>
              </el-table-column>
              <el-table-column label="错误码" min-width="150">
                <template #default="scope">
                  <span class="error-code">{{ scope.row.error_code ?? "—" }}</span>
                </template>
              </el-table-column>
              <el-table-column
                prop="error_message"
                label="错误信息"
                min-width="240"
                show-overflow-tooltip
              />
              <el-table-column prop="attempt" label="尝试" width="60" />
              <el-table-column label="索引" width="200">
                <template #default="scope">
                  <span class="mono muted">{{ scope.row.index_id }}</span>
                </template>
              </el-table-column>
            </el-table>
            <ListPagination
              v-model:page="failedTaskPage"
              v-model:page-size="failedTaskPageSize"
              :total="failedTaskTotal"
              @page-change="loadFailedTaskPage"
              @size-change="loadFailedTasks"
            />
            <EmptyState
              v-if="!loadingFailedTasks && !loadError && !failedTasks.length"
              title="没有失败任务"
              description="后台任务全部执行成功。"
            />
          </el-tab-pane>

          <el-tab-pane label="全部链路追踪（Trace）" name="traces" lazy>
            <TracesView />
          </el-tab-pane>
        </el-tabs>
      </div>
    </section>

    <!-- Request error detail drawer -->
    <el-drawer v-model="requestErrorDrawerOpen" title="请求错误详情" size="600px">
      <div v-loading="loadingRequestErrorDetail" class="drawer-loading-body">
        <template v-if="selectedRequestError">
          <el-descriptions
            :column="1"
            border
            size="small"
            class="drawer-descriptions"
          >
            <el-descriptions-item label="请求 ID（Request ID）">
              <span class="mono">{{ selectedRequestError.request_id }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="时间">
              {{ store.formatDate(selectedRequestError.created_at) }}
            </el-descriptions-item>
            <el-descriptions-item label="请求">
              <span class="mono">{{ selectedRequestError.method }} {{ selectedRequestError.path }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="状态码">
              {{ selectedRequestError.status_code }}
            </el-descriptions-item>
            <el-descriptions-item label="错误码">
              {{ selectedRequestError.error_code }}
            </el-descriptions-item>
            <el-descriptions-item label="信息">
              {{ selectedRequestError.message }}
            </el-descriptions-item>
          </el-descriptions>
          <template v-if="requestErrorDetailText">
            <h4 class="section-label">详情（含异常堆栈 / 参数校验字段）</h4>
            <CodeBlock :code="requestErrorDetailText" max-height="420px" />
          </template>
        </template>
      </div>
    </el-drawer>

    <!-- Failed turn detail drawer -->
    <el-drawer
      v-model="failedTraceDrawerOpen"
      title="失败轮次详情"
      size="600px"
    >
      <div v-loading="loadingFailedDetail" class="drawer-loading-body">
        <template v-if="selectedFailedTrace">
          <el-alert
            :title="`错误：${failedTraceError?.code ?? 'UNKNOWN'}`"
            :description="[
              failedTraceError?.message,
              failedTraceError?.stage ? `失败阶段：${failedTraceError.stage}` : '',
              failedTraceError?.retryable != null
                ? failedTraceError.retryable ? '可重试' : '不可重试'
                : '',
              typeof failedTraceError?.detail === 'string'
                ? `上游详情：${failedTraceError.detail}`
                : '',
            ].filter(Boolean).join(' · ')"
            type="error"
            :closable="false"
            show-icon
          />
          <el-descriptions
            :column="1"
            border
            size="small"
            class="drawer-descriptions"
          >
            <el-descriptions-item label="问题">
              {{ selectedFailedTrace.question }}
            </el-descriptions-item>
            <el-descriptions-item label="请求 ID（Request ID）">
              <span class="mono">{{ selectedFailedTrace.request_id }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="时间">
              {{ store.formatDate(selectedFailedTrace.created_at) }}
            </el-descriptions-item>
            <el-descriptions-item v-if="failedSupportTrace?.focus_document_id" label="聚焦文档">
              <span class="mono">{{ failedSupportTrace.focus_document_id }}</span>
            </el-descriptions-item>
            <el-descriptions-item v-if="failedSupportTrace?.validation_result?.code" label="校验失败码">
              {{ failedSupportTrace.validation_result.code }}
            </el-descriptions-item>
          </el-descriptions>
          <div v-if="selectedFailedTrace.conversation_id" class="drawer-actions">
            <el-button
              type="primary"
              @click="store.openConversation(selectedFailedTrace.conversation_id!)"
            >
              查看会话回放
            </el-button>
          </div>
          <h4 class="section-label">失败链路追踪（Trace，含被拒草案摘要）</h4>
          <CodeBlock :code="store.formatJson(selectedFailedTrace.retrieval_result)" max-height="420px" />
          <h4 class="section-label">完整错误 JSON</h4>
          <CodeBlock :code="store.formatJson(selectedFailedTrace.error)" max-height="200px" />
        </template>
      </div>
    </el-drawer>

    <!-- Failed task detail drawer -->
    <el-drawer v-model="taskDrawerOpen" title="任务失败详情" size="560px">
      <template v-if="selectedTask">
        <el-descriptions
          :column="1"
          border
          size="small"
          class="drawer-descriptions"
        >
          <el-descriptions-item label="任务 ID">
            <span class="mono">{{ selectedTask.id }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="索引 ID">
            <span class="mono">{{ selectedTask.index_id }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="阶段">{{ selectedTask.stage ?? "—" }}</el-descriptions-item>
          <el-descriptions-item label="尝试次数">{{ selectedTask.attempt }}</el-descriptions-item>
          <el-descriptions-item label="错误码">
            <span class="error-code">{{ selectedTask.error_code ?? "—" }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="错误信息">
            {{ selectedTask.error_message ?? "—" }}
          </el-descriptions-item>
          <el-descriptions-item label="时间">
            {{ store.formatDate(selectedTask.created_at) }}
          </el-descriptions-item>
        </el-descriptions>
        <h4 class="section-label">任务元数据</h4>
        <CodeBlock :code="store.formatJson(selectedTask.metadata)" max-height="320px" />
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.logs-view {
  display: grid;
  gap: var(--space-content-gap);
  align-content: start;
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

@media (max-width: 720px) {
  .scope-actions {
    flex-wrap: wrap;
    width: 100%;
  }

  .scope-actions .el-select,
  .tab-toolbar .el-select,
  .tab-toolbar .el-input {
    width: 100%;
  }
}

.overview-body {
  display: grid;
  gap: 14px;
}

.metric-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(220px, 100%), 1fr));
  gap: 14px;
}

.distribution-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(320px, 100%), 1fr));
  gap: 14px;
}

.distribution-table {
  width: 100%;
}

.tabs-body {
  padding-top: 4px;
}

.tab-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.tool-input {
  width: 200px;
}

.tool-select {
  width: 100px;
}

.tool-select-wide {
  width: 180px;
}

.tab-hint {
  font-size: 12px;
}

.clickable-table :deep(.el-table__row) {
  cursor: pointer;
}

.error-code {
  color: var(--danger, #c45656);
  font-weight: 600;
}

.pager-row {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}

.drawer-descriptions {
  margin-bottom: 14px;
}

.drawer-loading-body {
  min-height: 180px;
}

.drawer-actions {
  margin: 4px 0 14px;
}

.section-label {
  margin: 14px 0 8px;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 600;
}
</style>

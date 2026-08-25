<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { Refresh, Search, WarningFilled } from "@element-plus/icons-vue";

import { adminApi } from "@/api/admin";
import { useAdminStore } from "@/composables/adminStore";
import type {
  FailureSummary,
  IndexTask,
  RagTrace,
  RequestErrorLog,
  TraceSummary,
} from "@/types/admin";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import EmptyState from "@/components/ui/EmptyState.vue";
import CodeBlock from "@/components/ui/CodeBlock.vue";
import MetricCard from "@/components/ui/MetricCard.vue";

const store = useAdminStore();

/* ---------------- overview ---------------- */

const summary = ref<FailureSummary | null>(null);
const loadingSummary = ref(false);

const totalTraceFailures = computed(() =>
  summary.value?.trace_failures.reduce((total, group) => total + group.count, 0) ?? 0,
);
const totalRequestErrors = computed(() =>
  summary.value?.request_errors.reduce((total, group) => total + group.count, 0) ?? 0,
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
  try {
    summary.value = await adminApi.getFailureSummary({
      knowledgeId: store.selectedKnowledgeId || undefined,
    });
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
const selectedRequestError = ref<RequestErrorLog | null>(null);
const requestErrorDrawerOpen = ref(false);

async function loadRequestErrors(): Promise<void> {
  loadingRequestErrors.value = true;
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
  try {
    selectedRequestError.value = await adminApi.getRequestError(row.id);
  } catch {
    /* keep the list-row copy; detail JSON is best-effort */
  }
}

const requestErrorDetailText = computed(() => {
  const detail = selectedRequestError.value?.detail;
  return detail && Object.keys(detail).length ? store.formatJson(detail) : "";
});

/* ---------------- failed support turns ---------------- */

const failedTraceFilter = ref<{ errorCode: string }>({ errorCode: "" });
const failedTraces = ref<TraceSummary[]>([]);
const loadingFailedTraces = ref(false);
const selectedFailedTrace = ref<RagTrace | null>(null);
const failedTraceDrawerOpen = ref(false);
const loadingFailedDetail = ref(false);

const failedTraceCodes = computed(() =>
  (summary.value?.trace_failures ?? []).map((group) => group.error_code),
);

async function loadFailedTraces(): Promise<void> {
  loadingFailedTraces.value = true;
  try {
    failedTraces.value = await adminApi.listTraces(store.selectedKnowledgeId || undefined, {
      status: "FAILED",
      errorCode: failedTraceFilter.value.errorCode || undefined,
      limit: 200,
    });
  } finally {
    loadingFailedTraces.value = false;
  }
}

async function openFailedTrace(traceId: string): Promise<void> {
  loadingFailedDetail.value = true;
  failedTraceDrawerOpen.value = true;
  try {
    selectedFailedTrace.value = await adminApi.getTrace(traceId);
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

const failedTasks = ref<IndexTask[]>([]);
const loadingFailedTasks = ref(false);
const selectedTask = ref<IndexTask | null>(null);
const taskDrawerOpen = ref(false);

async function loadFailedTasks(): Promise<void> {
  loadingFailedTasks.value = true;
  try {
    failedTasks.value = await adminApi.listTasks({
      status: "FAILED",
      knowledgeId: store.selectedKnowledgeId || undefined,
      limit: 100,
    });
  } finally {
    loadingFailedTasks.value = false;
  }
}

/* ---------------- escalations ---------------- */

const escalations = ref<TraceSummary[]>([]);
const loadingEscalations = ref(false);
const selectedEscalation = ref<RagTrace | null>(null);
const escalationDrawerOpen = ref(false);
const loadingEscalationDetail = ref(false);

async function loadEscalations(): Promise<void> {
  loadingEscalations.value = true;
  try {
    escalations.value = await adminApi.listTraces(store.selectedKnowledgeId || undefined, {
      responseType: "ESCALATION_REQUIRED",
      limit: 200,
    });
  } finally {
    loadingEscalations.value = false;
  }
}

async function openEscalation(traceId: string): Promise<void> {
  loadingEscalationDetail.value = true;
  escalationDrawerOpen.value = true;
  try {
    selectedEscalation.value = await adminApi.getTrace(traceId);
  } finally {
    loadingEscalationDetail.value = false;
  }
}

type EscalationTrace = {
  escalation?: {
    required?: boolean;
    reason?: string | null;
    summary?: {
      problem_code?: string;
      current_step?: number | null;
      completed_steps?: number[];
      observations?: Array<Record<string, unknown>>;
      checks_performed?: Array<Record<string, unknown>>;
      missing_information?: string[];
    };
  };
};

const escalationRecord = computed(() => {
  const result = selectedEscalation.value?.retrieval_result as EscalationTrace | undefined;
  return result?.escalation ?? null;
});

const escalationSummary = computed(() => escalationRecord.value?.summary ?? null);

/* ---------------- tabs ---------------- */

const activeTab = ref("request-errors");

function handleTabChange(tab: string | number): void {
  const name = String(tab);
  if (name === "request-errors" && !requestErrors.value.length) void loadRequestErrors();
  if (name === "failed-turns" && !failedTraces.value.length) void loadFailedTraces();
  if (name === "task-failures" && !failedTasks.value.length) void loadFailedTasks();
  if (name === "escalations" && !escalations.value.length) void loadEscalations();
}

onMounted(() => {
  void loadSummary();
  void loadRequestErrors();
});
</script>

<template>
  <div class="logs-view">
    <!-- Overview: failure distribution for the review window -->
    <section v-loading="loadingSummary" class="panel">
      <header class="panel-header">
        <div>
          <h3>失败概览</h3>
          <span class="sub">
            最近 7 天 · 失败轮次 {{ totalTraceFailures }} · 请求错误 {{ totalRequestErrors }}
          </span>
        </div>
        <el-button :icon="Refresh" :loading="loadingSummary" @click="loadSummary">刷新</el-button>
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
          <!-- 1. Request errors -->
          <el-tab-pane label="请求错误" name="request-errors">
            <div class="tab-toolbar">
              <el-input
                v-model="requestErrorFilter.requestId"
                placeholder="request_id 精确查询"
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
              <el-table-column label="Request ID" width="200">
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
              v-if="!loadingRequestErrors && !requestErrors.length"
              title="没有请求错误"
              description="指定条件与时间范围内没有失败的 API 请求。"
            />
          </el-tab-pane>

          <!-- 2. Failed support turns -->
          <el-tab-pane label="失败轮次" name="failed-turns">
            <div class="tab-toolbar">
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
            <EmptyState
              v-if="!loadingFailedTraces && !failedTraces.length"
              title="没有失败轮次"
              description="指定条件下没有失败的技术支持轮次。"
            />
          </el-tab-pane>

          <!-- 3. Failed tasks -->
          <el-tab-pane label="任务失败" name="task-failures">
            <div class="tab-toolbar">
              <span class="muted tab-hint">
                索引构建、GC 与恢复任务的失败记录（跨全部索引）
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
              @row-click="(row: IndexTask) => { selectedTask = row; taskDrawerOpen = true; }"
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
            <EmptyState
              v-if="!loadingFailedTasks && !failedTasks.length"
              title="没有失败任务"
              description="后台任务全部执行成功。"
            />
          </el-tab-pane>

          <!-- 4. Escalations -->
          <el-tab-pane label="升级记录" name="escalations">
            <div class="tab-toolbar">
              <span class="muted tab-hint">
                触发升级人工的轮次（只读记录，处理线下进行）
              </span>
              <el-button :icon="Refresh" :loading="loadingEscalations" @click="loadEscalations">
                刷新
              </el-button>
            </div>
            <el-table
              v-loading="loadingEscalations"
              :data="escalations"
              size="small"
              class="clickable-table"
              @row-click="(row: TraceSummary) => openEscalation(row.trace_id)"
            >
              <el-table-column label="时间" width="160">
                <template #default="scope">{{ store.formatDate(scope.row.created_at) }}</template>
              </el-table-column>
              <el-table-column
                prop="question"
                label="问题"
                min-width="240"
                show-overflow-tooltip
              />
              <el-table-column label="用户" width="120" show-overflow-tooltip>
                <template #default="scope">{{ scope.row.user_id ?? "—" }}</template>
              </el-table-column>
              <el-table-column label="知识库" width="200">
                <template #default="scope">
                  <span class="mono muted">{{ scope.row.kb_id }}</span>
                </template>
              </el-table-column>
              <el-table-column label="状态" width="110">
                <template #default="scope">
                  <StatusBadge :status="scope.row.status" />
                </template>
              </el-table-column>
            </el-table>
            <EmptyState
              v-if="!loadingEscalations && !escalations.length"
              title="没有升级记录"
              description="指定条件下没有触发升级人工的轮次。"
            />
          </el-tab-pane>
        </el-tabs>
      </div>
    </section>

    <!-- Request error detail drawer -->
    <el-drawer v-model="requestErrorDrawerOpen" title="请求错误详情" size="600px">
      <template v-if="selectedRequestError">
        <el-descriptions
          :column="1"
          border
          size="small"
          class="drawer-descriptions"
        >
          <el-descriptions-item label="Request ID">
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
    </el-drawer>

    <!-- Failed turn detail drawer -->
    <el-drawer
      v-model="failedTraceDrawerOpen"
      v-loading="loadingFailedDetail"
      title="失败轮次详情"
      size="600px"
    >
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
          <el-descriptions-item label="Request ID">
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
        <h4 class="section-label">失败 Trace（含被拒草案摘要）</h4>
        <CodeBlock :code="store.formatJson(selectedFailedTrace.retrieval_result)" max-height="420px" />
        <h4 class="section-label">完整错误 JSON</h4>
        <CodeBlock :code="store.formatJson(selectedFailedTrace.error)" max-height="200px" />
      </template>
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

    <!-- Escalation detail drawer -->
    <el-drawer
      v-model="escalationDrawerOpen"
      v-loading="loadingEscalationDetail"
      title="升级详情"
      size="600px"
    >
      <template v-if="selectedEscalation">
        <h3 class="drawer-question">{{ selectedEscalation.question }}</h3>
        <div class="drawer-meta">
          <StatusBadge :status="selectedEscalation.status" />
          <span class="muted">{{ store.formatDate(selectedEscalation.created_at) }}</span>
          <span v-if="selectedEscalation.user_id" class="muted">
            用户 {{ selectedEscalation.user_id }}
          </span>
        </div>
        <el-alert
          v-if="escalationRecord?.reason"
          :title="escalationRecord.reason"
          type="warning"
          :closable="false"
          show-icon
          class="drawer-alert"
        />
        <template v-if="escalationSummary">
          <el-descriptions
            :column="1"
            border
            size="small"
            class="drawer-descriptions"
          >
            <el-descriptions-item label="问题编码">
              {{ escalationSummary.problem_code ?? "—" }}
            </el-descriptions-item>
            <el-descriptions-item label="当前步骤">
              {{ escalationSummary.current_step ?? "—" }}
            </el-descriptions-item>
            <el-descriptions-item label="已完成步骤">
              {{ (escalationSummary.completed_steps ?? []).join("、") || "—" }}
            </el-descriptions-item>
            <el-descriptions-item label="缺失信息">
              {{ (escalationSummary.missing_information ?? []).join("；") || "—" }}
            </el-descriptions-item>
          </el-descriptions>
          <h4 class="section-label">升级摘要 JSON</h4>
          <CodeBlock :code="store.formatJson(escalationSummary)" max-height="360px" />
        </template>
        <div v-if="selectedEscalation.conversation_id" class="drawer-actions">
          <el-button
            type="primary"
            @click="store.openConversation(selectedEscalation.conversation_id!)"
          >
            查看会话回放
          </el-button>
        </div>
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

.overview-body {
  display: grid;
  gap: 14px;
}

.metric-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 14px;
}

.distribution-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
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

.drawer-alert {
  margin-bottom: 14px;
}

.drawer-actions {
  margin: 4px 0 14px;
}

.drawer-question {
  color: var(--text-primary);
  font-size: 15px;
  font-weight: 600;
  line-height: 1.4;
}

.drawer-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 6px 0 14px;
}

.section-label {
  margin: 14px 0 8px;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 600;
}
</style>

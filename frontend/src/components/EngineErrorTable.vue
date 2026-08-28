<script setup lang="ts">
import type { TraceSummary } from "@/types/admin";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import EmptyState from "@/components/ui/EmptyState.vue";

defineProps<{
  engine: "SUPPORT" | "RAG";
  traces: TraceSummary[];
  loading: boolean;
  error: string;
}>();

const emit = defineEmits<{ reload: [] }>();

function errorCode(trace: TraceSummary): string {
  const code = trace.error?.code;
  return typeof code === "string" ? code : "UNKNOWN";
}

function errorStage(trace: TraceSummary): string {
  const stage = trace.error?.stage;
  return typeof stage === "string" ? stage : trace.stage || "—";
}

function errorMessage(trace: TraceSummary): string {
  const message = trace.error?.message;
  return typeof message === "string" ? message : "—";
}

function formattedError(trace: TraceSummary): string {
  return JSON.stringify(trace.error || {}, null, 2);
}
</script>

<template>
  <div class="engine-errors">
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
      show-icon
    />
    <section class="panel">
      <header class="panel-header">
        <div>
          <h3>{{ engine }} 错误（Errors）</h3>
          <span class="sub">仅显示 {{ engine }} 引擎失败链路追踪（Trace），不混入另一条链路</span>
        </div>
        <el-button :loading="loading" @click="emit('reload')">刷新</el-button>
      </header>
      <el-table v-if="traces.length" v-loading="loading" :data="traces">
        <el-table-column type="expand">
          <template #default="scope">
            <div class="error-detail">
              <div><strong>错误详情</strong><pre>{{ formattedError(scope.row) }}</pre></div>
              <dl>
                <dt>父链路（Trace）</dt>
                <dd class="mono">{{ scope.row.parent_trace_id || "—" }}</dd>
                <dt>降级来源</dt>
                <dd>{{ scope.row.fallback_from || "—" }}</dd>
                <dt>降级原因</dt>
                <dd>{{ scope.row.fallback_reason || "—" }}</dd>
              </dl>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="时间" width="180">
          <template #default="scope">
            {{ new Date(scope.row.created_at).toLocaleString("zh-CN", { hour12: false }) }}
          </template>
        </el-table-column>
        <el-table-column
          prop="question"
          label="问题"
          min-width="240"
          show-overflow-tooltip
        />
        <el-table-column label="阶段" width="180">
          <template #default="scope">
            <span class="mono">{{ errorStage(scope.row) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="错误码" width="210">
          <template #default="scope">
            <StatusBadge :status="errorCode(scope.row)" />
          </template>
        </el-table-column>
        <el-table-column label="错误信息" min-width="240" show-overflow-tooltip>
          <template #default="scope">{{ errorMessage(scope.row) }}</template>
        </el-table-column>
        <el-table-column label="链路 ID（Trace ID）" min-width="230">
          <template #default="scope">
            <span class="mono">{{ scope.row.trace_id }}</span>
          </template>
        </el-table-column>
      </el-table>
      <EmptyState
        v-else
        :title="`暂无 ${engine} 失败链路追踪（Trace）`"
        description="当前筛选范围内没有该引擎的失败记录。"
      />
    </section>
  </div>
</template>

<style scoped>
.error-detail { display: grid; grid-template-columns: minmax(280px, 1fr) minmax(260px, 0.7fr); gap: 24px; padding: 8px 44px 18px; }
.error-detail pre { margin: 8px 0 0; padding: 12px; overflow: auto; border-radius: 8px; background: var(--bg-subtle); color: var(--text-secondary); font-size: 12px; }
.error-detail dl { display: grid; grid-template-columns: 86px 1fr; align-content: start; gap: 8px 12px; margin: 0; }
.error-detail dt { color: var(--text-tertiary); }
.error-detail dd { margin: 0; overflow-wrap: anywhere; }
@media (max-width: 760px) { .error-detail { grid-template-columns: 1fr; padding-inline: 16px; } }
</style>

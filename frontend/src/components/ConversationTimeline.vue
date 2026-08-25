<script setup lang="ts">
import { computed, ref, watch } from "vue";

import { adminApi } from "@/api/admin";
import { useAdminStore } from "@/composables/adminStore";
import { renderMarkdown } from "@/utils/markdown";
import type { RagTrace, SupportMessage } from "@/types/admin";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import CodeBlock from "@/components/ui/CodeBlock.vue";
import EmptyState from "@/components/ui/EmptyState.vue";

/*
 * Conversation replay: full multi-turn review of one conversation.
 * Messages provide content / response_type / support_mode; the matching
 * traces provide workflow before/after snapshots and escalation details.
 */
const props = defineProps<{ conversationId: string }>();

const store = useAdminStore();

const messages = ref<SupportMessage[]>([]);
const traces = ref<RagTrace[]>([]);
const loading = ref(false);
const loadError = ref("");

// Traces carry the heavy workflow JSON; cap detail fetches so a runaway
// conversation cannot turn the replay drawer into a request storm.
const MAX_TRACE_DETAILS = 20;

async function load(conversationId: string): Promise<void> {
  if (!conversationId) return;
  loading.value = true;
  loadError.value = "";
  messages.value = [];
  traces.value = [];
  try {
    const [messageList, traceSummaries] = await Promise.all([
      adminApi.listConversationMessages(conversationId),
      adminApi.listTraces(undefined, { conversationId, limit: 50 }),
    ]);
    messages.value = messageList;
    traces.value = await Promise.all(
      traceSummaries.slice(0, MAX_TRACE_DETAILS).map((summary) => adminApi.getTrace(summary.trace_id)),
    );
  } catch (cause) {
    loadError.value = cause instanceof Error ? cause.message : "会话加载失败";
  } finally {
    loading.value = false;
  }
}

watch(
  () => props.conversationId,
  (value) => void load(value),
  { immediate: true },
);

interface Turn {
  user: SupportMessage | null;
  assistant: SupportMessage | null;
  trace: RagTrace | null;
}

const turns = computed<Turn[]>(() => {
  const sorted = [...traces.value].sort((a, b) => a.created_at.localeCompare(b.created_at));
  const used = new Set<string>();
  const pickTrace = (question: string): RagTrace | null => {
    const exact = sorted.find((item) => !used.has(item.trace_id) && item.question === question);
    const match = exact ?? sorted.find((item) => !used.has(item.trace_id));
    if (!match) return null;
    used.add(match.trace_id);
    return match;
  };
  const result: Turn[] = [];
  let pendingUser: SupportMessage | null = null;
  for (const message of messages.value) {
    if (message.role === "USER") {
      if (pendingUser) result.push({ user: pendingUser, assistant: null, trace: null });
      pendingUser = message;
    } else {
      result.push({
        user: pendingUser,
        assistant: message,
        trace: pendingUser ? pickTrace(pendingUser.content) : null,
      });
      pendingUser = null;
    }
  }
  if (pendingUser) result.push({ user: pendingUser, assistant: null, trace: null });
  return result;
});

type SupportTrace = {
  focus_document_id?: string;
  support_mode?: string;
  workflow_before?: Record<string, unknown>;
  workflow_after?: {
    current_step?: number | null;
    completed_steps?: number[];
    observations?: Array<Record<string, unknown>>;
    pending_options?: Array<Record<string, unknown>>;
    missing_information?: string[];
    escalation?: { required?: boolean; reason?: string | null };
  };
  escalation?: { required?: boolean; reason?: string | null };
  failure?: { code?: string };
};

function supportTraceOf(trace: RagTrace | null): SupportTrace | null {
  if (!trace) return null;
  return (trace.retrieval_result as SupportTrace | undefined) ?? null;
}

function workflowSummary(trace: RagTrace | null): string {
  const workflow = supportTraceOf(trace)?.workflow_after;
  if (!workflow) return "";
  const parts: string[] = [];
  if (workflow.current_step != null) parts.push(`当前步骤 ${workflow.current_step}`);
  if (workflow.completed_steps?.length) parts.push(`已完成 ${workflow.completed_steps.length} 步`);
  if (workflow.observations?.length) parts.push(`观察 ${workflow.observations.length} 条`);
  if (workflow.pending_options?.length) parts.push(`待确认选项 ${workflow.pending_options.length} 个`);
  if (workflow.missing_information?.length)
    parts.push(`缺少信息 ${workflow.missing_information.length} 项`);
  if (workflow.escalation?.required) parts.push("已升级人工");
  return parts.join(" · ");
}

function escalationOf(trace: RagTrace | null): SupportTrace["escalation"] | null {
  const support = supportTraceOf(trace);
  if (!support?.escalation?.required) return null;
  return support.escalation;
}

function traceError(trace: RagTrace | null): { code?: string; message?: string } | null {
  const error = trace?.error as { code?: string; message?: string } | undefined;
  return error?.code ? error : null;
}
</script>

<template>
  <div v-loading="loading" class="conversation-timeline">
    <el-alert
      v-if="loadError"
      :title="loadError"
      type="error"
      :closable="false"
      show-icon
    />
    <EmptyState
      v-else-if="!loading && !turns.length"
      title="暂无会话内容"
      description="该会话没有任何消息记录。"
    />
    <template v-else>
      <header class="timeline-head">
        <span class="mono muted">{{ conversationId }}</span>
        <span class="muted">{{ turns.length }} 轮对话</span>
      </header>
      <div
        v-for="(turn, index) in turns"
        :key="turn.assistant?.id ?? turn.user?.id ?? index"
        class="turn"
      >
        <div v-if="turn.user" class="bubble user">
          <div class="bubble-role">用户</div>
          <div class="bubble-content">{{ turn.user.content }}</div>
          <div class="bubble-time muted">{{ store.formatDate(turn.user.created_at) }}</div>
        </div>
        <div v-if="turn.assistant" class="bubble assistant">
          <div class="bubble-role">
            助手
            <div class="bubble-badges">
              <StatusBadge v-if="turn.assistant.response_type" :status="turn.assistant.response_type" />
              <StatusBadge :status="turn.assistant.status" />
              <span v-if="turn.assistant.support_mode" class="muted">
                {{ turn.assistant.support_mode }}
              </span>
            </div>
          </div>
          <!-- eslint-disable vue/no-v-html -- DOMPurify 消毒后的 Markdown 输出 -->
          <div
            class="bubble-content markdown"
            v-html="renderMarkdown(turn.assistant.content || '（无内容）')"
          ></div>
          <!-- eslint-enable vue/no-v-html -->
          <el-alert
            v-if="traceError(turn.trace)"
            :title="`本轮失败：${traceError(turn.trace)?.code}`"
            :description="traceError(turn.trace)?.message"
            type="error"
            :closable="false"
            show-icon
            class="turn-alert"
          />
          <el-alert
            v-if="escalationOf(turn.trace)"
            title="本轮触发升级人工"
            :description="escalationOf(turn.trace)?.reason ?? ''"
            type="warning"
            :closable="false"
            show-icon
            class="turn-alert"
          />
          <div v-if="workflowSummary(turn.trace)" class="workflow-line muted">
            {{ workflowSummary(turn.trace) }}
          </div>
          <div v-if="turn.trace" class="turn-actions">
            <el-button
              text
              size="small"
              @click="store.openTrace(turn.trace.trace_id)"
            >
              查看完整 Trace
            </el-button>
          </div>
          <el-collapse v-if="turn.trace" class="turn-collapse">
            <el-collapse-item title="工作流状态与升级详情" name="workflow">
              <CodeBlock
                :code="store.formatJson({
                  focus_document_id: supportTraceOf(turn.trace)?.focus_document_id,
                  workflow_before: supportTraceOf(turn.trace)?.workflow_before,
                  workflow_after: supportTraceOf(turn.trace)?.workflow_after,
                  escalation: supportTraceOf(turn.trace)?.escalation,
                  failure: supportTraceOf(turn.trace)?.failure,
                })"
                max-height="260px"
              />
            </el-collapse-item>
          </el-collapse>
          <div class="bubble-time muted">{{ store.formatDate(turn.assistant.created_at) }}</div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.conversation-timeline {
  display: grid;
  gap: 14px;
  min-height: 120px;
}

.timeline-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
}

.turn {
  display: grid;
  gap: 8px;
}

.bubble {
  padding: 10px 14px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
}

.bubble.user {
  justify-self: end;
  max-width: 86%;
  background: var(--brand-subtle);
}

.bubble.assistant {
  background: var(--bg-surface);
}

.bubble-role {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
  color: var(--text-tertiary);
  font-size: 11px;
}

.bubble-badges {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.bubble-content {
  color: var(--text-primary);
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.markdown {
  white-space: normal;
}

.bubble-time {
  margin-top: 6px;
  font-size: 11px;
  text-align: right;
}

.turn-alert {
  margin-top: 8px;
}

.workflow-line {
  margin-top: 8px;
  padding: 6px 10px;
  border-radius: var(--radius-sm);
  background: var(--bg-subtle);
  font-size: 12px;
}

.turn-actions {
  margin-top: 6px;
}

.turn-collapse {
  margin-top: 6px;
  border-top: 1px solid var(--border-subtle);
}
</style>

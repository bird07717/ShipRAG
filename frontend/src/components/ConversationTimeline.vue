<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";

import { adminApi } from "@/api/admin";
import { useAdminStore } from "@/composables/adminStore";
import { renderMarkdown } from "@/utils/markdown";
import type { RagTrace, SupportMessage, TraceSummary } from "@/types/admin";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import EmptyState from "@/components/ui/EmptyState.vue";

/*
 * Conversation replay: full multi-turn review of one conversation.
 * Messages provide the user-visible conversation; traces link to technical
 * detail when needed.
 */
const props = defineProps<{ conversationId: string }>();

const store = useAdminStore();

const messages = ref<SupportMessage[]>([]);
const traces = ref<RagTrace[]>([]);
const loading = ref(false);
const loadError = ref("");
const traceDetailWarning = ref("");

// Traces carry the heavy workflow JSON; cap detail fetches so a runaway
// conversation cannot turn the replay drawer into a request storm.
const MAX_TRACE_DETAILS = 20;
const TRACE_DETAILS_CONCURRENCY = 4;
const REPLAY_TIMEOUT_MS = 20_000;

let activeController: AbortController | null = null;
let activeLoad = 0;

function abortError(): Error {
  const error = new Error("会话回放请求已取消");
  error.name = "AbortError";
  return error;
}

async function loadTraceDetails(
  summaries: TraceSummary[],
  signal: AbortSignal,
): Promise<PromiseSettledResult<RagTrace>[]> {
  const targets = summaries.slice(0, MAX_TRACE_DETAILS);
  const results: PromiseSettledResult<RagTrace>[] = [];
  let nextIndex = 0;

  const worker = async (): Promise<void> => {
    while (nextIndex < targets.length) {
      if (signal.aborted) throw abortError();
      const index = nextIndex;
      nextIndex += 1;
      const target = targets[index];
      if (!target) continue;
      try {
        results[index] = {
          status: "fulfilled",
          value: await adminApi.getTrace(target.trace_id, { signal }),
        };
      } catch (reason) {
        results[index] = { status: "rejected", reason };
      }
    }
  };

  await Promise.all(
    Array.from({ length: Math.min(TRACE_DETAILS_CONCURRENCY, targets.length) }, () => worker()),
  );
  if (signal.aborted) throw abortError();
  return results;
}

async function load(conversationId: string): Promise<void> {
  if (!conversationId) return;
  const loadId = ++activeLoad;
  activeController?.abort();
  const controller = new AbortController();
  activeController = controller;
  let timedOut = false;
  const timeoutId = window.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, REPLAY_TIMEOUT_MS);

  loading.value = true;
  loadError.value = "";
  traceDetailWarning.value = "";
  messages.value = [];
  traces.value = [];
  try {
    const [messageList, traceSummaries] = await Promise.all([
      adminApi.listConversationMessages(conversationId, { signal: controller.signal }),
      adminApi.listTraces(undefined, { conversationId, limit: 50 }, { signal: controller.signal }),
    ]);
    if (!Array.isArray(messageList.items) || !Array.isArray(traceSummaries)) {
      throw new Error("会话回放接口返回的数据格式不正确");
    }
    if (loadId !== activeLoad || controller.signal.aborted) return;

    messages.value = messageList.items;
    const detailResults = await loadTraceDetails(traceSummaries, controller.signal);
    if (loadId !== activeLoad || controller.signal.aborted) return;

    traces.value = detailResults
      .filter((result): result is PromiseFulfilledResult<RagTrace> => result.status === "fulfilled")
      .map((result) => result.value);
    const failedCount = detailResults.filter((result) => result.status === "rejected").length;
    if (failedCount) {
      traceDetailWarning.value = `${failedCount} 条 Trace 详情加载失败，已展示其余会话内容。`;
    }
  } catch (cause) {
    if (loadId !== activeLoad || (controller.signal.aborted && !timedOut)) return;
    loadError.value = timedOut
      ? "会话回放请求超时，请重试。"
      : cause instanceof Error
        ? cause.message
        : "会话加载失败";
  } finally {
    window.clearTimeout(timeoutId);
    if (loadId === activeLoad) {
      loading.value = false;
      activeController = null;
    }
  }
}

watch(
  () => props.conversationId,
  (value) => void load(value),
  { immediate: true },
);

onBeforeUnmount(() => {
  activeLoad += 1;
  activeController?.abort();
  activeController = null;
});

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
      <el-alert
        v-if="traceDetailWarning"
        :title="traceDetailWarning"
        type="warning"
        :closable="false"
        show-icon
      />
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
          <div v-if="turn.trace" class="turn-actions">
            <el-button
              text
              size="small"
              @click="store.openTrace(turn.trace.trace_id)"
            >
              查看完整 Trace
            </el-button>
          </div>
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

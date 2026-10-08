<script setup lang="ts">
import { ElMessage, ElNotification } from "element-plus";
import { computed, onMounted, onUnmounted, watch, type Component } from "vue";

import { useAdminStore } from "@/composables/adminStore";
import { renderMarkdown } from "@/utils/markdown";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import CodeBlock from "@/components/ui/CodeBlock.vue";
import EmptyState from "@/components/ui/EmptyState.vue";
import DashboardView from "@/views/DashboardView.vue";
import KnowledgeView from "@/views/KnowledgeView.vue";
import DocumentsView from "@/views/DocumentsView.vue";
import IndexBuildView from "@/views/IndexBuildView.vue";
import LogsView from "@/views/LogsView.vue";
import PlaygroundView from "@/views/PlaygroundView.vue";
import ConfigurationView from "@/views/ConfigurationView.vue";
import EngineRoutingView from "@/views/EngineRoutingView.vue";
import SupportReviewView from "@/views/SupportReviewView.vue";
import ConversationsView from "@/views/ConversationsView.vue";
import ConversationTimeline from "@/components/ConversationTimeline.vue";

const props = defineProps<{ view: string; navigationQuery?: string }>();

const store = useAdminStore();

const views: Record<string, Component> = {
  dashboard: DashboardView,
  knowledge: KnowledgeView,
  documents: DocumentsView,
  indexes: IndexBuildView,
  logs: LogsView,
  playground: PlaygroundView,
  configuration: ConfigurationView,
  "engine-routing": EngineRoutingView,
  "support-review": SupportReviewView,
  conversations: ConversationsView,
};

const viewProps = computed(() =>
  ["logs", "configuration", "knowledge", "conversations"].includes(props.view)
    ? { navigationQuery: props.navigationQuery ?? "" }
    : {},
);

let indexPollTimer: ReturnType<typeof setInterval> | undefined;
let taskPollTimer: ReturnType<typeof setInterval> | undefined;

function stopAutomaticRequests(): void {
  clearInterval(indexPollTimer);
  clearInterval(taskPollTimer);
  indexPollTimer = undefined;
  taskPollTimer = undefined;
}

function startAutomaticRequests(): void {
  if (!store.hasServiceCredential || indexPollTimer || taskPollTimer) return;
  store.restoreTrackedTasks();
  indexPollTimer = setInterval(() => {
    if (store.hasServiceCredential && store.indexes.some((index) => index.status === "BUILDING")) {
      void store.refreshOperationalState();
    }
  }, 5_000);
  taskPollTimer = setInterval(() => {
    if (store.hasServiceCredential) void store.pollTrackedTasks();
  }, 3_000);
  void store.pollTrackedTasks();
}

// Notices are graded by store.noticeLevel (see ADR-0032): a plain success is a
// short toast; a caveat or warning is a notification that stays until closed,
// because it carries effect boundaries or a result that needs attention. Only
// one sticky notice is kept: a newer notice replaces it.
const SUCCESS_TOAST_MAX_MS = 6_000;
let stickyNotice: { close: () => void } | undefined;

function clearNoticeIfCurrent(message: string): void {
  // Let an identical follow-up notice (e.g. saving twice) be shown again.
  if (store.notice === message) store.notice = "";
}

watch(
  () => store.notice,
  (message) => {
    if (!message) return;
    const level = store.noticeLevel;
    store.noticeLevel = "success";
    stickyNotice?.close();
    stickyNotice = undefined;
    if (level === "success") {
      ElMessage({
        type: "success",
        message,
        showClose: true,
        grouping: true,
        duration: Math.min(3_000 + message.length * 60, SUCCESS_TOAST_MAX_MS),
        onClose: () => clearNoticeIfCurrent(message),
      });
      return;
    }
    stickyNotice = ElNotification({
      type: level === "warning" ? "warning" : "success",
      title: level === "warning" ? "需要注意" : "已完成，请留意生效范围",
      message,
      duration: 0,
      onClose: () => clearNoticeIfCurrent(message),
    });
  },
);

watch(
  () => store.hasServiceCredential,
  (available) => {
    if (available) startAutomaticRequests();
    else stopAutomaticRequests();
  },
);

onMounted(() => {
  if (store.hasServiceCredential) void store.run(() => store.refreshAll());
  startAutomaticRequests();
});

onUnmounted(() => {
  stopAutomaticRequests();
  stickyNotice?.close();
});
</script>

<template>
  <section class="studio-content">
    <el-alert
      v-if="store.error"
      :title="store.error"
      type="error"
      show-icon
      class="studio-alert"
      @close="store.error = ''"
    />
    <EmptyState
      v-if="!store.hasServiceCredential && props.view !== 'configuration'"
      title="需要配置服务凭证"
      description="管理台已暂停自动加载和轮询，配置有效的 Service Token 后再访问管理功能。"
    >
      <template #action>
        <el-button type="primary" @click="store.navigate('configuration', 'tab=credentials')">
          配置服务凭证
        </el-button>
      </template>
    </EmptyState>
    <component :is="views[props.view] ?? DashboardView" v-else v-bind="viewProps" />

    <!-- Shared trace detail drawer (opened from Dashboard recent activity) -->
    <el-drawer
      v-model="store.traceDrawerOpen"
      title="RAG 链路追踪（Trace）"
      size="560px"
    >
      <template v-if="store.selectedTrace">
        <div class="trace-drawer-head">
          <h3>{{ store.selectedTrace.question }}</h3>
          <div class="trace-drawer-meta">
            <StatusBadge :status="store.selectedTrace.status" />
            <span class="muted">{{ store.selectedTrace.mode }}</span>
            <span class="mono muted">{{ store.selectedTrace.latency.total_ms ?? "—" }} ms</span>
          </div>
        </div>
        <h4 class="section-label">回答（Answer）</h4>
        <!-- eslint-disable-next-line vue/no-v-html -- DOMPurify 消毒后的 Markdown 输出 -->
        <div class="trace-answer" v-html="renderMarkdown(store.selectedTrace.answer || '')"></div>
        <h4 class="section-label">原始 JSON（Raw JSON）</h4>
        <CodeBlock :code="store.formatJson(store.selectedTrace)" max-height="360px" />
      </template>
    </el-drawer>

    <!-- Shared conversation replay drawer (opened from Trace / Logs views) -->
    <el-drawer v-model="store.conversationDrawerOpen" title="会话回放" size="640px">
      <ConversationTimeline
        v-if="store.conversationDrawerOpen && store.conversationId"
        :conversation-id="store.conversationId"
      />
    </el-drawer>
  </section>
</template>

<style scoped>
.studio-content {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: var(--space-content-gap);
  align-content: start;
  min-width: 0;
}

.studio-content > * {
  min-width: 0;
}

.studio-alert + .studio-alert {
  margin-top: -4px;
}

.trace-drawer-head h3 {
  color: var(--text-primary);
  font-size: 15px;
  font-weight: 600;
  line-height: 1.4;
}

.trace-drawer-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 6px;
}

.trace-answer {
  color: var(--text-primary);
  font-size: 14px;
  line-height: 1.8;
}

.trace-answer :deep(p) {
  margin: 6px 0;
}

.trace-answer :deep(ol),
.trace-answer :deep(ul) {
  margin: 6px 0;
  padding-left: 22px;
}

.trace-answer :deep(code) {
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--bg-subtle);
  font-family: "JetBrains Mono Variable", monospace;
  font-size: 0.92em;
}

.section-label {
  margin: 16px 0 8px;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 600;
}
</style>

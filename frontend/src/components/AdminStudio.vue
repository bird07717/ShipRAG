<script setup lang="ts">
import { onMounted, onUnmounted } from "vue";

import { useAdminStore } from "@/composables/adminStore";
import { renderMarkdown } from "@/utils/markdown";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import CodeBlock from "@/components/ui/CodeBlock.vue";
import DashboardView from "@/views/DashboardView.vue";
import KnowledgeView from "@/views/KnowledgeView.vue";
import DocumentsView from "@/views/DocumentsView.vue";
import IndexBuildView from "@/views/IndexBuildView.vue";
import PlaygroundView from "@/views/PlaygroundView.vue";
import TracesView from "@/views/TracesView.vue";
import ConfigurationView from "@/views/ConfigurationView.vue";
import SettingsView from "@/views/SettingsView.vue";

const props = defineProps<{ view: string }>();

const store = useAdminStore();

const views: Record<string, typeof DashboardView> = {
  dashboard: DashboardView,
  knowledge: KnowledgeView,
  documents: DocumentsView,
  indexes: IndexBuildView,
  playground: PlaygroundView,
  traces: TracesView,
  configuration: ConfigurationView,
  settings: SettingsView,
};

let indexPollTimer: ReturnType<typeof setInterval> | undefined;

onMounted(() => {
  void store.run(() => store.refreshAll());
  indexPollTimer = setInterval(() => {
    if (props.view === "indexes" && store.indexes.some((index) => index.status === "BUILDING")) {
      void store.loadKnowledgeDetails().catch(() => undefined);
    }
  }, 3_000);
});

onUnmounted(() => clearInterval(indexPollTimer));
</script>

<template>
  <section v-loading="store.loading" class="studio-content">
    <el-alert
      v-if="store.error"
      :title="store.error"
      type="error"
      :closable="false"
      show-icon
      class="studio-alert"
    />
    <el-alert
      v-if="store.notice"
      :title="store.notice"
      type="success"
      show-icon
      class="studio-alert"
      @close="store.notice = ''"
    />
    <component :is="views[props.view] ?? SettingsView" />

    <!-- Shared trace detail drawer (opened from Dashboard recent activity) -->
    <el-drawer
      v-model="store.traceDrawerOpen"
      title="RAG Trace"
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
        <h4 class="section-label">Answer</h4>
        <!-- eslint-disable-next-line vue/no-v-html -- DOMPurify 消毒后的 Markdown 输出 -->
        <div class="trace-answer" v-html="renderMarkdown(store.selectedTrace.answer || '')"></div>
        <h4 class="section-label">Raw JSON</h4>
        <CodeBlock :code="store.formatJson(store.selectedTrace)" max-height="360px" />
      </template>
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

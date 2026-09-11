<script setup lang="ts">
import {
  Box,
  ChatDotRound,
  Cpu,
  Files,
  Notebook,
  Odometer,
  Operation,
  Tickets,
} from "@element-plus/icons-vue";
import { computed, onMounted, onUnmounted, ref, watch, type Component } from "vue";

import AdminStudio from "@/components/AdminStudio.vue";
import DemoChat from "@/components/DemoChat.vue";
import HealthPanel from "@/components/HealthPanel.vue";
import EmptyState from "@/components/ui/EmptyState.vue";
import PageHeader from "@/components/ui/PageHeader.vue";
import { useAdminStore } from "@/composables/adminStore";

interface MenuItem {
  key: string;
  label: string;
  subtitle: string;
  icon: Component;
}

interface MenuGroup {
  label: string;
  items: MenuItem[];
}

interface NavigationLocation {
  view: string;
  query: string;
  canonicalHash: string;
}

const menuGroups: MenuGroup[] = [
  {
    label: "概览",
    items: [
      { key: "dashboard", label: "运行概览", subtitle: "知识库与索引运行概况", icon: Odometer },
    ],
  },
  {
    label: "知识管理",
    items: [
      { key: "knowledge", label: "知识库", subtitle: "知识域与目录管理", icon: Notebook },
      { key: "documents", label: "文档管理", subtitle: "文档上传、内容与切片检查", icon: Files },
    ],
  },
  {
    label: "运行与验证",
    items: [
      { key: "engine-routing", label: "引擎路由", subtitle: "统一配置新会话的问答引擎策略", icon: Operation },
      { key: "support-chat", label: "问答测试", subtitle: "通过统一 Gateway 验证真实对外能力", icon: ChatDotRound },
    ],
  },
  {
    label: "支持管理",
    items: [
      { key: "support-review", label: "配置审核", subtitle: "审核自动生成的 Support Profile 草稿", icon: Notebook },
    ],
  },
  {
    label: "运维",
    items: [
      { key: "logs", label: "日志与链路追踪", subtitle: "错误、失败任务与完整链路排查", icon: Tickets },
      { key: "configuration", label: "模型、凭证与系统配置", subtitle: "模型、Prompt、RAG 参数与服务凭证（Service Token）", icon: Cpu },
    ],
  },
  {
    label: "高级工具",
    items: [
      { key: "indexes", label: "索引管理", subtitle: "技术构建、快照与回收诊断", icon: Box },
      { key: "playground", label: "调试台", subtitle: "检索、Prompt 与答案调试", icon: Operation },
    ],
  },
];

const allItems = menuGroups.flatMap((group) => group.items);
const legacyViewTargets: Record<string, { view: string; query?: string }> = {
  "support-configuration": { view: "indexes" },
  "support-profiles": { view: "support-review" },
  "support-errors": { view: "logs", query: "tab=failed-turns&engine=SUPPORT" },
  "rag-errors": { view: "logs", query: "tab=failed-turns&engine=RAG" },
  traces: { view: "logs", query: "tab=traces" },
  "rag-configuration": { view: "configuration", query: "tab=rag" },
  settings: { view: "configuration", query: "tab=credentials" },
};

const store = useAdminStore();

function readNavigationLocation(): NavigationLocation {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const queryAt = raw.indexOf("?");
  const rawView = queryAt >= 0 ? raw.slice(0, queryAt) : raw;
  const rawQuery = queryAt >= 0 ? raw.slice(queryAt + 1) : "";
  const legacyTarget = legacyViewTargets[rawView];
  const resolvedView = legacyTarget?.view ?? rawView;
  const view = allItems.some((item) => item.key === resolvedView) ? resolvedView : "dashboard";
  const query = view === "dashboard" ? "" : (legacyTarget?.query ?? rawQuery);
  return {
    view,
    query,
    canonicalHash: `#/${view}${query ? `?${query}` : ""}`,
  };
}

const initialNavigation = readNavigationLocation();
if (window.location.hash !== initialNavigation.canonicalHash) {
  window.history.replaceState(null, "", initialNavigation.canonicalHash);
}
const activeView = ref(initialNavigation.view);
const navigationQuery = ref(initialNavigation.query);
const activeMenu = computed(
  () => allItems.find((item) => item.key === activeView.value) ?? allItems[0]!,
);
const isDemoChat = computed(() => activeView.value === "support-chat");

function syncHash(): void {
  const next = readNavigationLocation();
  if (next.view !== activeView.value) activeView.value = next.view;
  navigationQuery.value = next.query;
  if (window.location.hash !== next.canonicalHash) {
    window.history.replaceState(null, "", next.canonicalHash);
  }
}

function navigate(view: string, query = ""): void {
  activeView.value = view;
  navigationQuery.value = query;
  const target = `#/${view}${query ? `?${query}` : ""}`;
  if (window.location.hash !== target) window.history.pushState(null, "", target);
}

watch(
  () => store.requestedView,
  (view) => {
    if (!view || !allItems.some((item) => item.key === view)) return;
    navigate(view, store.requestedViewQuery);
    store.requestedView = "";
    store.requestedViewQuery = "";
  },
);

onMounted(() => {
  window.addEventListener("hashchange", syncHash);
  syncHash();
  if (isDemoChat.value && store.hasServiceCredential && !store.knowledgeBases.length) {
    void store.run(() => store.refreshAll());
  }
});
onUnmounted(() => window.removeEventListener("hashchange", syncHash));

/* Data-driven publishing status (replaces the old decorative A/B diagram) */
const indexStats = computed(() => {
  const kbs = store.knowledgeBases;
  return {
    ready: kbs.filter((kb) => kb.runtime_state === "READY").length,
    building: kbs.filter((kb) => kb.building_index_id).length,
    chunks: kbs.reduce((total, kb) => total + kb.active_chunk_count, 0),
  };
});
</script>

<template>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand">
        <span class="brand-mark">R</span>
        <div><strong>技术支持工作台</strong></div>
      </div>
      <nav>
        <div v-for="group in menuGroups" :key="group.label" class="nav-group">
          <div class="nav-group-label">{{ group.label }}</div>
          <button
            v-for="item in group.items"
            :key="item.key"
            type="button"
            :class="{ active: activeView === item.key }"
            @click="navigate(item.key)"
          >
            <el-icon><component :is="item.icon" /></el-icon><span>{{ item.label }}</span>
          </button>
        </div>
      </nav>
    </aside>

    <main class="main" :class="{ 'chat-mode': isDemoChat }">
      <DemoChat v-if="isDemoChat && store.hasServiceCredential" />
      <div v-else-if="isDemoChat" class="page">
        <PageHeader :title="activeMenu.label" :description="activeMenu.subtitle" />
        <div class="page-body">
          <EmptyState
            title="需要配置服务凭证"
            description="问答测试已暂停加载，请先配置有效的 Service Token。"
          >
            <template #action>
              <el-button type="primary" @click="navigate('configuration', 'tab=credentials')">
                配置服务凭证
              </el-button>
            </template>
          </EmptyState>
        </div>
      </div>
      <div v-else class="page">
        <PageHeader :title="activeMenu.label" :description="activeMenu.subtitle">
          <template #actions>
            <el-button
              v-if="activeView === 'knowledge'"
              type="primary"
              :disabled="!store.hasServiceCredential"
              @click="store.knowledgeDrawerOpen = true"
            >
              新建知识库
            </el-button>
            <el-button
              v-if="activeView === 'documents'"
              type="primary"
              :disabled="!store.hasServiceCredential || !store.selectedKnowledgeId"
              @click="store.uploadDrawerOpen = true"
            >
              上传文档
            </el-button>
          </template>
        </PageHeader>
        <div class="page-body">
          <section v-if="activeView === 'dashboard'" class="panel index-note">
            <div class="index-note-text">
              <h3>双链路知识发布</h3>
              <p>RAG 技术构建生成候选索引，经管理员明确切换后供新会话使用；Support Profile 经人工审核后独立发布。既有会话始终使用各自冻结的版本。</p>
            </div>
            <div class="index-stats">
              <div class="index-stat">
                <strong>{{ indexStats.ready }}</strong>
                <span>在线知识库</span>
              </div>
              <div class="index-stat">
                <strong :class="{ building: indexStats.building > 0 }">{{ indexStats.building }}</strong>
                <span>构建中</span>
              </div>
              <div class="index-stat">
                <strong>{{ indexStats.chunks.toLocaleString() }}</strong>
                <span>已索引切片（Chunks）</span>
              </div>
            </div>
          </section>
          <AdminStudio :view="activeView" :navigation-query="navigationQuery" />
          <HealthPanel v-if="activeView === 'dashboard'" />
        </div>
      </div>
    </main>
  </div>
</template>

<style scoped>
.index-note {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 16px 18px;
}

.index-note h3 {
  color: var(--text-primary);
  font-size: 14px;
  font-weight: 600;
}

.index-note p {
  margin-top: 2px;
  max-width: 520px;
  color: var(--text-secondary);
  font-size: 13px;
}

.index-stats {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 28px;
  padding-left: 24px;
  border-left: 1px solid var(--border-subtle);
}

.index-stat {
  display: grid;
  gap: 2px;
  justify-items: center;
  min-width: 72px;
}

.index-stat strong {
  color: var(--text-primary);
  font-size: 20px;
  font-weight: 600;
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
}

.index-stat strong.building {
  color: var(--warning);
}

.index-stat span {
  color: var(--text-tertiary);
  font-size: 11px;
}

@media (max-width: 900px) {
  .index-note {
    flex-direction: column;
    align-items: flex-start;
  }

  .index-stats {
    padding-left: 0;
    border-left: 0;
  }
}
</style>

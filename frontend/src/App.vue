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
import RuntimeVersion from "@/components/RuntimeVersion.vue";
import EmptyState from "@/components/ui/EmptyState.vue";
import PageHeader from "@/components/ui/PageHeader.vue";
import { SERVICE_CREDENTIAL_REJECTED_EVENT } from "@/api/serviceCredential";
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
      { key: "knowledge", label: "知识库", subtitle: "知识域、目录与 Assistant 上线管理", icon: Notebook },
      { key: "documents", label: "文档管理", subtitle: "文档上传、内容与切片检查", icon: Files },
      { key: "indexes", label: "索引管理", subtitle: "技术构建、快照与回收诊断", icon: Box },
    ],
  },
  {
    label: "问答与调优",
    items: [
      { key: "support-chat", label: "问答测试", subtitle: "通过统一 Gateway 验证 RAG V2 真实对外能力", icon: ChatDotRound },
      { key: "engine-routing", label: "引擎路由", subtitle: "知识库执行策略与就绪检查（当前仅 RAG_ONLY 在线）", icon: Operation },
      { key: "playground", label: "调试台", subtitle: "RAG V2 检索、上下文与答案调试", icon: Operation },
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
    label: "遗留能力（Support · 已冻结）",
    items: [
      { key: "support-review", label: "配置审核", subtitle: "Support Profile 草稿审核与发布（已冻结，仅历史兼容）", icon: Notebook },
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

function handleCredentialRejected(event: Event): void {
  // Older manual dispatches (and any non-CustomEvent) carry no detail; treat
  // that as a real rejection rather than the quieter "never configured" case.
  const reason = (event as CustomEvent<{ reason?: "missing" | "invalid" }>).detail?.reason;
  store.rejectCredential(reason ?? "invalid");
}

// Register during setup so child onMounted hooks cannot emit a rejection
// before the application-level credential owner is listening.
window.addEventListener(SERVICE_CREDENTIAL_REJECTED_EVENT, handleCredentialRejected);

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
onUnmounted(() => {
  window.removeEventListener("hashchange", syncHash);
  window.removeEventListener(SERVICE_CREDENTIAL_REJECTED_EVENT, handleCredentialRejected);
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
      <RuntimeVersion />
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
          <AdminStudio :view="activeView" :navigation-query="navigationQuery" />
          <details v-if="activeView === 'dashboard'" class="panel infrastructure-details">
            <summary>基础服务状态 · 数据库、队列与文件存储</summary>
            <HealthPanel />
          </details>
        </div>
      </div>
    </main>
  </div>
</template>

<style scoped>
.infrastructure-details > summary { padding: 16px 18px; cursor: pointer; color: var(--text-secondary); font-size: 13px; }
</style>

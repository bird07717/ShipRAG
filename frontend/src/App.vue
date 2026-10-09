<script setup lang="ts">
import {
  Box,
  ChatDotRound,
  ChatLineSquare,
  Collection,
  Expand,
  Files,
  Fold,
  Menu,
  Monitor,
  Odometer,
  Setting,
  Share,
  Stamp,
  Tickets,
} from "@element-plus/icons-vue";
import { computed, defineAsyncComponent, onMounted, onUnmounted, ref, watch } from "vue";

import AdminStudio from "@/components/AdminStudio.vue";
import AppNavigation, { type NavigationGroup } from "@/components/AppNavigation.vue";
import BackendStatusBanner from "@/components/BackendStatusBanner.vue";
import ModelRuntimeBanner from "@/components/ModelRuntimeBanner.vue";
import RuntimeCapacityPanel from "@/components/RuntimeCapacityPanel.vue";
import TaskCenter from "@/components/TaskCenter.vue";
import TaskCenterButton from "@/components/TaskCenterButton.vue";
import HealthPanel from "@/components/HealthPanel.vue";
import RuntimeVersion from "@/components/RuntimeVersion.vue";
import EmptyState from "@/components/ui/EmptyState.vue";
import PageHeader from "@/components/ui/PageHeader.vue";
import { SERVICE_CREDENTIAL_REJECTED_EVENT } from "@/api/serviceCredential";
import { useAdminStore } from "@/composables/adminStore";
import { environmentBadge } from "@/utils/modelRuntime";
import { VIEW_QUERY_REPLACED_EVENT } from "@/utils/viewQuery";

// The chat page pulls in the streaming client and Markdown renderer; load it on demand.
const DemoChat = defineAsyncComponent(() => import("@/components/DemoChat.vue"));

interface NavigationLocation {
  view: string;
  query: string;
  canonicalHash: string;
}

const menuGroups: NavigationGroup[] = [
  {
    label: "概览",
    items: [
      { key: "dashboard", label: "运行概览", subtitle: "知识库与索引运行概况", icon: Odometer },
    ],
  },
  {
    label: "知识管理",
    items: [
      { key: "knowledge", label: "知识库", subtitle: "知识域、目录与 Assistant 上线管理", icon: Collection },
      { key: "documents", label: "文档管理", subtitle: "文档上传、内容与切片检查", icon: Files },
      { key: "indexes", label: "索引管理", subtitle: "技术构建、快照与回收诊断", icon: Box },
    ],
  },
  {
    label: "问答与调优",
    items: [
      { key: "support-chat", label: "问答测试", subtitle: "通过统一 Gateway 验证 RAG V2 真实对外能力", icon: ChatDotRound },
      { key: "engine-routing", label: "引擎路由", subtitle: "知识库执行策略与就绪检查（当前仅 RAG_ONLY 在线）", icon: Share },
      { key: "playground", label: "调试台", subtitle: "RAG V2 检索、上下文与答案调试", icon: Monitor },
    ],
  },
  {
    label: "运维",
    items: [
      { key: "conversations", label: "会话浏览", subtitle: "按来源与用户查看完整对话（外部 Assistant / 内部测试）", icon: ChatLineSquare },
      { key: "logs", label: "日志与链路追踪", subtitle: "错误、失败任务与完整链路排查", icon: Tickets },
      { key: "configuration", label: "模型、凭证与系统配置", subtitle: "模型、Prompt、RAG 参数与服务凭证（Service Token）", icon: Setting },
    ],
  },
  {
    label: "遗留能力（Support · 已冻结）",
    collapsible: true,
    items: [
      { key: "support-review", label: "配置审核", subtitle: "Support Profile 草稿审核与发布（已冻结，仅历史兼容）", icon: Stamp },
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
const navigationBadges = computed<Record<string, number>>(() => ({ indexes: store.buildingCount }));
// Shown only once the backend has told us; never guessed from the URL.
const environment = computed(() => environmentBadge(
  store.hasServiceCredential ? (store.systemInfo?.environment ?? store.modelRuntime?.environment) : null,
));

function handleBackendReconnected(): void {
  // Data on screen may be stale after an outage; reload what the shell owns.
  if (store.hasServiceCredential) void store.run(() => store.refreshAll());
}

const SIDEBAR_STORAGE_KEY = "shipdemo.sidebarCollapsed";
function readSidebarPreference(): boolean {
  try {
    return window.localStorage.getItem(SIDEBAR_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}
const sidebarPreferredCollapsed = ref(readSidebarPreference());
const mobileNavOpen = ref(false);
// Read the viewport synchronously so the first paint already has the right
// sidebar mode (no expand-then-collapse animation on narrow screens).
const narrowQuery = typeof window.matchMedia === "function" ? window.matchMedia("(max-width: 1024px)") : undefined;
const mobileQuery = typeof window.matchMedia === "function" ? window.matchMedia("(max-width: 720px)") : undefined;
const narrowViewport = ref(narrowQuery?.matches ?? false);
const mobileViewport = ref(mobileQuery?.matches ?? false);
// Below 1024px the sidebar is always icon-only; the manual toggle applies above it.
const sidebarCollapsed = computed(() => narrowViewport.value || sidebarPreferredCollapsed.value);

function toggleSidebar(): void {
  sidebarPreferredCollapsed.value = !sidebarPreferredCollapsed.value;
  try {
    window.localStorage.setItem(SIDEBAR_STORAGE_KEY, sidebarPreferredCollapsed.value ? "1" : "0");
  } catch {
    // Preference is a convenience only; storage may be unavailable.
  }
}

function updateViewport(): void {
  narrowViewport.value = narrowQuery?.matches ?? false;
  mobileViewport.value = mobileQuery?.matches ?? false;
  if (!mobileViewport.value) mobileNavOpen.value = false;
}

function watchViewport(): void {
  updateViewport();
  narrowQuery?.addEventListener?.("change", updateViewport);
  mobileQuery?.addEventListener?.("change", updateViewport);
}

function navigateFromMenu(view: string): void {
  mobileNavOpen.value = false;
  navigate(view);
}

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
  watchViewport();
  window.addEventListener("hashchange", syncHash);
  // Views write their tab/selection back with replaceState (no hashchange);
  // keep navigationQuery equal to the URL so re-following a link still works.
  window.addEventListener(VIEW_QUERY_REPLACED_EVENT, syncHash);
  syncHash();
  if (isDemoChat.value && store.hasServiceCredential && !store.knowledgeBases.length) {
    void store.run(() => store.refreshAll());
  }
});
onUnmounted(() => {
  narrowQuery?.removeEventListener?.("change", updateViewport);
  mobileQuery?.removeEventListener?.("change", updateViewport);
  window.removeEventListener("hashchange", syncHash);
  window.removeEventListener(VIEW_QUERY_REPLACED_EVENT, syncHash);
  window.removeEventListener(SERVICE_CREDENTIAL_REJECTED_EVENT, handleCredentialRejected);
});

</script>

<template>
  <div class="shell" :class="{ 'is-collapsed': sidebarCollapsed }">
    <aside class="sidebar">
      <div class="brand">
        <span class="brand-mark" :title="environment?.label">R</span>
        <div class="brand-text">
          <strong>技术支持工作台</strong><small>知识库 · RAG 管理台</small>
          <span v-if="environment" class="env-badge" :class="`env-${environment.tone}`">{{ environment.label }}</span>
        </div>
      </div>
      <AppNavigation
        :groups="menuGroups"
        :active="activeView"
        :collapsed="sidebarCollapsed"
        :badges="navigationBadges"
        @navigate="navigateFromMenu"
      />
      <TaskCenterButton :compact="sidebarCollapsed" />
      <RuntimeVersion :compact="sidebarCollapsed" />
      <button
        v-if="!narrowViewport"
        type="button"
        class="sidebar-toggle"
        :aria-label="sidebarCollapsed ? '展开侧栏' : '收起侧栏'"
        :aria-expanded="!sidebarCollapsed"
        @click="toggleSidebar"
      >
        <el-icon><component :is="sidebarCollapsed ? Expand : Fold" /></el-icon>
        <span>收起侧栏</span>
      </button>
    </aside>

    <header class="mobile-topbar">
      <button
        type="button"
        class="mobile-menu-button"
        aria-label="打开导航"
        :aria-expanded="mobileNavOpen"
        @click="mobileNavOpen = true"
      >
        <el-icon><Menu /></el-icon>
      </button>
      <span class="brand-mark small">R</span>
      <strong>{{ activeMenu.label }}</strong>
      <span v-if="environment" class="env-badge" :class="`env-${environment.tone}`">{{ environment.label }}</span>
      <TaskCenterButton variant="topbar" />
    </header>
    <TaskCenter />
    <el-drawer
      v-if="mobileViewport"
      v-model="mobileNavOpen"
      direction="ltr"
      size="272px"
      :with-header="false"
      class="mobile-nav-drawer"
    >
      <div class="sidebar mobile-sidebar">
        <div class="brand">
          <span class="brand-mark">R</span>
          <div class="brand-text"><strong>技术支持工作台</strong><small>知识库 · RAG 管理台</small></div>
        </div>
        <AppNavigation
          v-if="mobileNavOpen"
          :groups="menuGroups"
          :active="activeView"
          :badges="navigationBadges"
          @navigate="navigateFromMenu"
        />
      </div>
    </el-drawer>

    <main class="main" :class="{ 'chat-mode': isDemoChat }">
      <div class="system-banners">
        <BackendStatusBanner @reconnected="handleBackendReconnected" />
        <ModelRuntimeBanner />
      </div>
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
            <summary>基础服务与运行容量 · 数据库、队列、文件存储、并发与任务积压</summary>
            <div class="infrastructure-body">
              <HealthPanel />
              <RuntimeCapacityPanel v-if="store.hasServiceCredential" />
            </div>
          </details>
        </div>
      </div>
    </main>
  </div>
</template>

<style scoped>
.infrastructure-details > summary { padding: 16px 18px; cursor: pointer; color: var(--text-secondary); font-size: 13px; }
.infrastructure-body { display: grid; gap: var(--space-content-gap); padding: 0 18px 18px; }
</style>

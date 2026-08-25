<script setup lang="ts">
import {
  Box,
  ChatDotRound,
  Cpu,
  DataLine,
  Files,
  Notebook,
  Odometer,
  Operation,
  Setting,
  Tickets,
} from "@element-plus/icons-vue";
import { computed, onMounted, onUnmounted, ref, watch, type Component } from "vue";

import AdminStudio from "@/components/AdminStudio.vue";
import DemoChat from "@/components/DemoChat.vue";
import HealthPanel from "@/components/HealthPanel.vue";
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

const menuGroups: MenuGroup[] = [
  {
    label: "Overview",
    items: [
      { key: "dashboard", label: "Dashboard", subtitle: "知识与索引运行概况", icon: Odometer },
    ],
  },
  {
    label: "Runtime",
    items: [
      { key: "engine-routing", label: "Engine Routing", subtitle: "统一配置新会话的问答引擎策略", icon: Operation },
      { key: "support-chat", label: "DemoChat 问答测试", subtitle: "通过统一 Gateway 验证真实对外能力", icon: ChatDotRound },
    ],
  },
  {
    label: "Data",
    items: [
      { key: "knowledge", label: "Knowledge Base", subtitle: "知识域与目录管理", icon: Notebook },
      { key: "documents", label: "Documents", subtitle: "Word 文档、Element 与 Chunk", icon: Files },
      { key: "indexes", label: "Index Build", subtitle: "双索引构建与原子发布", icon: Box },
    ],
  },
  {
    label: "Technical Support",
    items: [
      { key: "support-profiles", label: "问题文档配置", subtitle: "标准问题与完整文档映射", icon: Files },
      { key: "support-configuration", label: "Support Configuration", subtitle: "Profile 与完整文档发布就绪状态", icon: Setting },
      { key: "support-errors", label: "Support Errors", subtitle: "Profile、文档、工作流与安全错误", icon: Tickets },
    ],
  },
  {
    label: "Generic RAG",
    items: [
      { key: "playground", label: "RAG Playground", subtitle: "检索、Prompt 与答案调试", icon: Operation },
      { key: "rag-configuration", label: "RAG Configuration", subtitle: "召回、融合、重排与上下文配置", icon: Cpu },
      { key: "rag-errors", label: "RAG Errors", subtitle: "检索、模型与引用错误", icon: Tickets },
    ],
  },
  {
    label: "Diagnostics",
    items: [
      { key: "logs", label: "Logs & Errors", subtitle: "失败请求、轮次与任务排查", icon: Tickets },
      { key: "traces", label: "Trace", subtitle: "完整 RAG 链路追踪", icon: DataLine },
    ],
  },
  {
    label: "Configuration",
    items: [
      { key: "configuration", label: "Models & Prompt", subtitle: "共享模型注册与提示词快照", icon: Cpu },
      { key: "settings", label: "Settings", subtitle: "本地管理台设置", icon: Setting },
    ],
  },
];

const allItems = menuGroups.flatMap((group) => group.items);

const store = useAdminStore();

function hashView(): string {
  const value = window.location.hash.replace(/^#\/?/, "");
  return allItems.some((item) => item.key === value) ? value : "dashboard";
}

const activeView = ref(hashView());
const activeMenu = computed(
  () => allItems.find((item) => item.key === activeView.value) ?? allItems[0]!,
);
const isDemoChat = computed(() => activeView.value === "support-chat");

function selectKnowledge(): void {
  void store.selectKnowledge();
}

function syncHash(): void {
  const next = hashView();
  if (next !== activeView.value) activeView.value = next;
}

watch(activeView, (view) => {
  const target = `#/${view}`;
  if (window.location.hash !== target) window.history.pushState(null, "", target);
});

watch(
  () => store.requestedView,
  (view) => {
    if (!view || !allItems.some((item) => item.key === view)) return;
    activeView.value = view;
    store.requestedView = "";
  },
);

onMounted(() => {
  window.addEventListener("hashchange", syncHash);
  if (isDemoChat.value && !store.knowledgeBases.length) {
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
        <div><strong>Support Studio</strong><small>Technical Documents</small></div>
      </div>
      <nav>
        <div v-for="group in menuGroups" :key="group.label" class="nav-group">
          <div class="nav-group-label">{{ group.label }}</div>
          <button
            v-for="item in group.items"
            :key="item.key"
            type="button"
            :class="{ active: activeView === item.key }"
            @click="activeView = item.key"
          >
            <el-icon><component :is="item.icon" /></el-icon><span>{{ item.label }}</span>
          </button>
        </div>
      </nav>
      <div class="sidebar-footer">V1 · DUAL ENGINE</div>
    </aside>

    <main class="main" :class="{ 'chat-mode': isDemoChat }">
      <section class="management-context" aria-label="当前管理范围">
        <div class="context-field">
          <span>当前知识库</span>
          <el-select
            v-model="store.selectedKnowledgeId"
            placeholder="选择知识库"
            size="small"
            @change="selectKnowledge"
          >
            <el-option
              v-for="kb in store.knowledgeBases"
              :key="kb.id"
              :label="kb.name"
              :value="kb.id"
            />
          </el-select>
        </div>
        <div class="context-fact">
          <span>环境</span>
          <strong>{{ store.systemInfo?.environment || "未知" }}</strong>
        </div>
        <div class="context-fact">
          <span>线上版本</span>
          <strong>{{ store.activeIndex ? `Index v${store.activeIndex.version}` : "无" }}</strong>
        </div>
        <button
          type="button"
          class="candidate-context"
          :class="{ actionable: store.selectedRelease }"
          :disabled="!store.selectedRelease"
          @click="store.navigate('support-profiles')"
        >
          <span>发布候选</span>
          <strong v-if="store.selectedRelease">
            v{{ store.selectedRelease.index_version }} · {{ store.pendingReviewCount }} 项待处理
          </strong>
          <strong v-else>无</strong>
        </button>
      </section>
      <DemoChat v-if="isDemoChat" />
      <div v-else class="page">
        <PageHeader :title="activeMenu.label" :description="activeMenu.subtitle">
          <template #actions>
            <el-button
              v-if="activeView === 'knowledge'"
              type="primary"
              @click="store.knowledgeDrawerOpen = true"
            >
              新建知识库
            </el-button>
            <el-button
              v-if="activeView === 'documents'"
              type="primary"
              :disabled="!store.selectedKnowledgeId"
              @click="store.uploadDrawerOpen = true"
            >
              上传文档
            </el-button>
          </template>
        </PageHeader>
        <div class="page-body">
          <section v-if="activeView === 'dashboard'" class="panel index-note">
            <div class="index-note-text">
              <h3>不可变索引发布</h3>
              <p>构建在后台，切换在瞬间。线上请求始终冻结 Active Index；新快照完成验证后原子发布。</p>
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
                <span>已索引 Chunks</span>
              </div>
            </div>
          </section>
          <AdminStudio :view="activeView" />
          <HealthPanel v-if="activeView === 'dashboard'" />
        </div>
      </div>
    </main>
  </div>
</template>

<style scoped>
.management-context {
  display: flex;
  align-items: center;
  gap: 18px;
  width: min(var(--content-max-width), 100%);
  margin: 0 auto 16px;
  padding: 10px 14px;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg);
  background: var(--bg-surface);
}

.context-field,
.context-fact,
.candidate-context {
  display: grid;
  gap: 2px;
}

.context-field {
  grid-template-columns: auto 220px;
  align-items: center;
  gap: 10px;
}

.context-field > span,
.context-fact span,
.candidate-context span {
  color: var(--text-tertiary);
  font-size: 11px;
}

.context-fact,
.candidate-context {
  padding-left: 18px;
  border-left: 1px solid var(--border-subtle);
}

.context-fact strong,
.candidate-context strong {
  color: var(--text-primary);
  font-size: 12px;
  font-weight: 600;
}

.candidate-context {
  margin-left: auto;
  border-top: 0;
  border-right: 0;
  border-bottom: 0;
  background: transparent;
  text-align: left;
}

.candidate-context.actionable {
  cursor: pointer;
}

.candidate-context.actionable strong {
  color: var(--brand-primary);
}

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
  .management-context {
    flex-wrap: wrap;
  }

  .context-field {
    grid-template-columns: 1fr;
  }

  .candidate-context {
    margin-left: 0;
  }

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

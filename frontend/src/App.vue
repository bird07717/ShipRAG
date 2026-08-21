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
} from "@element-plus/icons-vue";
import { computed, ref, type Component } from "vue";

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
    label: "Data",
    items: [
      { key: "knowledge", label: "Knowledge Base", subtitle: "知识域与目录管理", icon: Notebook },
      { key: "documents", label: "Documents", subtitle: "Word 文档、Element 与 Chunk", icon: Files },
      { key: "indexes", label: "Index Build", subtitle: "双索引构建与原子发布", icon: Box },
    ],
  },
  {
    label: "RAG",
    items: [
      { key: "playground", label: "RAG Playground", subtitle: "检索、Prompt 与答案调试", icon: Operation },
      { key: "traces", label: "Trace", subtitle: "完整 RAG 链路追踪", icon: DataLine },
      { key: "demo-chat", label: "Demo Chat", subtitle: "模拟用户问答演示", icon: ChatDotRound },
    ],
  },
  {
    label: "Configuration",
    items: [
      { key: "configuration", label: "Models & Prompt", subtitle: "全局 AI 模型与提示词快照", icon: Cpu },
      { key: "settings", label: "Settings", subtitle: "本地管理台设置", icon: Setting },
    ],
  },
];

const allItems = menuGroups.flatMap((group) => group.items);

const store = useAdminStore();

const activeView = ref("dashboard");
const activeMenu = computed(
  () => allItems.find((item) => item.key === activeView.value) ?? allItems[0]!,
);
const isDemoChat = computed(() => activeView.value === "demo-chat");

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
        <div><strong>RAG Studio</strong><small>Knowledge Engine</small></div>
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
      <div class="sidebar-footer">V1 · M6 COMPLETE</div>
    </aside>

    <main class="main" :class="{ 'chat-mode': isDemoChat }">
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
            <el-button
              v-if="activeView === 'indexes'"
              type="primary"
              :disabled="!store.selectedKnowledgeId"
              @click="store.buildIndex()"
            >
              新建构建
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

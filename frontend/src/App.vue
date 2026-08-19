<script setup lang="ts">
import {
  Collection,
  DataAnalysis,
  Document,
  Grid,
  Operation,
  SetUp,
  Setting,
} from "@element-plus/icons-vue";
import { computed, ref } from "vue";

import AdminStudio from "@/components/AdminStudio.vue";
import HealthPanel from "@/components/HealthPanel.vue";

const menu = [
  { key: "dashboard", label: "Dashboard", icon: DataAnalysis, subtitle: "知识与索引运行概况" },
  { key: "knowledge", label: "Knowledge Base", icon: Collection, subtitle: "知识域与目录管理" },
  { key: "documents", label: "Documents", icon: Document, subtitle: "Word、Element 与 Chunk" },
  { key: "indexes", label: "Index Build", icon: Grid, subtitle: "双索引构建与发布" },
  { key: "playground", label: "RAG Playground", icon: Operation, subtitle: "检索、Prompt 与答案调试" },
  { key: "traces", label: "Trace", icon: SetUp, subtitle: "完整 RAG 链路追踪" },
  { key: "configuration", label: "Models & Prompt", icon: Setting, subtitle: "全局 AI 与提示词快照" },
  { key: "settings", label: "Settings", icon: Setting, subtitle: "本地管理台设置" },
];

const activeView = ref("dashboard");
const activeMenu = computed(() => menu.find((item) => item.key === activeView.value) ?? menu[0]!);
</script>

<template>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand">
        <span class="brand-mark">R</span>
        <div><strong>RAG Studio</strong><small>Knowledge Engine</small></div>
      </div>
      <nav>
        <button
          v-for="item in menu"
          :key="item.key"
          type="button"
          :class="{ active: activeView === item.key }"
          @click="activeView = item.key"
        >
          <el-icon><component :is="item.icon" /></el-icon><span>{{ item.label }}</span>
        </button>
      </nav>
      <div class="version">V1 · M6 COMPLETE</div>
    </aside>

    <main>
      <header>
        <div>
          <span class="kicker">ENTERPRISE KNOWLEDGE</span>
          <h1>{{ activeMenu.label }}</h1>
          <p>{{ activeMenu.subtitle }}</p>
        </div>
        <el-tag effect="dark" type="success" round>M6</el-tag>
      </header>
      <section v-if="activeView === 'dashboard'" class="hero">
        <div><span class="hero-label">IMMUTABLE INDEX PUBLISHING</span><h2>构建在后台，<br />切换在瞬间。</h2><p>线上请求始终冻结 Active Index；新快照完成验证后原子发布。</p></div>
        <div class="index-switch" aria-hidden="true"><span>ACTIVE · A</span><i>→</i><span>BUILDING · B</span></div>
      </section>
      <AdminStudio :view="activeView" />
      <HealthPanel v-if="activeView === 'dashboard'" />
    </main>
  </div>
</template>

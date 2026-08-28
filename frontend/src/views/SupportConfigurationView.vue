<script setup lang="ts">
import { computed, onMounted, watch } from "vue";

import StatusBadge from "@/components/ui/StatusBadge.vue";
import { useAdminStore } from "@/composables/adminStore";
import { useSupportAdminStore } from "@/composables/supportAdminStore";

const shared = useAdminStore();
const support = useSupportAdminStore();
const readyProfiles = computed(() => support.profiles.filter((item) => item.support_ready));
const readinessPercentage = computed(() =>
  support.profiles.length
    ? Math.round((readyProfiles.value.length / support.profiles.length) * 100)
    : 0,
);

async function load(): Promise<void> {
  await support.run(() => support.loadConfiguration());
}

watch(() => shared.selectedKnowledgeId, () => void load());
onMounted(() => void load());
</script>

<template>
  <div class="domain-config">
    <el-alert
      v-if="support.error"
      :title="support.error"
      type="error"
      :closable="false"
      show-icon
    />
    <el-alert
      title="该页面只检查 Profile 支持（Profile Support）能力。总引擎策略请在引擎路由（Engine Routing）页面管理。"
      type="info"
      :closable="false"
      show-icon
    />
    <section class="panel">
      <header class="panel-header">
        <div>
          <h3>Profile 支持配置状态（Profile Support）</h3>
          <span class="sub">检查问题 Profile、完整文档映射和发布就绪状态</span>
        </div>
      </header>
      <div v-if="shared.selectedKnowledgeId" class="panel-body config-body">
        <div class="summary">
          <div>
            <span>当前知识库</span>
            <strong>{{ shared.selectedKnowledge?.name }}</strong>
          </div>
          <div>
            <span>已发布就绪</span>
            <strong>{{ readyProfiles.length }} / {{ support.profiles.length }}</strong>
          </div>
        </div>
        <el-progress
          :percentage="readinessPercentage"
          :status="readinessPercentage === 100 ? 'success' : undefined"
        />
        <el-table v-if="support.profiles.length" :data="support.profiles">
          <el-table-column prop="problem_code" label="问题编码（Problem Code）" width="180" />
          <el-table-column
            prop="canonical_question"
            label="标准问题"
            min-width="260"
            show-overflow-tooltip
          />
          <el-table-column label="发布状态" width="130">
            <template #default="scope">
              <StatusBadge :status="scope.row.support_ready ? 'READY' : 'NOT_READY'" />
            </template>
          </el-table-column>
          <el-table-column label="未就绪原因" min-width="280">
            <template #default="scope">
              {{ scope.row.readiness_reasons?.join("；") || "—" }}
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-else description="当前知识库尚未配置 Problem Profile" />
      </div>
      <el-empty v-else description="请先选择知识库" />
    </section>
  </div>
</template>

<style scoped>
.config-body { display: grid; gap: 18px; }
.summary { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.summary > div { display: grid; gap: 5px; padding: 14px; border-radius: 9px; background: var(--bg-subtle); }
.summary span { color: var(--text-tertiary); font-size: 12px; }
.summary strong { color: var(--text-primary); font-size: 18px; }
@media (max-width: 760px) { .summary { grid-template-columns: 1fr; } }
</style>

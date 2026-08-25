<script setup lang="ts">
import { onMounted, ref, watch } from "vue";

import { useAdminStore } from "@/composables/adminStore";
import { useSupportAdminStore } from "@/composables/supportAdminStore";
import type { ExecutionPolicy } from "@/types/admin";

const shared = useAdminStore();
const support = useSupportAdminStore();
const selectedPolicy = ref<ExecutionPolicy>("SUPPORT_ONLY");

const options: Array<{ value: ExecutionPolicy; label: string; description: string }> = [
  {
    value: "SUPPORT_ONLY",
    label: "仅 Profile Support",
    description: "未匹配时要求补充信息，不自动进入通用 RAG。",
  },
  {
    value: "SUPPORT_WITH_RAG_FALLBACK",
    label: "Support 优先，RAG 降级",
    description: "当前仅允许 NO_MATCH 自动降级，安全错误不会绕过门禁。",
  },
  {
    value: "RAG_ONLY",
    label: "仅通用 RAG",
    description: "新会话跳过 Profile 和完整文档交付。",
  },
];

async function load(): Promise<void> {
  await support.run(() => support.loadConfiguration());
  if (support.policy) selectedPolicy.value = support.policy.execution_policy;
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
      v-if="support.notice"
      :title="support.notice"
      type="success"
      show-icon
    />
    <section class="panel">
      <header class="panel-header">
        <div>
          <h3>Profile Support 运行配置</h3>
          <span class="sub">策略按知识库保存，并在新会话创建时冻结</span>
        </div>
      </header>
      <div v-if="shared.selectedKnowledgeId" class="panel-body config-body">
        <div class="field">
          <label>当前知识库</label>
          <strong>{{ shared.selectedKnowledge?.name }}</strong>
        </div>
        <div class="field">
          <label>问答运行策略</label>
          <el-select v-model="selectedPolicy" class="policy-select">
            <el-option
              v-for="option in options"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
          <p>{{ options.find((item) => item.value === selectedPolicy)?.description }}</p>
        </div>
        <div class="policy-meta">
          <span>配置版本：{{ support.policy?.config_version ?? 0 }}</span>
          <span>来源：{{ support.policy?.source ?? "DEFAULT" }}</span>
          <span>自动降级白名单：{{ support.policy?.fallback_conditions?.join(", ") || "无" }}</span>
        </div>
        <div class="readiness">
          <strong>{{ support.profiles.filter((item) => item.support_ready).length }}</strong>
          <span>/ {{ support.profiles.length }} 个 Profile 已发布就绪</span>
        </div>
        <div class="actions">
          <el-button
            type="primary"
            :loading="support.loading"
            @click="support.savePolicy(selectedPolicy)"
          >
            保存 Support 策略
          </el-button>
        </div>
      </div>
      <el-empty v-else description="请先选择知识库" />
    </section>
  </div>
</template>

<style scoped>
.config-body { display: grid; gap: 18px; max-width: 680px; }
.field { display: grid; gap: 7px; }
.field label { color: var(--text-secondary); font-size: 13px; font-weight: 600; }
.field p, .policy-meta { color: var(--text-tertiary); font-size: 12px; }
.policy-select { width: 100%; }
.policy-meta { display: flex; flex-wrap: wrap; gap: 16px; }
.readiness { display: flex; align-items: baseline; gap: 6px; }
.readiness strong { color: var(--success); font-size: 24px; }
.actions { display: flex; justify-content: flex-end; }
</style>

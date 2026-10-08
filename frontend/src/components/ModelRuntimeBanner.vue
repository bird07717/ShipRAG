<script setup lang="ts">
import { WarningFilled } from "@element-plus/icons-vue";
import { computed, ref } from "vue";

import { useAdminStore } from "@/composables/adminStore";
import { modelRuntimeIssues } from "@/utils/modelRuntime";

/*
 * Shows when any model type does not run as a configured real model, using
 * the backend's own factory decision (/system/model-runtime). Nothing is
 * shown while the status is unknown: absence of the banner only means "no
 * issue reported", never a guess.
 */
const store = useAdminStore();
const expanded = ref(false);

const status = computed(() => (store.hasServiceCredential ? store.modelRuntime : null));
const issues = computed(() => modelRuntimeIssues(status.value));
const simulated = computed(() => issues.value.filter((issue) => issue.severity === "simulated"));
const unavailable = computed(() => issues.value.filter((issue) => issue.severity === "unavailable"));
const simulatedIndexes = computed(() => status.value?.simulated_active_index_count ?? 0);
const visible = computed(() => issues.value.length > 0 || simulatedIndexes.value > 0);
const tone = computed(() => (simulated.value.length || simulatedIndexes.value ? "danger" : "warning"));
const title = computed(() => {
  if (simulated.value.length || simulatedIndexes.value) {
    return "当前有模型使用模拟实现，检索与回答结果不代表真实效果";
  }
  return "部分模型当前不可用";
});
const summary = computed(() => {
  const parts: string[] = [];
  if (simulated.value.length) parts.push(`模拟：${simulated.value.map((issue) => issue.item.model_type).join("、")}`);
  if (unavailable.value.length) parts.push(`不可用：${unavailable.value.map((issue) => issue.item.model_type).join("、")}`);
  if (simulatedIndexes.value) parts.push(`${simulatedIndexes.value} 个当前生效的索引由模拟向量构建`);
  return parts.join("；");
});
</script>

<template>
  <div
    v-if="visible"
    class="system-banner"
    :class="`tone-${tone}`"
    role="alert"
  >
    <el-icon class="banner-icon"><WarningFilled /></el-icon>
    <div class="banner-text">
      <strong>{{ title }}</strong>
      <span>{{ summary }}</span>
      <ul v-if="expanded" class="banner-details">
        <li v-for="issue in issues" :key="issue.item.model_type">
          <b>{{ issue.label }}</b>：{{ issue.detail }}
        </li>
        <li v-if="simulatedIndexes">
          <b>索引</b>：{{ simulatedIndexes }} 个 ACTIVE 索引的 Embedding 为模拟向量（deterministic-shake256），配置真实 Embedding 后需重建并重新设为当前索引
        </li>
      </ul>
    </div>
    <el-button size="small" text @click="expanded = !expanded">{{ expanded ? "收起" : "查看原因" }}</el-button>
    <el-button size="small" @click="store.navigate('configuration', 'tab=models')">前往模型配置</el-button>
  </div>
</template>

<script setup lang="ts">
import { Timer } from "@element-plus/icons-vue";
import { computed } from "vue";

import { useAdminStore } from "@/composables/adminStore";
import { taskCenter } from "@/composables/taskCenterStore";

/* Trigger for the global task drawer; data and polling live in TaskCenter. */
const props = withDefaults(defineProps<{ compact?: boolean; variant?: "sidebar" | "topbar" }>(), {
  compact: false,
  variant: "sidebar",
});
const store = useAdminStore();
const activeCount = computed(() => taskCenter.active.length);
const label = computed(() => (activeCount.value ? `后台任务（${activeCount.value} 个进行中）` : "后台任务"));
</script>

<template>
  <el-tooltip :content="label" placement="right" :disabled="!props.compact">
    <button
      type="button"
      class="task-center-button"
      :class="`is-${props.variant}`"
      :aria-label="label"
      :disabled="!store.hasServiceCredential"
      @click="taskCenter.drawerOpen = true"
    >
      <el-icon><Timer /></el-icon>
      <span class="task-center-label">后台任务</span>
      <span v-if="activeCount" class="nav-badge">{{ activeCount }}</span>
    </button>
  </el-tooltip>
</template>

<script setup lang="ts">
import { computed } from "vue";

import { statusDisplay } from "@/utils/statusLabels";

/*
 * Unified status badge for the whole console.
 * Maps backend enum statuses to semantic tones; unknown values render as neutral.
 */
const props = defineProps<{
  status: string;
}>();

const resolved = computed(() => statusDisplay(props.status));
</script>

<template>
  <span class="status-badge" :class="resolved.tone">{{ resolved.label }}</span>
</template>

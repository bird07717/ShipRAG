<script setup lang="ts">
import { computed, ref } from "vue";
import { CopyDocument } from "@element-plus/icons-vue";

const props = defineProps<{
  code: string;
  maxHeight?: string;
}>();

const copied = ref(false);

const style = computed(() => (props.maxHeight ? { maxHeight: props.maxHeight } : undefined));

async function copy(): Promise<void> {
  try {
    await navigator.clipboard.writeText(props.code);
    copied.value = true;
    setTimeout(() => (copied.value = false), 1500);
  } catch {
    /* clipboard unavailable; ignore */
  }
}
</script>

<template>
  <div class="code-block">
    <pre :style="style">{{ code }}</pre>
    <el-button
      class="copy-button"
      size="small"
      text
      :icon="CopyDocument"
      @click="copy"
    >
      {{ copied ? "已复制" : "复制" }}
    </el-button>
  </div>
</template>

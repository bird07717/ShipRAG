<script setup lang="ts">
import { computed } from "vue";
import { renderMarkdown } from "@/utils/markdown";
import ReviewImage from "@/components/ReviewImage.vue";
const props = defineProps<{ elements: Array<Record<string, unknown>> }>();
const ordered = computed(() => [...props.elements].sort((a, b) => Number(a.sequence_no) - Number(b.sequence_no)));
</script>
<!-- eslint-disable vue/no-v-html -- Markdown rendering is sanitized by DOMPurify. -->
<template>
  <div class="document-preview">
    <p class="muted">按所选索引的解析顺序展示；图片识别文字仅作排查参考。回答交付会去除首条非空文本行。</p>
    <p v-if="!ordered.length">该索引没有可展示的解析元素。</p>
    <section v-for="element in ordered" :key="String(element.id)" class="element">
      <small>{{ element.sequence_no }} · {{ element.element_type }}</small>
      <template v-if="element.element_type === 'IMAGE'">
        <ReviewImage v-if="element.image_asset_id" :key="String(element.image_asset_id)" :asset-id="String(element.image_asset_id)" />
        <p v-else>图片未提取，请检查源文档与构建告警。</p>
        <details v-if="element.ocr_text || element.vision_caption">
          <summary>识别文字与图片描述</summary>
          <p>{{ element.ocr_text }}</p><p>{{ element.vision_caption }}</p>
        </details>
      </template>
      <div v-else-if="element.element_type === 'TABLE'" v-html="renderMarkdown(String(element.content || ''))"></div>
      <div v-else class="original-text">{{ element.content }}</div>
    </section>
  </div>
</template>
<style scoped>
.element { padding: 12px 0; border-bottom: 1px solid var(--border-default); }
small { color: var(--text-secondary); }
.original-text { white-space: pre-wrap; line-height: 1.8; overflow-wrap: anywhere; }
.document-preview :deep(table) { border-collapse: collapse; width: 100%; }
.document-preview :deep(td), .document-preview :deep(th) { border: 1px solid var(--border-default); padding: 6px; }
</style>

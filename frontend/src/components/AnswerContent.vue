<script setup lang="ts">
import { computed } from "vue";
import { renderMarkdown } from "@/utils/markdown";
import type { AnswerPayload } from "@/types/reviews";
import ReviewImage from "@/components/ReviewImage.vue";
const props = defineProps<{ payload: AnswerPayload }>();
defineEmits<{ inspect: [documentId: string] }>();
const showAnswer = computed(() => !props.payload.content?.length || ["DOC_DELIVERED", "DOCUMENT_DELIVERED"].includes(props.payload.response_type ?? ""));
</script>
<!-- eslint-disable vue/no-v-html -- All Markdown passes through DOMPurify. -->
<template>
  <article class="answer-content">
    <div v-if="showAnswer" v-html="renderMarkdown(payload.answer || '（无完整回答快照）')"></div>
    <template v-for="(block, index) in payload.content ?? []" :key="`${index}-${block.image_asset_id ?? ''}`">
      <ReviewImage v-if="block.type === 'image' && block.image_asset_id" :asset-id="block.image_asset_id" :caption="block.caption" />
      <div v-else v-html="renderMarkdown(block.text || '')"></div>
    </template>
    <section v-for="(section, index) in payload.sections ?? []" :key="index">
      <b>{{ section.title }}</b>
      <div v-html="renderMarkdown(section.content)"></div>
    </section>
    <p v-if="payload.clarification?.message">{{ payload.clarification.message }}</p>
    <ol v-if="payload.clarification?.choices?.length"><li v-for="choice in payload.clarification.choices" :key="choice.choice_id">{{ choice.title }}</li></ol>
    <p v-if="payload.disclaimer">{{ payload.disclaimer }}</p>
    <ul v-if="payload.next_suggestions?.length"><li v-for="suggestion in payload.next_suggestions" :key="suggestion">{{ suggestion }}</li></ul>
    <div v-if="payload.references?.length" class="references">
      <span>引用文档：</span>
      <el-button
        v-for="reference in payload.references"
        :key="reference.document_id"
        text
        @click="$emit('inspect', reference.document_id)"
      >
        {{ reference.title }}
      </el-button>
    </div>
  </article>
</template>
<style scoped>
.answer-content { line-height: 1.8; overflow-wrap: anywhere; }
.answer-content :deep(table) { border-collapse: collapse; width: 100%; }
.answer-content :deep(td), .answer-content :deep(th) { border: 1px solid var(--border-default); padding: 6px; }
.answer-content :deep(pre) { overflow: auto; }
.references { border-top: 1px solid var(--border-default); margin-top: 12px; }
</style>

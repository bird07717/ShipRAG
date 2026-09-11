<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { fetchImageBlobUrl } from "@/api/chat";
const props = defineProps<{ assetId: string; caption?: string }>();
const host = ref<HTMLElement | null>(null);
const url = ref("");
const error = ref("");
let active = true;
let observer: IntersectionObserver | undefined;
async function load() {
  observer?.disconnect();
  try {
    const value = await fetchImageBlobUrl(props.assetId);
    if (active) url.value = value;
    else URL.revokeObjectURL(value);
  } catch { if (active) error.value = "图片不可用，可能已删除或超出保留期限"; }
}
onMounted(() => {
  if (typeof IntersectionObserver === "undefined") void load();
  else {
    observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) void load();
    }, { rootMargin: "200px" });
    if (host.value) observer.observe(host.value);
  }
});
onBeforeUnmount(() => { active = false; observer?.disconnect(); if (url.value) URL.revokeObjectURL(url.value); });
</script>
<template>
  <figure ref="host" class="review-image">
    <img v-if="url" :src="url" :alt="caption || '文档图片'" />
    <p v-else>{{ error || "图片加载中…" }}</p>
    <figcaption v-if="caption">{{ caption }}</figcaption>
  </figure>
</template>
<style scoped>
.review-image { margin: 12px 0; min-height: 48px; }
img { display: block; max-width: 100%; max-height: 600px; object-fit: contain; }
figcaption, p { color: var(--text-secondary); font-size: 12px; }
</style>

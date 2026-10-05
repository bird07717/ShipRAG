<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import { ElImageViewer } from "element-plus";
import { fetchImageBlobUrl } from "@/api/chat";
import { ApiRequestError } from "@/api/serviceCredential";
const props = defineProps<{ assetId: string; caption?: string }>();
const host = ref<HTMLElement | null>(null);
const url = ref("");
const error = ref("");
const loading = ref(false);
const retryable = ref(false);
const preview = ref(false);
let active = true;
let mounted = false;
let observer: IntersectionObserver | undefined;
let controller: AbortController | undefined;
let generation = 0;
function reset() {
  generation++;
  controller?.abort();
  observer?.disconnect();
  if (url.value) URL.revokeObjectURL(url.value);
  url.value = "";
  error.value = "";
  preview.value = false;
  loading.value = false;
  retryable.value = false;
}
async function load() {
  if (!active || loading.value || url.value) return;
  observer?.disconnect();
  const current = ++generation;
  controller = new AbortController();
  loading.value = true;
  error.value = "";
  try {
    const value = await fetchImageBlobUrl(props.assetId, controller.signal);
    if (active && current === generation) { url.value = value; retryable.value = false; }
    else URL.revokeObjectURL(value);
  } catch (cause) {
    if (!active || current !== generation) return;
    const status = cause instanceof ApiRequestError ? cause.status : 0;
    retryable.value = ![401, 403, 404, 410].includes(status);
    error.value = status === 401 ? "服务凭证失效，请重新配置凭证"
      : status === 403 ? "无权查看此图片"
        : [404, 410].includes(status) ? "图片不可用，可能已删除或超出保留期限"
          : "图片加载失败，请检查网络后重试";
  } finally { if (active && current === generation) loading.value = false; }
}
function observe() {
  if (typeof IntersectionObserver === "undefined") void load();
  else {
    observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) void load();
    }, { rootMargin: "200px" });
    if (host.value) observer.observe(host.value);
  }
}
function decodeFailed() {
  if (url.value) URL.revokeObjectURL(url.value);
  url.value = "";
  preview.value = false;
  error.value = "图片无法显示，请重试";
  retryable.value = true;
}
onMounted(() => { mounted = true; observe(); });
watch(() => props.assetId, () => { reset(); if (mounted) observe(); });
onBeforeUnmount(() => { active = false; reset(); });
</script>
<template>
  <figure ref="host" class="review-image">
    <button
      v-if="url"
      type="button"
      class="image-button"
      :aria-label="`放大图片：${caption || '文档图片'}`"
      @click="preview = true"
    >
      <img :src="url" :alt="caption || '文档图片'" @error="decodeFailed" />
      <span class="image-hint">点击放大</span>
    </button>
    <div v-else class="image-state" role="status">
      <p>{{ loading ? "图片加载中…" : error || "图片等待加载…" }}</p>
      <button
        v-if="retryable && !loading"
        type="button"
        class="retry-button"
        @click="load"
      >
        重试加载图片
      </button>
    </div>
    <figcaption v-if="caption">{{ caption }}</figcaption>
    <ElImageViewer
      v-if="preview && url"
      :url-list="[url]"
      teleported
      hide-on-click-modal
      @close="preview = false"
    />
  </figure>
</template>
<style scoped>
.review-image { margin: 12px 0; min-height: 48px; }
img { display: block; max-width: 100%; max-height: 600px; object-fit: contain; }
.image-button { position: relative; display: block; max-width: 100%; padding: 0; border: 1px solid var(--border-subtle); border-radius: var(--radius-sm); background: var(--bg-subtle); cursor: zoom-in; overflow: hidden; }
.image-button:focus-visible { outline: 2px solid var(--brand-primary); outline-offset: 3px; }
.image-hint { position: absolute; right: 8px; bottom: 8px; padding: 3px 8px; border-radius: var(--radius-sm); background: var(--bg-surface); color: var(--text-secondary); font-size: 12px; }
.image-state { padding: 12px; border: 1px dashed var(--border-subtle); border-radius: var(--radius-sm); }
.retry-button { padding: 4px 0; border: 0; color: var(--brand-primary); background: transparent; cursor: pointer; }
figcaption, p { color: var(--text-secondary); font-size: 12px; }
</style>

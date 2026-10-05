<script setup lang="ts">
import { computed } from "vue";
import { useAdminStore } from "@/composables/adminStore";
import type { BuildInfo } from "@/types/admin";
const store = useAdminStore();
const frontend: BuildInfo = import.meta.env.APP_BUILD_INFO;
const backend = computed(() => store.hasServiceCredential ? store.systemInfo?.build : undefined);
function label(build?: BuildInfo) { return build?.commit ? `${build.commit.slice(0, 8)}${build.dirty ? ' · 未提交改动' : ''}` : '未标注'; }
const mismatch = computed(() => !!frontend.commit && !!backend.value?.commit && frontend.commit !== backend.value.commit);
</script>
<template>
  <details class="runtime-version">
    <summary>运行版本<span v-if="mismatch" class="version-warning">前后端不一致</span></summary>
    <dl>
      <dt>前端构建</dt><dd :title="frontend.commit || ''">{{ label(frontend) }}</dd>
      <dt>构建时间</dt><dd>{{ frontend.built_at || '未标注' }}</dd>
      <dt>后端构建</dt><dd :title="backend?.commit || ''">{{ label(backend) }}</dd>
      <dt>构建时间</dt><dd>{{ backend?.built_at || '未标注' }}</dd>
    </dl>
    <p v-if="mismatch" class="version-warning">请核对镜像重建、容器替换及浏览器缓存；不同提交不一定不兼容。</p>
    <p v-else-if="!frontend.commit || !backend?.commit">未标注版本不能确认是否为最新版。部署时请使用带版本标记的构建流程。</p>
    <p v-else-if="frontend.dirty || backend?.dirty">包含未提交改动，提交号相同也不保证代码完全一致。</p>
    <p v-else>提交号可用于核对部署；不代表已通过生产验收。</p>
  </details>
</template>
<style scoped>
.runtime-version { margin: 16px; padding: 12px 0; border-top: 1px solid var(--sidebar-border); font-size: 12px; color: var(--sidebar-text); max-height: 40vh; overflow-y: auto; flex-shrink: 0; }
summary { cursor: pointer; }
dl { display: grid; gap: 4px; margin: 10px 0; }
dt { color: var(--sidebar-text); }
dd { margin: 0 0 6px; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; color: var(--sidebar-text-hover); }
p { line-height: 1.6; }
.version-warning { color: var(--warning-bg); margin-left: 4px; }
</style>

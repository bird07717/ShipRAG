<script setup lang="ts">
import { computed } from "vue";

/*
 * Unified status badge for the whole console.
 * Maps backend enum statuses to semantic tones; unknown values render as neutral.
 */
const props = defineProps<{
  status: string;
}>();

type Tone = "success" | "processing" | "warning" | "danger" | "neutral";

const statusMap: Record<string, { label: string; tone: Tone }> = {
  ACTIVE: { label: "线上生效", tone: "success" },
  READY: { label: "已就绪", tone: "success" },
  READY_TO_PUBLISH: { label: "可发布", tone: "success" },
  APPROVED: { label: "审核通过", tone: "success" },
  PUBLISHED: { label: "已发布", tone: "success" },
  COMPLETED: { label: "已完成", tone: "success" },
  SUCCEEDED: { label: "已完成", tone: "success" },
  ENABLED: { label: "已启用", tone: "success" },
  INDEXED: { label: "已索引", tone: "success" },
  OK: { label: "正常", tone: "success" },
  BUILDING: { label: "构建中", tone: "processing" },
  RUNNING: { label: "运行中", tone: "processing" },
  VALIDATING: { label: "验证中", tone: "processing" },
  UPDATING: { label: "更新中", tone: "processing" },
  PARSING: { label: "解析中", tone: "processing" },
  CHUNKING: { label: "分块中", tone: "processing" },
  EMBEDDING: { label: "向量化中", tone: "processing" },
  PROCESSING_IMAGES: { label: "处理图片", tone: "processing" },
  UPLOADING: { label: "上传中", tone: "processing" },
  STORED: { label: "已存储", tone: "warning" },
  DEPRECATED: { label: "已废弃", tone: "warning" },
  PENDING: { label: "待处理", tone: "warning" },
  VALIDATION_FAILED: { label: "验证失败", tone: "danger" },
  REJECTED: { label: "退回修改", tone: "danger" },
  NOT_READY: { label: "未就绪", tone: "warning" },
  DRAFT: { label: "草稿", tone: "neutral" },
  CREATE: { label: "新增", tone: "success" },
  UPDATE: { label: "更新", tone: "warning" },
  NONE: { label: "仅供参考", tone: "neutral" },
  FALLBACK: { label: "保守生成", tone: "warning" },
  LLM: { label: "模型生成", tone: "processing" },
  MANUAL: { label: "人工修改", tone: "neutral" },
  PAUSED: { label: "已暂停", tone: "warning" },
  RUNTIME: { label: "运行时", tone: "neutral" },
  FAILED: { label: "失败", tone: "danger" },
  CANCELLED: { label: "已取消", tone: "neutral" },
  ERROR: { label: "错误", tone: "danger" },
  DISABLED: { label: "已停用", tone: "neutral" },
};

const resolved = computed(() => {
  const key = (props.status ?? "").toUpperCase();
  return statusMap[key] ?? { label: props.status || "—", tone: "neutral" as Tone };
});
</script>

<template>
  <span class="status-badge" :class="resolved.tone">{{ resolved.label }}</span>
</template>

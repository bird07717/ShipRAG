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
  ACTIVE: { label: "Active", tone: "success" },
  READY: { label: "Ready", tone: "success" },
  COMPLETED: { label: "Completed", tone: "success" },
  SUCCEEDED: { label: "Completed", tone: "success" },
  ENABLED: { label: "Enabled", tone: "success" },
  INDEXED: { label: "Indexed", tone: "success" },
  OK: { label: "Ok", tone: "success" },
  BUILDING: { label: "Building", tone: "processing" },
  RUNNING: { label: "Running", tone: "processing" },
  UPDATING: { label: "Updating", tone: "processing" },
  PARSING: { label: "Parsing", tone: "processing" },
  CHUNKING: { label: "Chunking", tone: "processing" },
  EMBEDDING: { label: "Embedding", tone: "processing" },
  PROCESSING_IMAGES: { label: "Processing Images", tone: "processing" },
  UPLOADING: { label: "Uploading", tone: "processing" },
  STORED: { label: "Stored", tone: "warning" },
  DEPRECATED: { label: "Deprecated", tone: "warning" },
  PENDING: { label: "Pending", tone: "warning" },
  DRAFT: { label: "Draft", tone: "neutral" },
  PAUSED: { label: "Paused", tone: "warning" },
  FAILED: { label: "Failed", tone: "danger" },
  CANCELLED: { label: "Cancelled", tone: "danger" },
  ERROR: { label: "Error", tone: "danger" },
  DISABLED: { label: "Disabled", tone: "neutral" },
};

const resolved = computed(() => {
  const key = (props.status ?? "").toUpperCase();
  return statusMap[key] ?? { label: props.status || "—", tone: "neutral" as Tone };
});
</script>

<template>
  <span class="status-badge" :class="resolved.tone">{{ resolved.label }}</span>
</template>

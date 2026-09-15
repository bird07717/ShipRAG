<script setup lang="ts">
import { computed, reactive, watch } from "vue";
import { ElMessageBox } from "element-plus";

import EmptyState from "@/components/ui/EmptyState.vue";
import { useAdminStore } from "@/composables/adminStore";
import type {
  RagDocumentCardPayload,
  RagDocumentCardType,
} from "@/types/admin";

const store = useAdminStore();

type TextListKey =
  | "components"
  | "tasks"
  | "applicable_questions"
  | "prerequisites"
  | "key_topics"
  | "exclusions";

const form = reactive({
  document_type: "MIXED" as RagDocumentCardType,
  summary: "",
  components: "",
  tasks: "",
  applicable_questions: "",
  prerequisites: "",
  key_topics: "",
  exclusions: "",
  has_procedure: false,
  has_images: false,
});

const card = computed(() => store.ragDocumentCard);
const actionKey = computed(
  () => `rag-document-card-save:${store.selectedDocument?.id ?? ""}`,
);
const generationTask = computed(() => {
  const indexId = card.value?.index_id || store.documentInspectionIndexId;
  return indexId ? store.latestRagDocumentCardTasks[indexId] : undefined;
});
const generating = computed(() => {
  const actionRunning = Boolean(
    store.actionLoading[
      `rag-document-card-generate:${store.selectedDocument?.id ?? ""}`
    ],
  );
  return (
    actionRunning ||
    ["QUEUED", "RUNNING"].includes(generationTask.value?.status ?? "")
  );
});

function join(values: string[] | undefined): string {
  return (values ?? []).join("\n");
}

function split(value: string): string[] {
  return [
    ...new Set(
      value
        .split("\n")
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  ];
}

function sync(payload: RagDocumentCardPayload | null | undefined): void {
  if (!payload) return;
  form.document_type = payload.document_type;
  form.summary = payload.summary;
  for (const key of [
    "components",
    "tasks",
    "applicable_questions",
    "prerequisites",
    "key_topics",
    "exclusions",
  ] as TextListKey[]) {
    form[key] = join(payload[key]);
  }
  form.has_procedure = payload.has_procedure;
  form.has_images = payload.has_images;
}

watch(() => card.value?.card_payload, sync, { immediate: true });
watch(
  () => form.document_type,
  (value) => {
    if (value === "PROCEDURE") form.has_procedure = true;
  },
);

function payload(): RagDocumentCardPayload {
  return {
    document_type: form.document_type,
    summary: form.summary.trim(),
    components: split(form.components),
    tasks: split(form.tasks),
    applicable_questions: split(form.applicable_questions),
    prerequisites: split(form.prerequisites),
    key_topics: split(form.key_topics),
    exclusions: split(form.exclusions),
    has_procedure: form.has_procedure,
    has_images: form.has_images,
  };
}

async function regenerate(): Promise<void> {
  const current = card.value;
  if (current?.review_status === "CONFIRMED") {
    try {
      await ElMessageBox.confirm(
        "当前卡片已人工确认。重新生成成功后会替换为待确认草稿；生成失败时保留当前卡片。",
        "重新生成已确认卡片",
        {
          type: "warning",
          confirmButtonText: "确认重新生成",
          cancelButtonText: "取消",
        },
      );
    } catch {
      return;
    }
  }
  await store.generateRagDocumentCards(
    store.selectedDocument?.id,
    current?.review_status === "CONFIRMED",
  );
}
</script>

<template>
  <div class="card-editor">
    <div class="card-heading">
      <div>
        <h4>RAG 文档理解</h4>
        <p class="muted">
          仅用于后续 RAG 文档级检索，与 Support Profile 独立。
        </p>
      </div>
      <div v-if="card" class="status-tags">
        <el-tag
          size="small"
          :type="
            card.generation_status === 'READY'
              ? 'success'
              : card.generation_status === 'FAILED'
                ? 'danger'
                : 'warning'
          "
        >
          {{ card.generation_status }}
        </el-tag>
        <el-tag
          size="small"
          :type="
            card.review_status === 'CONFIRMED'
              ? 'success'
              : card.review_status === 'REJECTED'
                ? 'danger'
                : 'warning'
          "
        >
          {{ card.review_status }}
        </el-tag>
        <span class="mono muted">rev {{ card.revision }}</span>
      </div>
    </div>

    <el-alert
      v-if="card?.error_message"
      :title="card.error_message"
      type="warning"
      :closable="false"
    />

    <div v-if="generating && generationTask" class="generation-progress">
      <span class="muted">
        {{ generationTask.stage }} · {{ generationTask.progress }}%
      </span>
      <el-progress :percentage="generationTask.progress" :stroke-width="7" />
    </div>

    <EmptyState
      v-if="!card || !card.card_payload"
      title="尚无 RAG 文档理解卡片"
      description="可对当前索引中的该文档提交生成任务；任务完成后再人工检查。"
    >
      <template #action>
        <el-button type="primary" :loading="generating" @click="regenerate">
          生成卡片
        </el-button>
      </template>
    </EmptyState>

    <el-form v-else label-position="top" class="card-form">
      <div class="two-columns">
        <el-form-item label="文档类型">
          <el-select v-model="form.document_type">
            <el-option label="操作流程（PROCEDURE）" value="PROCEDURE" />
            <el-option label="参数参考（REFERENCE）" value="REFERENCE" />
            <el-option
              label="故障排查（TROUBLESHOOTING）"
              value="TROUBLESHOOTING"
            />
            <el-option label="概览（OVERVIEW）" value="OVERVIEW" />
            <el-option label="混合（MIXED）" value="MIXED" />
          </el-select>
        </el-form-item>
        <el-form-item label="结构特征">
          <el-checkbox v-model="form.has_procedure">包含操作步骤</el-checkbox>
          <el-checkbox v-model="form.has_images" disabled>
            包含图片（索引识别）
          </el-checkbox>
        </el-form-item>
      </div>
      <el-form-item label="文档任务摘要">
        <el-input
          v-model="form.summary"
          type="textarea"
          :rows="2"
          maxlength="500"
          show-word-limit
        />
      </el-form-item>
      <el-form-item label="适用任务（每行一项）">
        <el-input v-model="form.tasks" type="textarea" :rows="3" />
      </el-form-item>
      <el-form-item label="用户可能提问（每行一项）">
        <el-input
          v-model="form.applicable_questions"
          type="textarea"
          :rows="3"
        />
      </el-form-item>
      <div class="two-columns">
        <el-form-item label="组件（每行一项）">
          <el-input v-model="form.components" type="textarea" :rows="3" />
        </el-form-item>
        <el-form-item label="关键主题（每行一项）">
          <el-input v-model="form.key_topics" type="textarea" :rows="3" />
        </el-form-item>
        <el-form-item label="适用前提（每行一项）">
          <el-input v-model="form.prerequisites" type="textarea" :rows="3" />
        </el-form-item>
        <el-form-item label="排除范围（每行一项）">
          <el-input v-model="form.exclusions" type="textarea" :rows="3" />
        </el-form-item>
      </div>
      <div class="card-actions">
        <el-button
          :loading="Boolean(store.actionLoading[actionKey])"
          @click="store.saveRagDocumentCard(payload())"
        >
          保存为待确认
        </el-button>
        <el-button
          type="success"
          :disabled="card.generation_status !== 'READY'"
          @click="store.reviewRagDocumentCard('CONFIRMED')"
        >
          确认
        </el-button>
        <el-button
          type="danger"
          plain
          :disabled="card.generation_status !== 'READY'"
          @click="store.reviewRagDocumentCard('REJECTED')"
        >
          拒绝
        </el-button>
        <el-button link :loading="generating" @click="regenerate">
          重新生成
        </el-button>
      </div>
    </el-form>
  </div>
</template>

<style scoped>
.card-editor {
  display: grid;
  gap: 14px;
}
.generation-progress {
  display: grid;
  gap: 6px;
}
.card-heading {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}
.card-heading h4,
.card-heading p {
  margin: 0;
}
.card-heading p {
  margin-top: 4px;
}
.status-tags,
.card-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.card-form {
  max-height: 500px;
  padding-right: 4px;
  overflow: auto;
}
.two-columns {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 12px;
}
@media (max-width: 680px) {
  .two-columns {
    grid-template-columns: 1fr;
  }
}
</style>

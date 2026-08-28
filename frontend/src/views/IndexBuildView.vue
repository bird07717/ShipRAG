<script setup lang="ts">
import { computed, ref } from "vue";
import { ElMessageBox } from "element-plus";

import { useAdminStore } from "@/composables/adminStore";
import type { IndexTask, KnowledgeIndex } from "@/types/admin";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import EmptyState from "@/components/ui/EmptyState.vue";
import CodeBlock from "@/components/ui/CodeBlock.vue";

const store = useAdminStore();

const detailIndex = ref<KnowledgeIndex | null>(null);
const detailOpen = ref(false);

const PIPELINE_STAGES = ["PARSING", "PROCESSING_IMAGES", "CHUNKING", "EMBEDDING", "COMPLETED"];
const STAGE_LABELS: Record<string, string> = {
  PARSING: "解析文档",
  PROCESSING_IMAGES: "图片处理",
  CHUNKING: "分块",
  EMBEDDING: "向量化",
  COMPLETED: "完成",
};

async function openDetail(index: KnowledgeIndex): Promise<void> {
  detailIndex.value = index;
  detailOpen.value = true;
}

function isRagActive(index: KnowledgeIndex): boolean {
  const activeId =
    store.selectedKnowledge?.rag_active_index_id ?? store.selectedKnowledge?.active_index_id;
  return Boolean(activeId && activeId === index.id);
}

function rollbackable(index: KnowledgeIndex): boolean {
  return (
    ["READY", "DEPRECATED"].includes(index.status) ||
    (index.status === "ACTIVE" && !isRagActive(index))
  );
}

async function rollbackRag(index: KnowledgeIndex): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `显式将 RAG 在线索引回滚到 Index v${index.version}（${store.formatDate(index.finished_at)} 构建）。` +
        "新会话将使用该版本；既有会话继续使用各自冻结的版本。Support Release 与 Support 指针不受影响。",
      "回滚 RAG Index",
      { type: "warning", confirmButtonText: "确认回滚", cancelButtonText: "取消" },
    );
  } catch {
    return;
  }
  await store.rollbackRagIndex(index.id);
}

const detailTask = computed<IndexTask | undefined>(() => {
  const index = detailIndex.value;
  return index ? store.latestIndexTasks[index.id] : undefined;
});

const detailStageIndex = computed(() => {
  const stage = detailTask.value?.stage ?? "";
  if (detailIndex.value?.status === "ACTIVE" || detailIndex.value?.status === "READY") {
    return PIPELINE_STAGES.length;
  }
  if (detailIndex.value?.status === "FAILED") return -1;
  const idx = PIPELINE_STAGES.indexOf(stage);
  return idx >= 0 ? idx : 0;
});

function formatDuration(index: KnowledgeIndex): string {
  if (!index.finished_at) return "—";
  const started = new Date(index.created_at).getTime();
  const finished = new Date(index.finished_at).getTime();
  const seconds = Math.max(0, Math.round((finished - started) / 1000));
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function buildingDocumentSummary(index: KnowledgeIndex): string | null {
  if (index.status !== "BUILDING") return null;
  const metadata = store.latestIndexTasks[index.id]?.metadata ?? {};
  const total = Number(metadata.total_documents ?? metadata.document_count);
  const current = Number(metadata.current_document_position);
  if (Number.isFinite(total) && total > 0 && Number.isFinite(current) && current >= 0) {
    return `构建中 · 已处理 ${Math.min(current, total)}/${total} 份文档`;
  }
  if (Number.isFinite(total) && total > 0) return `构建中 · 共 ${total} 份文档`;
  return "构建中 · 正在等待任务统计";
}
</script>

<template>
  <div class="index-build">
    <div class="panel">
      <div class="panel-body toolbar-row">
        <div class="toolbar">
          <el-select
            v-model="store.selectedKnowledgeId"
            placeholder="选择知识库"
            @change="store.selectKnowledge()"
          >
            <el-option
              v-for="kb in store.knowledgeBases"
              :key="kb.id"
              :label="kb.name"
              :value="kb.id"
            />
          </el-select>
          <span class="muted candidate-note">这里只处理 RAG 技术构建、任务诊断和回收；技术支持（Support）审核请前往 Profile 审核（Profile Review）。</span>
          <div class="spacer" />
          <el-button :disabled="!store.selectedKnowledgeId" @click="store.gcIndexes()">
            回收旧索引
          </el-button>
          <el-button
            type="primary"
            :disabled="!store.selectedKnowledgeId"
            @click="store.buildIndex()"
          >
            新建构建
          </el-button>
        </div>
      </div>

      <el-table
        v-if="store.indexes.length"
        :data="store.indexes"
        @row-click="(row: any) => openDetail(row)"
      >
        <el-table-column label="索引" min-width="145">
          <template #default="scope">
            <div class="cell-primary">
              <b>Index v{{ scope.row.version }}</b>
              <small v-if="buildingDocumentSummary(scope.row)">{{ buildingDocumentSummary(scope.row) }}</small>
              <small v-else>{{ scope.row.document_count }} 份文档 · {{ scope.row.chunk_count }} 个切片</small>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="scope"><StatusBadge :status="scope.row.status" /></template>
        </el-table-column>
        <el-table-column label="进度" width="120">
          <template #default="scope">
            <el-progress
              v-if="scope.row.status === 'BUILDING' && store.latestIndexTasks[scope.row.id]"
              :percentage="store.latestIndexTasks[scope.row.id]?.progress ?? 0"
              :stroke-width="6"
            />
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="耗时" width="80">
          <template #default="scope">{{ formatDuration(scope.row) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="180" align="right">
          <template #default="scope">
            <el-button
              v-if="rollbackable(scope.row)"
              link
              type="warning"
              :loading="store.isActionLoading(`rollback-rag-index:${scope.row.id}`)"
              @click.stop="rollbackRag(scope.row)"
            >
              回滚 RAG
            </el-button>
            <el-button
              v-if="scope.row.status === 'DEPRECATED' || scope.row.status === 'FAILED'"
              link
              type="danger"
              @click.stop="store.deleteIndex(scope.row.id)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <EmptyState
        v-else
        title="暂无索引"
        description="该知识库还没有索引。上传文档或点击「新建构建」开始第一次构建。"
      />
    </div>

    <!-- Build detail drawer -->
    <el-drawer
      v-model="detailOpen"
      :title="detailIndex ? `索引（Index）v${detailIndex.version}` : '构建详情'"
      size="560px"
    >
      <template v-if="detailIndex">
        <div class="detail-head">
          <StatusBadge :status="detailIndex.status" />
          <span class="muted mono">{{ detailIndex.id }}</span>
        </div>

        <div class="detail-metrics">
          <div class="detail-metric">
            <span>文档数（Documents）</span>
            <strong>{{ detailIndex.document_count }}</strong>
          </div>
          <div class="detail-metric">
            <span>元素数（Elements）</span>
            <strong>{{ detailIndex.element_count }}</strong>
          </div>
          <div class="detail-metric">
            <span>切片数（Chunks）</span>
            <strong>{{ detailIndex.chunk_count }}</strong>
          </div>
        </div>

        <el-alert
          v-if="buildingDocumentSummary(detailIndex)"
          :title="buildingDocumentSummary(detailIndex) + '；文档、元素和切片总数会在技术校验完成后一次性写入。'"
          type="info"
          :closable="false"
          show-icon
          class="detail-alert"
        />

        <h4 class="section-label">构建流程（Pipeline）</h4>
        <el-progress
          v-if="detailTask"
          :percentage="detailTask.progress"
          :stroke-width="6"
          class="pipeline-progress"
        />
        <ol class="pipeline">
          <li
            v-for="(stage, index) in PIPELINE_STAGES"
            :key="stage"
            :class="{
              done: detailStageIndex > index,
              current: detailStageIndex === index,
              failed: detailStageIndex === -1 && index === PIPELINE_STAGES.length - 2,
            }"
          >
            <span class="stage-dot" />
            <span class="stage-name">{{ STAGE_LABELS[stage] }}</span>
            <span v-if="detailTask?.stage === stage && detailStageIndex === index" class="stage-live">
              {{ detailTask.progress }}%
            </span>
          </li>
        </ol>

        <el-alert
          v-if="detailIndex.error_message"
          :title="detailIndex.error_message"
          type="error"
          :closable="false"
          show-icon
          class="detail-alert"
        />

        <h4 class="section-label">构建信息</h4>
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="向量模型（Embedding Model）">
            <span class="mono">{{ detailIndex.embedding_model_name }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="BM25 引擎（BM25 Engine）">
            <span class="mono">{{ detailIndex.bm25_engine }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="构建原因">
            {{ detailIndex.build_reason }}
          </el-descriptions-item>
          <el-descriptions-item label="RAG 生效策略">
            构建完成后自动切换
          </el-descriptions-item>
          <el-descriptions-item label="开始时间">
            {{ store.formatDate(detailIndex.created_at) }}
          </el-descriptions-item>
          <el-descriptions-item label="完成时间">
            {{ store.formatDate(detailIndex.finished_at) }}
          </el-descriptions-item>
        </el-descriptions>

        <template v-if="detailTask">
          <h4 class="section-label">任务日志（Task Log）</h4>
          <CodeBlock :code="store.formatJson(detailTask)" max-height="260px" />
        </template>

        <el-alert
          title="此页面不处理 Support Profile。构建完成并通过技术校验后，RAG 会自动切换到该索引；请在 Profile 审核（Profile Review）中审核和发布技术支持（Support）配置。"
          type="info"
          :closable="false"
          show-icon
          class="detail-alert"
        />

        <div class="detail-actions">
          <el-button
            v-if="rollbackable(detailIndex)"
            type="warning"
            plain
            :loading="store.isActionLoading(`rollback-rag-index:${detailIndex.id}`)"
            @click="rollbackRag(detailIndex)"
          >
            回滚 RAG 到此版本
          </el-button>
          <el-button
            v-if="detailIndex.status === 'DEPRECATED' || detailIndex.status === 'FAILED'"
            type="danger"
            plain
            @click="store.deleteIndex(detailIndex.id)"
          >
            删除
          </el-button>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.index-build { min-width: 0; }
.index-build > .panel { min-width: 0; overflow: hidden; }
.index-build :deep(.el-table) { width: 100%; max-width: 100%; }
.toolbar-row {
  padding-bottom: 0;
}

.candidate-note {
  font-size: 12px;
}

:deep(.el-table__row) {
  cursor: pointer;
}

.detail-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

.detail-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 18px;
}

.detail-metric {
  display: grid;
  gap: 4px;
  padding: 12px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  background: var(--bg-subtle);
}

.detail-metric span {
  color: var(--text-tertiary);
  font-size: 12px;
}

@media (max-width: 760px) {
  .toolbar { flex-wrap: wrap; }
  .toolbar .spacer { display: none; }
  .candidate-note { width: 100%; order: 3; }
}

.detail-metric strong {
  color: var(--text-primary);
  font-size: 18px;
  font-variant-numeric: tabular-nums;
}

.section-label {
  margin: 18px 0 10px;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 600;
}

.pipeline-progress {
  margin-bottom: 12px;
}

.pipeline {
  display: grid;
  gap: 2px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.pipeline li {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 4px 7px 6px;
  border-radius: var(--radius-sm);
  color: var(--text-tertiary);
  font-size: 13px;
}

.pipeline li:not(:last-child)::after {
  position: absolute;
  top: 26px;
  bottom: -10px;
  left: 11px;
  width: 1px;
  background: var(--border-default);
  content: "";
}

.pipeline li.done {
  color: var(--text-secondary);
}

.pipeline li.done .stage-dot {
  border-color: var(--brand-primary);
  background: var(--brand-primary);
}

.pipeline li.current {
  color: var(--text-primary);
  background: var(--bg-hover);
  font-weight: 500;
}

.pipeline li.current .stage-dot {
  border-color: var(--brand-primary);
  box-shadow: 0 0 0 3px var(--brand-subtle);
}

.pipeline li.failed .stage-dot {
  border-color: var(--error);
  background: var(--error);
}

.stage-dot {
  width: 10px;
  height: 10px;
  flex-shrink: 0;
  border: 1.5px solid var(--border-strong);
  border-radius: 50%;
  background: var(--bg-surface);
}

.stage-live {
  margin-left: auto;
  color: var(--brand-primary);
  font-family: var(--font-mono);
  font-size: 11px;
}

.detail-alert {
  margin-top: 14px;
}

.detail-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 18px;
}
</style>

<script setup lang="ts">
import { computed, ref } from "vue";

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

function openDetail(index: KnowledgeIndex): void {
  detailIndex.value = index;
  detailOpen.value = true;
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
          <el-switch
            v-model="store.autoActivate"
            active-text="完成后自动激活"
            inactive-text="人工验证"
          />
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
        <el-table-column label="Task" min-width="190">
          <template #default="scope">
            <div class="cell-primary">
              <b>Index v{{ scope.row.version }}</b>
              <small class="mono">{{ scope.row.id }}</small>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="Status" width="120">
          <template #default="scope"><StatusBadge :status="scope.row.status" /></template>
        </el-table-column>
        <el-table-column label="Progress" width="150">
          <template #default="scope">
            <el-progress
              v-if="scope.row.status === 'BUILDING' && store.latestIndexTasks[scope.row.id]"
              :percentage="store.latestIndexTasks[scope.row.id]?.progress ?? 0"
              :stroke-width="6"
            />
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="document_count" label="Docs" width="80" />
        <el-table-column prop="chunk_count" label="Chunks" width="90" />
        <el-table-column label="Embedding" min-width="130" show-overflow-tooltip>
          <template #default="scope">
            <span class="mono">{{ scope.row.embedding_model_name }}</span>
          </template>
        </el-table-column>
        <el-table-column label="Started" min-width="150">
          <template #default="scope">{{ store.formatDate(scope.row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="Duration" width="90">
          <template #default="scope">{{ formatDuration(scope.row) }}</template>
        </el-table-column>
        <el-table-column label="" width="90" align="right">
          <template #default="scope">
            <el-button
              v-if="scope.row.status === 'READY'"
              link
              type="primary"
              @click.stop="store.activateIndex(scope.row.id)"
            >
              激活
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
      :title="detailIndex ? `Index v${detailIndex.version}` : '构建详情'"
      size="560px"
    >
      <template v-if="detailIndex">
        <div class="detail-head">
          <StatusBadge :status="detailIndex.status" />
          <span class="muted mono">{{ detailIndex.id }}</span>
        </div>

        <div class="detail-metrics">
          <div class="detail-metric">
            <span>Documents</span>
            <strong>{{ detailIndex.document_count }}</strong>
          </div>
          <div class="detail-metric">
            <span>Elements</span>
            <strong>{{ detailIndex.element_count }}</strong>
          </div>
          <div class="detail-metric">
            <span>Chunks</span>
            <strong>{{ detailIndex.chunk_count }}</strong>
          </div>
        </div>

        <h4 class="section-label">Pipeline</h4>
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
          <el-descriptions-item label="Embedding Model">
            <span class="mono">{{ detailIndex.embedding_model_name }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="BM25 Engine">
            <span class="mono">{{ detailIndex.bm25_engine }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="构建原因">
            {{ detailIndex.build_reason }}
          </el-descriptions-item>
          <el-descriptions-item label="完成后激活">
            {{ detailIndex.activate_on_success ? "是" : "否" }}
          </el-descriptions-item>
          <el-descriptions-item label="开始时间">
            {{ store.formatDate(detailIndex.created_at) }}
          </el-descriptions-item>
          <el-descriptions-item label="完成时间">
            {{ store.formatDate(detailIndex.finished_at) }}
          </el-descriptions-item>
        </el-descriptions>

        <template v-if="detailTask">
          <h4 class="section-label">Task Log</h4>
          <CodeBlock :code="store.formatJson(detailTask)" max-height="260px" />
        </template>

        <div class="detail-actions">
          <el-button
            v-if="detailIndex.status === 'READY'"
            type="primary"
            @click="store.activateIndex(detailIndex.id)"
          >
            激活此快照
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
.toolbar-row {
  padding-bottom: 0;
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
  justify-content: flex-end;
  gap: 8px;
  margin-top: 18px;
}
</style>

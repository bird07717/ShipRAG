<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ElMessageBox } from "element-plus";

import { useAdminStore } from "@/composables/adminStore";
import type { IndexTask, KnowledgeIndex, KnowledgeRelease, ProfileDraft } from "@/types/admin";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import EmptyState from "@/components/ui/EmptyState.vue";
import CodeBlock from "@/components/ui/CodeBlock.vue";

const store = useAdminStore();

onMounted(() => {
  /* Documents > Profile & Alias sends the reviewer straight to the
   * candidate's build drawer instead of a raw view switch. */
  const requested = store.requestedIndexDetailId;
  if (!requested) return;
  store.requestedIndexDetailId = "";
  const target = store.indexes.find((item) => item.id === requested);
  if (target) void openDetail(target);
});

const detailIndex = ref<KnowledgeIndex | null>(null);
const detailOpen = ref(false);
const editingDraft = ref<ProfileDraft | null>(null);
const editDraftOpen = ref(false);
const editForm = ref({
  problemCode: "",
  canonicalQuestion: "",
  aliases: "",
  keywords: "",
  routingSummary: "",
});

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
  await store.loadReleaseForIndex(index.id);
}

const detailRelease = computed<KnowledgeRelease | undefined>(() => {
  const index = detailIndex.value;
  if (!index) return undefined;
  const release = store.releaseForIndex(index.id);
  return release ? (store.releaseDetails[release.id] ?? release) : undefined;
});

async function approveDraft(draft: ProfileDraft): Promise<void> {
  await store.updateProfileDraft(draft.id, { status: "APPROVED" });
}

function openDraftEditor(draft: ProfileDraft): void {
  editingDraft.value = draft;
  editForm.value = {
    problemCode: draft.problem_code,
    canonicalQuestion: draft.canonical_question,
    aliases: draft.aliases.join("\n"),
    keywords: draft.keywords.join("\n"),
    routingSummary: draft.routing_summary,
  };
  editDraftOpen.value = true;
}

function splitLines(value: string): string[] {
  return value
    .split(/\n|,/)
    .map((item) => item.trim())
    .filter(Boolean);
}

async function saveDraft(): Promise<void> {
  const draft = editingDraft.value;
  if (!draft) return;
  await store.updateProfileDraft(draft.id, {
    problem_code: editForm.value.problemCode.trim(),
    canonical_question: editForm.value.canonicalQuestion.trim(),
    aliases: splitLines(editForm.value.aliases),
    keywords: splitLines(editForm.value.keywords),
    routing_summary: editForm.value.routingSummary.trim(),
  });
  if (!store.error) editDraftOpen.value = false;
}

async function publishRelease(release: KnowledgeRelease): Promise<void> {
  try {
    await ElMessageBox.confirm(
      "确认后将原子切换 RAG 索引与所有已审核的 Profile，新的会话会立即使用该版本。",
      "发布候选",
      { type: "warning", confirmButtonText: "确认发布", cancelButtonText: "继续审核" },
    );
  } catch {
    return;
  }
  await store.publishKnowledgeRelease(release.id);
  detailOpen.value = false;
}

async function refreshProfiles(index: KnowledgeIndex): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `将为 Index v${index.version} 的候选文档重新生成 Profile 草稿。未发布草稿会被覆盖，完成后请重新审核。`,
      "重建 Profile 草稿",
      { type: "warning", confirmButtonText: "提交后台任务", cancelButtonText: "取消" },
    );
  } catch {
    return;
  }
  await store.refreshProfileDrafts(index.id);
}

const detailTask = computed<IndexTask | undefined>(() => {
  const index = detailIndex.value;
  return index ? store.latestIndexTasks[index.id] : undefined;
});

const detailProfileTask = computed<IndexTask | undefined>(() => {
  const index = detailIndex.value;
  return index ? store.latestProfileTasks[index.id] : undefined;
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
          <span class="muted candidate-note">构建完成后须在此审核发布，不自动影响线上</span>
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
              <small>{{ scope.row.document_count }} 份文档 · {{ scope.row.chunk_count }} 个切片</small>
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
        <el-table-column label="审核发布" width="120" align="right">
          <template #default="scope">
            <el-button
              v-if="scope.row.status === 'READY'"
              link
              type="primary"
              @click.stop="openDetail(scope.row)"
            >
              审核并发布
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

        <template v-if="detailRelease">
          <h4 class="section-label">候选发布</h4>
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="候选状态">
              <StatusBadge :status="detailRelease.status" />
            </el-descriptions-item>
            <el-descriptions-item label="Profile 草稿">
              {{ detailRelease.approved_count }} / {{ detailRelease.draft_count }} 已审核
            </el-descriptions-item>
          </el-descriptions>
          <el-alert
            v-if="detailRelease.generation_error"
            :title="detailRelease.generation_error"
            type="warning"
            :closable="false"
            show-icon
            class="detail-alert"
          />
          <el-descriptions
            v-if="detailProfileTask"
            :column="2"
            border
            size="small"
            class="profile-task"
          >
            <el-descriptions-item label="Profile 任务">
              {{ detailProfileTask.task_type ?? "PROFILE" }} · {{ detailProfileTask.status }}
            </el-descriptions-item>
            <el-descriptions-item label="生成进度">
              {{ detailProfileTask.progress }}%
            </el-descriptions-item>
          </el-descriptions>
          <el-alert
            v-if="detailRelease.validation_result.review_required_count"
            :title="`仍有 ${detailRelease.validation_result.review_required_count} 条草稿待审核`"
            type="info"
            :closable="false"
            class="detail-alert"
          />
          <el-alert
            v-for="issue in detailRelease.validation_result.errors ?? []"
            :key="`${issue.code}-${issue.message}`"
            :title="`${issue.code}: ${issue.message}`"
            type="error"
            :closable="false"
            class="detail-alert"
          />
          <el-table
            v-if="detailRelease.drafts?.length"
            :data="detailRelease.drafts"
            size="small"
            class="draft-table"
          >
            <el-table-column
              prop="document_name"
              label="文档"
              min-width="130"
              show-overflow-tooltip
            />
            <el-table-column
              prop="canonical_question"
              label="标准问题"
              min-width="170"
              show-overflow-tooltip
            />
            <el-table-column label="质量" width="120">
              <template #default="scope">
                <el-tag
                  :type="scope.row.quality_status === 'PASS' || scope.row.quality_status === 'MANUAL_REVIEWED' ? 'success' : 'warning'"
                  size="small"
                >
                  {{ scope.row.quality_status }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="审核" width="100" align="right">
              <template #default="scope">
                <el-button
                  v-if="scope.row.status !== 'PUBLISHED'"
                  link
                  @click="openDraftEditor(scope.row)"
                >
                  编辑
                </el-button>
                <el-button
                  v-if="scope.row.status === 'DRAFT' && scope.row.quality_status !== 'REVIEW_REQUIRED' && scope.row.quality_status !== 'FALLBACK'"
                  link
                  type="primary"
                  :loading="store.isActionLoading(`profile-draft:${scope.row.id}`)"
                  @click="approveDraft(scope.row)"
                >
                  批准
                </el-button>
                <span v-else class="muted">{{ scope.row.status }}</span>
              </template>
            </el-table-column>
          </el-table>
          <p
            v-if="detailRelease.drafts?.some((draft) => draft.quality_status === 'REVIEW_REQUIRED' || draft.quality_status === 'FALLBACK')"
            class="muted quality-note"
          >
            质量待复核草稿须先编辑并保存，再单独批准；系统不会批量跳过该检查。
          </p>
        </template>
        <el-alert
          v-else-if="detailIndex.status === 'READY'"
          title="此旧索引没有发布候选，可通过旧式激活接口处理；新建索引将自动创建候选。"
          type="info"
          :closable="false"
          class="detail-alert"
        />

        <div class="detail-actions">
          <el-button
            v-if="detailRelease"
            :loading="store.isActionLoading(`validate-release:${detailRelease.id}`)"
            @click="store.validateKnowledgeRelease(detailRelease.id)"
          >
            发布前验证
          </el-button>
          <el-button
            v-if="detailRelease?.validation_result.publishable"
            type="primary"
            :loading="store.isActionLoading(`publish-release:${detailRelease.id}`)"
            @click="publishRelease(detailRelease)"
          >
            发布候选
          </el-button>
          <el-button
            v-if="detailRelease && detailIndex.status === 'READY'"
            :loading="store.isActionLoading(`refresh-profile:${detailIndex.id}`)"
            @click="refreshProfiles(detailIndex)"
          >
            重建 Profile 草稿
          </el-button>
          <el-button
            v-if="detailIndex.status === 'READY' && !detailRelease"
            type="primary"
            @click="store.activateIndex(detailIndex.id)"
          >
            激活旧式快照
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

    <el-dialog v-model="editDraftOpen" title="编辑 Profile 草稿" width="680px">
      <el-form label-position="top">
        <el-form-item label="问题编码">
          <el-input v-model="editForm.problemCode" maxlength="100" show-word-limit />
        </el-form-item>
        <el-form-item label="标准问题">
          <el-input v-model="editForm.canonicalQuestion" maxlength="500" show-word-limit />
        </el-form-item>
        <el-form-item label="Alias（每行一条）">
          <el-input v-model="editForm.aliases" type="textarea" :rows="4" />
        </el-form-item>
        <el-form-item label="关键词（每行一条）">
          <el-input v-model="editForm.keywords" type="textarea" :rows="3" />
        </el-form-item>
        <el-form-item label="路由摘要">
          <el-input
            v-model="editForm.routingSummary"
            type="textarea"
            :rows="4"
            maxlength="1000"
            show-word-limit
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDraftOpen = false">取消</el-button>
        <el-button
          type="primary"
          :loading="editingDraft ? store.isActionLoading(`profile-draft:${editingDraft.id}`) : false"
          @click="saveDraft"
        >
          保存草稿
        </el-button>
      </template>
    </el-dialog>
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
  justify-content: flex-end;
  gap: 8px;
  margin-top: 18px;
}
</style>

<script setup lang="ts">
import { computed, ref } from "vue";
import { CircleCheckFilled, CircleCloseFilled, Document, Loading, Search, UploadFilled } from "@element-plus/icons-vue";

import { useAdminStore } from "@/composables/adminStore";
import { randomUUID } from "@/utils/uuid";
import type {
  DocumentProfileStatus,
  ProfileQualityStatus,
} from "@/types/admin";
import DocumentPreview from "@/components/DocumentPreview.vue";
import RagDocumentCardEditor from "@/components/RagDocumentCardEditor.vue";
import EmptyState from "@/components/ui/EmptyState.vue";
import ListSkeleton from "@/components/ui/ListSkeleton.vue";
import StatusBadge from "@/components/ui/StatusBadge.vue";

const store = useAdminStore();

type UploadItem = {
  key: string;
  file: File;
  status: "PENDING" | "UPLOADING" | "DONE" | "FAILED" | "REJECTED";
  error: string;
};

/* 与后端 m2_docx_max_bytes / m2_pdf_max_bytes 保持同步 */
const DOCX_MAX_BYTES = 52_428_800;
const PDF_MAX_BYTES = 104_857_600;

const fileInput = ref<HTMLInputElement | null>(null);
const queue = ref<UploadItem[]>([]);
const uploading = ref(false);
const uploadError = ref("");
const autoBuild = ref(true);

const pendingItems = computed(() =>
  queue.value.filter((item) => item.status === "PENDING" || item.status === "FAILED"),
);
const batchSummary = computed(() => {
  if (uploading.value || !queue.value.length) return "";
  const done = queue.value.filter((item) => item.status === "DONE").length;
  const failed = queue.value.filter((item) => item.status === "FAILED").length;
  if (!done && !failed) return "";
  if (failed) return `已上传 ${done} 份，失败 ${failed} 份；失败项保留在队列中，可再次点击开始上传重试`;
  return `已上传 ${done} 份文档`;
});

const skippedDocuments = computed(() =>
  store.documents.filter((item) => item.last_build_status === "SKIPPED"),
);

const processingState = computed(() => {
  const building = store.buildingCount;
  if (building) return { label: `处理中 · ${building} 个构建`, type: "warning" as const };
  if (store.selectedKnowledge?.rebuild_required) {
    return { label: "等待后续构建", type: "warning" as const };
  }
  return { label: "处理完成", type: "success" as const };
});

const ragState = computed(() => {
  const index = store.activeIndex;
  return index
    ? { label: `在线 · Index v${index.version}`, type: "success" as const }
    : { label: "尚无可用索引", type: "info" as const };
});

const supportState = computed(() => {
  const summary = store.knowledgeProfileSummary;
  if (summary?.pending_count) return { label: `待上线 · ${summary.pending_count} 项属性`, type: "warning" as const };
  if (summary?.online_count) return { label: `已上线 · ${summary.online_count} 项属性`, type: "success" as const };
  return { label: "未配置（RAG 可用）", type: "info" as const };
});

const profileByDocument = computed(() =>
  Object.fromEntries((store.knowledgeProfileSummary?.profiles ?? []).map((item) => [item.document_id, item])),
);

const ragCardByDocument = computed(() =>
  Object.fromEntries(store.ragDocumentCards.map((item) => [item.document_id, item])),
);

const ragCardTask = computed(() => {
  const indexId = store.activeIndex?.id;
  return indexId ? store.latestRagDocumentCardTasks[indexId] : undefined;
});

const ragCardTaskActive = computed(() =>
  ["QUEUED", "RUNNING"].includes(ragCardTask.value?.status ?? ""),
);

const ragCardTaskCounts = computed(() => {
  const metadata = ragCardTask.value?.metadata ?? {};
  const generated = Number(metadata.generated_count ?? 0);
  const failed = Number(metadata.failed_count ?? 0);
  const total = Number(
    metadata.selected_count
      ?? metadata.document_count
      ?? store.ragDocumentCardReadiness?.expected_count
      ?? 0,
  );
  return {
    generated: Number.isFinite(generated) ? generated : 0,
    failed: Number.isFinite(failed) ? failed : 0,
    total: Number.isFinite(total) ? total : 0,
  };
});

const ragCardTaskProgressStatus = computed<"success" | "exception" | "warning" | undefined>(() => {
  const task = ragCardTask.value;
  if (!task) return undefined;
  if (task.status === "FAILED") return "exception";
  if (task.status === "CANCELLED") return "warning";
  if (task.status === "SUCCEEDED" && ragCardTaskCounts.value.failed === 0) return "success";
  if (task.status === "SUCCEEDED") return "warning";
  return undefined;
});

const ragCardTaskLabel = computed(() => {
  const task = ragCardTask.value;
  if (!task) return "";
  if (task.status === "QUEUED") return "等待生成";
  if (task.status === "RUNNING") return "正在生成";
  if (task.status === "FAILED") return "生成失败";
  if (task.status === "CANCELLED") return "已停止";
  return ragCardTaskCounts.value.failed > 0 ? "生成完成（含失败）" : "生成完成";
});

function ragCardLabel(documentId: string): string {
  const card = ragCardByDocument.value[documentId];
  if (!card) return "未生成";
  if (card.generation_status === "FAILED") return "生成失败";
  if (card.generation_status !== "READY") return "生成中";
  if (card.review_status === "CONFIRMED") return "已确认";
  if (card.review_status === "REJECTED") return "已拒绝";
  return "待确认";
}

function ragCardTagType(documentId: string): "success" | "warning" | "danger" | "info" {
  const card = ragCardByDocument.value[documentId];
  if (!card) return "info";
  if (card.generation_status === "FAILED" || card.review_status === "REJECTED") return "danger";
  if (card.generation_status === "READY" && card.review_status === "CONFIRMED") return "success";
  return "warning";
}

const profileLabels: Record<DocumentProfileStatus, string> = {
  NOT_GENERATED: "未生成", GENERATING: "生成中", PENDING_ONLINE: "待上线", ONLINE: "已上线",
  NEEDS_ATTENTION: "需处理", DISABLED: "已停用", FAILED: "失败",
};

function documentProfileStatus(documentId: string): DocumentProfileStatus {
  return profileByDocument.value[documentId]?.profile_status ?? "NOT_GENERATED";
}

function profileTagType(status: DocumentProfileStatus): "success" | "warning" | "danger" | "info" {
  if (status === "ONLINE") return "success";
  if (status === "PENDING_ONLINE" || status === "GENERATING") return "warning";
  return status === "FAILED" ? "danger" : "info";
}

function validateFile(file: File): string {
  const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
  if (extension !== "docx" && extension !== "pdf") return "仅支持 .docx / .pdf 文件";
  const limit = extension === "pdf" ? PDF_MAX_BYTES : DOCX_MAX_BYTES;
  if (file.size > limit) return "文件超过大小上限";
  return "";
}

function addFile(file: File): void {
  uploadError.value = "";
  const duplicate = queue.value.some(
    (item) =>
      item.file.name === file.name &&
      item.file.size === file.size &&
      item.file.lastModified === file.lastModified,
  );
  if (duplicate) return;
  const error = validateFile(file);
  queue.value.push({
    key: randomUUID(),
    file,
    status: error ? "REJECTED" : "PENDING",
    error,
  });
}

function pickFile(event: Event): void {
  const input = event.target as HTMLInputElement;
  for (const file of Array.from(input.files ?? [])) addFile(file);
  input.value = "";
}

function openPicker(): void {
  fileInput.value?.click();
}

function removeItem(item: UploadItem): void {
  if (uploading.value) return;
  queue.value = queue.value.filter((entry) => entry.key !== item.key);
}

function clearQueue(): void {
  if (uploading.value) return;
  queue.value = [];
}

function closeUploadDrawer(): void {
  if (uploading.value) return;
  store.uploadDrawerOpen = false;
}

async function confirmUpload(): Promise<void> {
  const items = [...pendingItems.value];
  if (!items.length || uploading.value) return;
  uploading.value = true;
  uploadError.value = "";
  try {
    for (const item of items) {
      item.status = "UPLOADING";
      try {
        await store.uploadFile(item.file, item.key, false);
        item.status = "DONE";
        item.error = "";
      } catch (cause) {
        item.status = "FAILED";
        item.error = cause instanceof Error ? cause.message : "上传失败";
      }
    }
    if (items.some((item) => item.status === "DONE")) {
      await store.run(async () => {
        await Promise.all([store.loadKnowledgeBases(), store.loadKnowledgeDetails()]);
      });
      if (autoBuild.value) {
        await store.buildIndex();
        if (store.error) uploadError.value = store.error;
      }
    }
  } catch (cause) {
    uploadError.value = cause instanceof Error ? cause.message : "上传失败，请稍后重试";
  } finally {
    uploading.value = false;
  }
}

function formatSize(size: number): string {
  return `${Math.max(1, Math.ceil(size / 1024))} KB`;
}

function qualityTagType(status: ProfileQualityStatus): "success" | "warning" | "info" {
  if (status === "PASS" || status === "MANUAL_REVIEWED") return "success";
  if (status === "FALLBACK") return "info";
  return "warning";
}
</script>

<template>
  <div class="documents-view">
    <section class="lifecycle-grid" aria-label="文档生命周期状态">
      <button type="button" class="lifecycle-card" @click="store.navigate('indexes')">
        <span>处理中（Processing）</span>
        <el-tag :type="processingState.type" size="small">{{ processingState.label }}</el-tag>
        <small>解析、图片处理、切片（Chunk）与向量化（Embedding）</small>
      </button>
      <button type="button" class="lifecycle-card" @click="store.navigate('indexes')">
        <span>RAG</span>
        <el-tag :type="ragState.type" size="small">{{ ragState.label }}</el-tag>
        <small>技术构建成功后，新会话自动使用</small>
      </button>
      <button type="button" class="lifecycle-card" @click="store.navigate('support-review')">
        <span>技术支持（Support）</span>
        <el-tag :type="supportState.type" size="small">{{ supportState.label }}</el-tag>
        <small>Profile 属性待上线前不影响 RAG</small>
      </button>
    </section>

    <el-alert
      v-if="skippedDocuments.length"
      type="warning"
      show-icon
      :closable="false"
      class="skipped-documents-alert"
      title="以下文档在最近的索引构建中多次处理失败被跳过，未进入当前索引；请修复或替换源文件后重新上传并触发构建"
    >
      <ul class="skipped-documents-list">
        <li v-for="doc in skippedDocuments" :key="doc.id">
          <b>{{ doc.display_name || doc.filename }}</b>
          <span>（构建 v{{ doc.last_build_version ?? "-" }}）：{{ doc.last_build_error || "未知原因" }}</span>
        </li>
      </ul>
    </el-alert>

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
          <el-input
            v-model="store.documentSearch"
            class="search-input"
            placeholder="搜索文件名..."
            clearable
            :prefix-icon="Search"
          />
          <div class="spacer" />
          <span v-if="store.ragDocumentCardReadiness" class="muted result-count">
            RAG 理解：{{ store.ragDocumentCardReadiness.confirmed_count }}/{{ store.ragDocumentCardReadiness.expected_count }} 已确认
          </span>
          <el-button
            :disabled="!store.activeIndex"
            :loading="Boolean(store.actionLoading[`rag-document-card-generate:${store.activeIndex?.id ?? ''}`]) || ragCardTaskActive"
            @click="store.generateRagDocumentCards()"
          >
            生成/补齐 RAG 理解
          </el-button>
          <span class="muted result-count">{{ store.filteredDocuments.length }} 个文档</span>
        </div>
      </div>

      <div v-if="ragCardTask" class="rag-card-task-progress">
        <div class="rag-card-task-summary">
          <b>{{ ragCardTaskLabel }}</b>
          <span class="muted">
            {{ ragCardTask.stage }} · 成功 {{ ragCardTaskCounts.generated }} · 失败
            {{ ragCardTaskCounts.failed }}<template v-if="ragCardTaskCounts.total">
              · 共 {{ ragCardTaskCounts.total }} 份</template>
          </span>
        </div>
        <el-progress
          :percentage="ragCardTask.progress"
          :status="ragCardTaskProgressStatus"
          :stroke-width="8"
        />
        <small v-if="ragCardTask.error_message" class="rag-card-task-error">
          {{ ragCardTask.error_message }}
        </small>
      </div>

      <ListSkeleton v-if="store.loading && !store.documents.length" :columns="7" />
      <el-table
        v-else-if="store.filteredDocuments.length"
        :data="store.filteredDocuments"
        @row-click="(row: any) => store.inspectDocument(row)"
      >
        <el-table-column label="文件" min-width="260">
          <template #default="scope">
            <div class="file-cell">
              <span class="file-type-badge">{{
                scope.row.filename?.toLowerCase().endsWith(".pdf") ? "PDF" : "DOCX"
              }}</span>
              <div class="cell-primary">
                <b>{{ scope.row.display_name }}</b>
                <small class="mono">{{ scope.row.filename }}</small>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="90">
          <template #default><span class="muted">Word 文档</span></template>
        </el-table-column>
        <el-table-column label="大小" width="100">
          <template #default="scope">{{ formatSize(scope.row.file_size) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="130">
          <template #default="scope"><StatusBadge :status="scope.row.status" /></template>
        </el-table-column>
        <el-table-column label="文档属性" width="130">
          <template #default="scope">
            <el-tag :type="profileTagType(documentProfileStatus(scope.row.id))" size="small">
              {{ profileLabels[documentProfileStatus(scope.row.id)] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="RAG 理解" width="120">
          <template #default="scope">
            <el-tag :type="ragCardTagType(scope.row.id)" size="small">
              {{ ragCardLabel(scope.row.id) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="上传时间" min-width="170">
          <template #default="scope">{{ store.formatDate(scope.row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="" width="140" align="right">
          <template #default="scope">
            <el-button
              link
              type="primary"
              @click.stop="store.renameDocument(scope.row)"
            >
              重命名
            </el-button>
            <el-button
              link
              type="danger"
              @click.stop="store.deleteDocument(scope.row)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <EmptyState
        v-else
        :icon="Document"
        title="暂无文档"
        description="上传 Word / PDF 文档后会自动完成解析、切片（Chunk）和向量化（Embedding）；候选索引构建成功后需明确切换。Support Profile 已冻结，不再参与在线问答。"
      >
        <template #action>
          <el-button
            type="primary"
            :disabled="!store.selectedKnowledgeId"
            @click="store.uploadDrawerOpen = true"
          >
            上传文档
          </el-button>
        </template>
      </EmptyState>
    </div>

    <!-- Upload drawer -->
    <el-drawer
      v-model="store.uploadDrawerOpen"
      title="上传文档"
      size="520px"
      :close-on-click-modal="false"
    >
      <div class="form-section">
        <h4>选择文件</h4>
        <p class="desc">
          当前知识库：{{ store.selectedKnowledge?.name ?? "未选择" }}。支持一次选择多份 .docx /
          .pdf 文件，队列上传结束后统一触发一次 RAG 候选索引构建，构建完成后需明确切换；索引构建后会自动生成 RAG 文档理解卡片。
        </p>
        <button type="button" class="upload-dropzone" @click="openPicker">
          <el-icon :size="22"><UploadFilled /></el-icon>
          <b>点击选择 .docx / .pdf 文件（可多选）</b>
          <small>完全相同的文件会被拒绝，修订文件将作为新的候选文档</small>
        </button>
        <input
          ref="fileInput"
          type="file"
          accept=".docx,.pdf"
          multiple
          class="hidden-input"
          @change="pickFile"
        />
        <div v-if="queue.length" class="upload-queue">
          <div class="upload-queue-head">
            <span>上传队列（{{ queue.length }}）</span>
            <el-button
              link
              type="danger"
              size="small"
              :disabled="uploading"
              @click="clearQueue"
            >
              清空列表
            </el-button>
          </div>
          <ul>
            <li
              v-for="item in queue"
              :key="item.key"
              :class="`upload-item upload-item--${item.status.toLowerCase()}`"
            >
              <el-icon v-if="item.status === 'DONE'"><CircleCheckFilled /></el-icon>
              <el-icon v-else-if="item.status === 'FAILED' || item.status === 'REJECTED'"><CircleCloseFilled /></el-icon>
              <el-icon v-else-if="item.status === 'UPLOADING'" class="is-loading"><Loading /></el-icon>
              <el-icon v-else><Document /></el-icon>
              <div class="upload-item-main">
                <b>{{ item.file.name }}</b>
                <small>
                  {{ formatSize(item.file.size) }}<template v-if="item.error"> · {{ item.error }}</template>
                </small>
              </div>
              <el-button
                v-if="item.status !== 'UPLOADING'"
                link
                type="danger"
                size="small"
                :disabled="uploading"
                @click="removeItem(item)"
              >
                移除
              </el-button>
            </li>
          </ul>
        </div>
        <p v-if="batchSummary" class="upload-summary">{{ batchSummary }}</p>
        <el-checkbox v-model="autoBuild" :disabled="uploading">
          上传完成后自动触发一次索引构建
        </el-checkbox>
        <el-alert
          v-if="uploadError"
          class="upload-error"
          type="error"
          :title="uploadError"
          show-icon
          :closable="false"
        />
      </div>
      <template #footer>
        <div class="drawer-footer">
          <el-button :disabled="uploading" @click="closeUploadDrawer">取消</el-button>
          <el-button
            type="primary"
            :disabled="!pendingItems.length"
            :loading="uploading"
            @click="confirmUpload"
          >
            {{ uploading ? "上传中…" : `开始上传（${pendingItems.length}）` }}
          </el-button>
        </div>
      </template>
    </el-drawer>

    <!-- Document detail drawer -->
    <el-drawer
      v-model="store.documentDrawerOpen"
      :title="store.selectedDocument?.display_name ?? '文档详情'"
      size="640px"
    >
      <template v-if="store.selectedDocument">
        <div class="doc-meta">
          <StatusBadge :status="store.selectedDocument.status" />
          <span class="muted mono">{{ store.selectedDocument.filename }}</span>
          <span class="muted">{{ formatSize(store.selectedDocument.file_size) }}</span>
          <span class="muted">{{ store.formatDate(store.selectedDocument.created_at) }}</span>
        </div>
        <div class="inspection-index">
          <label>检查索引版本</label>
          <el-select
            v-model="store.documentInspectionIndexId"
            :disabled="!store.documentInspectionIndexes.length"
            @change="store.loadDocumentInspection()"
          >
            <el-option
              v-for="index in store.documentInspectionIndexes"
              :key="index.id"
              :label="index.label"
              :value="index.id"
            />
          </el-select>
          <span class="mono muted">{{ store.documentInspectionIndexId || "无可检查索引" }}</span>
        </div>
        <el-tabs>
          <el-tab-pane label="内容预览" lazy>
            <DocumentPreview v-if="store.documentDrawerOpen" :elements="store.elements" />
          </el-tab-pane>
          <el-tab-pane :label="`元素（Elements）· ${store.elements.length}`">
            <div class="scroll-list">
              <pre v-for="item in store.elements" :key="String(item.id)">{{ store.formatJson(item) }}</pre>
            </div>
          </el-tab-pane>
          <el-tab-pane :label="`切片（Chunks）· ${store.chunks.length}`">
            <div class="scroll-list">
              <pre v-for="item in store.chunks" :key="String(item.id)">{{ store.formatJson(item) }}</pre>
            </div>
          </el-tab-pane>
          <el-tab-pane label="Profile 与别名（Alias）">
            <div v-if="store.documentProfileContext" class="profile-context">
              <template v-if="store.documentProfileContext.active_profile || store.documentProfileContext.pending_profile">
                <div class="profile-card">
                  <div class="profile-card-head"><h4>Profile 属性</h4><el-tag :type="profileTagType(store.documentProfileContext.profile_status ?? 'NOT_GENERATED')" size="small">{{ profileLabels[store.documentProfileContext.profile_status ?? 'NOT_GENERATED'] }}</el-tag></div>
                  <p class="muted">未上线、低置信或失败的 Profile 不参与快速命中，RAG 仍可用。</p>
                  <div v-for="(intent, index) in store.documentProfileContext.active_profile?.intents ?? []" :key="`active-${index}`" class="intent-details">
                    <h5>当前线上属性</h5>
                    <el-descriptions :column="1" border size="small">
                      <el-descriptions-item label="标准问题"><b>{{ intent.canonical_question }}</b></el-descriptions-item>
                      <el-descriptions-item label="别名（Alias）"><div v-if="intent.aliases.length" class="tag-wrap"><el-tag v-for="alias in intent.aliases" :key="alias" size="small">{{ alias }}</el-tag></div><span v-else class="muted">无</span></el-descriptions-item>
                      <el-descriptions-item label="路由摘要">{{ intent.routing_summary || "—" }}</el-descriptions-item>
                      <el-descriptions-item label="启用状态"><el-tag :type="intent.enabled !== false ? 'success' : 'info'" size="small">{{ intent.enabled !== false ? "启用" : "停用" }}</el-tag></el-descriptions-item>
                    </el-descriptions>
                  </div>
                  <div v-for="(intent, index) in store.documentProfileContext.pending_profile?.intents ?? []" :key="`pending-${index}`" class="intent-details profile-pending">
                    <h5>待上线属性</h5>
                    <el-descriptions :column="1" border size="small">
                      <el-descriptions-item label="标准问题"><b>{{ intent.canonical_question }}</b></el-descriptions-item>
                      <el-descriptions-item label="别名（Alias）">
                        <div v-if="intent.aliases.length" class="tag-wrap">
                          <el-tag
                            v-for="alias in intent.aliases"
                            :key="alias"
                            size="small"
                            type="warning"
                          >
                            {{ alias }}
                          </el-tag>
                        </div><span v-else class="muted">无</span>
                      </el-descriptions-item>
                      <el-descriptions-item label="路由摘要">{{ intent.routing_summary || "—" }}</el-descriptions-item>
                      <el-descriptions-item label="启用状态"><el-tag :type="intent.enabled !== false ? 'success' : 'info'" size="small">{{ intent.enabled !== false ? "启用" : "停用" }}</el-tag></el-descriptions-item>
                    </el-descriptions>
                  </div>
                  <el-alert
                    v-if="store.documentProfileContext.pending_error_message"
                    :title="store.documentProfileContext.pending_error_message"
                    type="warning"
                    :closable="false"
                    class="profile-pending"
                  />
                  <el-button link type="primary" @click="store.navigate('support-review'); store.documentDrawerOpen = false">前往 Profile 审核（Profile Review）编辑或上线</el-button>
                </div>
              </template>
              <template v-else>
                <!-- Published (live) Profile -->
                <div v-if="store.documentProfileContext.published_profile" class="profile-card">
                  <div class="profile-card-head">
                    <h4>已发布 Profile</h4>
                    <el-tag type="success" size="small">已发布 · 影响线上技术支持（Support）</el-tag>
                  </div>
                  <el-descriptions :column="1" border size="small">
                    <el-descriptions-item label="标准问题">
                      {{ store.documentProfileContext.published_profile.canonical_question }}
                    </el-descriptions-item>
                    <el-descriptions-item label="别名（Alias）">
                      <div v-if="store.documentProfileContext.published_profile.aliases.length" class="tag-wrap">
                        <el-tag
                          v-for="alias in store.documentProfileContext.published_profile.aliases"
                          :key="alias"
                          size="small"
                        >
                          {{ alias }}
                        </el-tag>
                      </div>
                      <span v-else class="muted">空别名（Alias；无用户改写）</span>
                    </el-descriptions-item>
                    <el-descriptions-item label="路由摘要">
                      <p class="wrapped-text">
                        {{ store.documentProfileContext.published_profile.routing_summary || "—" }}
                      </p>
                    </el-descriptions-item>
                  </el-descriptions>
                </div>
                <div v-else class="profile-card profile-card-empty">
                  <h4>已发布 Profile</h4>
                  <p class="muted">尚未配置 —— 该文档还没有生效的线上 Profile。</p>
                </div>

                <!-- Unpublished candidate draft -->
                <div v-if="store.documentProfileContext.candidate_draft" class="profile-card">
                  <div class="profile-card-head">
                    <h4>候选草稿 · 未发布</h4>
                    <el-tag type="warning" size="small">不影响线上 Support</el-tag>
                  </div>
                  <el-descriptions :column="1" border size="small">
                    <el-descriptions-item label="候选索引版本（Index）">
                      v{{ store.documentProfileContext.candidate_draft.index_version }}
                      <span class="muted">（{{ store.documentProfileContext.candidate_draft.release_status }}）</span>
                    </el-descriptions-item>
                    <el-descriptions-item label="草稿操作">
                      <el-tag size="small" type="info">
                        {{ store.documentProfileContext.candidate_draft.operation }}
                      </el-tag>
                    </el-descriptions-item>
                    <el-descriptions-item label="审核状态">
                      <StatusBadge :status="store.documentProfileContext.candidate_draft.status" />
                    </el-descriptions-item>
                    <el-descriptions-item label="质量状态">
                      <el-tag
                        :type="qualityTagType(store.documentProfileContext.candidate_draft.quality_status)"
                        size="small"
                      >
                        {{ store.documentProfileContext.candidate_draft.quality_status }}
                      </el-tag>
                      <p
                        v-for="note in store.documentProfileContext.candidate_draft.quality_notes"
                        :key="note"
                        class="muted quality-note-line"
                      >
                        {{ note }}
                      </p>
                    </el-descriptions-item>
                    <el-descriptions-item label="标准问题">
                      {{ store.documentProfileContext.candidate_draft.canonical_question }}
                    </el-descriptions-item>
                    <el-descriptions-item label="别名（Alias）">
                      <div v-if="store.documentProfileContext.candidate_draft.aliases.length" class="tag-wrap">
                        <el-tag
                          v-for="alias in store.documentProfileContext.candidate_draft.aliases"
                          :key="alias"
                          size="small"
                          type="warning"
                        >
                          {{ alias }}
                        </el-tag>
                      </div>
                      <span v-else class="muted">空别名（Alias；无用户改写）</span>
                    </el-descriptions-item>
                    <el-descriptions-item label="路由摘要">
                      {{ store.documentProfileContext.candidate_draft.routing_summary || "—" }}
                    </el-descriptions-item>
                  </el-descriptions>
                  <div class="draft-actions">
                    <el-button size="small" @click="store.goToCandidateReview(store.documentProfileContext.candidate_draft!.index_id)">
                      前往候选审核
                    </el-button>
                  </div>
                </div>
                <div v-else class="profile-card profile-card-empty">
                  <h4>候选草稿</h4>
                  <p class="muted">当前没有未发布的 Profile 草稿。</p>
                </div>
              </template>
            </div>
            <EmptyState
              v-else
              title="Profile 数据加载中"
              description="正在读取该文档的已发布 Profile 与候选草稿。"
            />
          </el-tab-pane>
          <el-tab-pane label="RAG 文档理解">
            <RagDocumentCardEditor />
          </el-tab-pane>
        </el-tabs>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.lifecycle-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.lifecycle-card {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 8px 12px;
  padding: 14px;
  color: var(--text-primary);
  text-align: left;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  background: var(--bg-surface);
  cursor: pointer;
}

.lifecycle-card:hover { border-color: var(--brand-primary); }
.lifecycle-card > span { font-size: 13px; font-weight: 650; }
.lifecycle-card > small { grid-column: 1 / -1; color: var(--text-tertiary); }

@media (max-width: 760px) {
  .lifecycle-grid { grid-template-columns: 1fr; }
}

.toolbar-row {
  padding-bottom: 0;
}
.result-count {
  font-size: 12px;
}

.rag-card-task-progress {
  display: grid;
  gap: 8px;
  margin: 14px 0 0;
  padding: 12px 14px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  background: var(--bg-subtle);
}

.rag-card-task-summary {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 8px 16px;
  font-size: 12px;
}

.rag-card-task-error {
  color: var(--danger);
  overflow-wrap: anywhere;
}

.inspection-index {
  display: grid;
  grid-template-columns: auto minmax(220px, 1fr);
  gap: 8px 12px;
  align-items: center;
  margin: 16px 0;
}

.inspection-index .mono {
  grid-column: 2;
  font-size: 11px;
}

:deep(.el-table__row) {
  cursor: pointer;
}

.file-cell {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.file-type-badge {
  flex-shrink: 0;
  padding: 1px 6px;
  border: 1px solid var(--border-default);
  border-radius: 4px;
  color: var(--text-secondary);
  background: var(--bg-subtle);
  font-family: var(--font-mono);
  font-size: 10px;
  line-height: 18px;
}

.drawer-footer {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.hidden-input {
  display: none;
}

.upload-error {
  margin-top: 12px;
}

.upload-queue {
  margin-top: 12px;
}

.upload-queue-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
  color: var(--text-secondary);
  margin-bottom: 4px;
}

.upload-queue ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 4px;
  max-height: 240px;
  overflow-y: auto;
}

.upload-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-md);
}

.upload-item .el-icon {
  flex-shrink: 0;
}

.upload-item--done .el-icon {
  color: var(--el-color-success);
}

.upload-item--failed .el-icon,
.upload-item--rejected .el-icon {
  color: var(--el-color-danger);
}

.upload-item--uploading .el-icon {
  color: var(--el-color-primary);
}

.upload-item-main {
  display: grid;
  gap: 2px;
  min-width: 0;
  flex: 1;
}

.upload-item-main b {
  font-size: 13px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.upload-item-main small {
  font-size: 12px;
  color: var(--text-tertiary);
}

.upload-summary {
  margin: 12px 0 0;
  font-size: 13px;
  color: var(--text-secondary);
}

.skipped-documents-alert {
  margin-bottom: 16px;
}

.skipped-documents-list {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
  display: grid;
  gap: 4px;
}

.skipped-documents-list li {
  font-size: 13px;
  line-height: 1.5;
}

.skipped-documents-list b {
  font-weight: 600;
}

.upload-dropzone {
  display: grid;
  gap: 4px;
  justify-items: center;
  width: 100%;
  padding: 28px 16px;
  border: 1px dashed var(--border-strong);
  border-radius: var(--radius-md);
  color: var(--text-tertiary);
  background: var(--bg-subtle);
  cursor: pointer;
  transition: border-color var(--transition-fast), background-color var(--transition-fast);
}

.upload-dropzone:hover {
  border-color: var(--brand-primary);
  background: var(--brand-subtle);
}

.upload-dropzone b {
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 500;
}

.upload-dropzone small {
  font-size: 12px;
}

.doc-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border-subtle);
}

.scroll-list {
  max-height: 460px;
  overflow: auto;
}

.scroll-list pre {
  margin: 0 0 8px;
  padding: 10px 12px;
  overflow: auto;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  color: #33443b;
  background: var(--bg-subtle);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.profile-context {
  display: grid;
  gap: 16px;
  max-height: 460px;
  overflow: auto;
}

.profile-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
}

.profile-card-head h4 {
  margin: 0;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 600;
}

.intent-details + .intent-details {
  margin-top: 16px;
}

.intent-details h5 {
  margin: 14px 0 8px;
  color: var(--text-primary);
  font-size: 12px;
}

.profile-card-empty {
  padding: 14px;
  border: 1px dashed var(--border-default);
  border-radius: var(--radius-sm);
  background: var(--bg-subtle);
}

.profile-card-empty h4 {
  margin: 0 0 6px;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 600;
}

.profile-card-empty p {
  margin: 0;
  font-size: 12px;
}

/* Long alias lists and routing summaries must wrap inside the 640px drawer
 * instead of forcing horizontal overflow. */
.tag-wrap {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  max-height: 120px;
  overflow: auto;
}

.wrapped-text {
  max-height: 120px;
  overflow: auto;
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 12px;
  line-height: 1.6;
}

.quality-note-line {
  margin: 4px 0 0;
  font-size: 12px;
}

.draft-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 10px;
}
</style>

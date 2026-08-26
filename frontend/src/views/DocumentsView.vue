<script setup lang="ts">
import { ref } from "vue";
import { Document, Search, UploadFilled } from "@element-plus/icons-vue";

import { useAdminStore } from "@/composables/adminStore";
import type { PublishedProfileSummary, ProfileQualityStatus } from "@/types/admin";
import EmptyState from "@/components/ui/EmptyState.vue";
import StatusBadge from "@/components/ui/StatusBadge.vue";

const store = useAdminStore();

const pendingFile = ref<File | null>(null);
const pendingUploadKey = ref("");
const fileInput = ref<HTMLInputElement | null>(null);

function pickFile(event: Event): void {
  const input = event.target as HTMLInputElement;
  pendingFile.value = input.files?.[0] ?? null;
  pendingUploadKey.value = pendingFile.value ? crypto.randomUUID() : "";
}

function openPicker(): void {
  fileInput.value?.click();
}

async function confirmUpload(): Promise<void> {
  if (!pendingFile.value) return;
  await store.upload(pendingFile.value, pendingUploadKey.value || crypto.randomUUID());
  if (!store.error) {
    pendingFile.value = null;
    pendingUploadKey.value = "";
    if (fileInput.value) fileInput.value.value = "";
  }
}

function formatSize(size: number): string {
  return `${Math.max(1, Math.ceil(size / 1024))} KB`;
}

function formatProductModels(profile: PublishedProfileSummary): string {
  const parts = [profile.product_name, ...profile.model_names].filter(Boolean);
  return parts.length ? parts.join(" · ") : "—";
}

function qualityTagType(status: ProfileQualityStatus): "success" | "warning" | "info" {
  if (status === "PASS" || status === "MANUAL_REVIEWED") return "success";
  if (status === "FALLBACK") return "info";
  return "warning";
}
</script>

<template>
  <div class="documents-view">
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
          <span class="muted result-count">{{ store.filteredDocuments.length }} 个文档</span>
        </div>
      </div>

      <el-table
        v-if="store.filteredDocuments.length"
        :data="store.filteredDocuments"
        @row-click="(row: any) => store.inspectDocument(row)"
      >
        <el-table-column label="File" min-width="260">
          <template #default="scope">
            <div class="file-cell">
              <span class="file-type-badge">DOCX</span>
              <div class="cell-primary">
                <b>{{ scope.row.display_name }}</b>
                <small class="mono">{{ scope.row.filename }}</small>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="Type" width="90">
          <template #default><span class="muted">Word</span></template>
        </el-table-column>
        <el-table-column label="Size" width="100">
          <template #default="scope">{{ formatSize(scope.row.file_size) }}</template>
        </el-table-column>
        <el-table-column label="Status" width="130">
          <template #default="scope"><StatusBadge :status="scope.row.status" /></template>
        </el-table-column>
        <el-table-column label="Uploaded" min-width="170">
          <template #default="scope">{{ store.formatDate(scope.row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="" width="80" align="right">
          <template #default="scope">
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
        description="上传 Word 文档后会构建候选 RAG 索引；请在 Index Build 审核发布，发布前不影响线上问答。"
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
          当前知识库：{{ store.selectedKnowledge?.name ?? "未选择" }}。仅支持 .docx
          文件，上传后将自动构建候选 RAG 索引，审核发布前不影响线上问答。
        </p>
        <button type="button" class="upload-dropzone" @click="openPicker">
          <el-icon :size="22"><UploadFilled /></el-icon>
          <template v-if="pendingFile">
            <b>{{ pendingFile.name }}</b>
            <small>{{ formatSize(pendingFile.size) }} · 点击重新选择</small>
          </template>
          <template v-else>
            <b>点击选择 .docx 文件</b>
            <small>单文件上传；完全相同的文件会被拒绝，修订文件将作为新的候选文档</small>
          </template>
        </button>
        <input
          ref="fileInput"
          type="file"
          accept=".docx"
          class="hidden-input"
          @change="pickFile"
        />
      </div>
      <template #footer>
        <div class="drawer-footer">
          <el-button @click="store.uploadDrawerOpen = false">取消</el-button>
          <el-button type="primary" :disabled="!pendingFile" @click="confirmUpload">
            开始上传
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
          <el-tab-pane :label="`Elements · ${store.elements.length}`">
            <div class="scroll-list">
              <pre v-for="item in store.elements" :key="String(item.id)">{{ store.formatJson(item) }}</pre>
            </div>
          </el-tab-pane>
          <el-tab-pane :label="`Chunks · ${store.chunks.length}`">
            <div class="scroll-list">
              <pre v-for="item in store.chunks" :key="String(item.id)">{{ store.formatJson(item) }}</pre>
            </div>
          </el-tab-pane>
          <el-tab-pane label="Profile & Alias">
            <div v-if="store.documentProfileContext" class="profile-context">
              <!-- Published (live) Profile -->
              <div v-if="store.documentProfileContext.published_profile" class="profile-card">
                <div class="profile-card-head">
                  <h4>已发布 Profile</h4>
                  <el-tag type="success" size="small">已发布 · 影响线上 Support</el-tag>
                </div>
                <el-descriptions :column="1" border size="small">
                  <el-descriptions-item label="问题编码">
                    <span class="mono">{{ store.documentProfileContext.published_profile.problem_code }}</span>
                  </el-descriptions-item>
                  <el-descriptions-item label="标准问题">
                    {{ store.documentProfileContext.published_profile.canonical_question }}
                  </el-descriptions-item>
                  <el-descriptions-item label="产品 / 型号">
                    {{ formatProductModels(store.documentProfileContext.published_profile) }}
                  </el-descriptions-item>
                  <el-descriptions-item label="关键词">
                    <div v-if="store.documentProfileContext.published_profile.keywords.length" class="tag-wrap">
                      <el-tag
                        v-for="keyword in store.documentProfileContext.published_profile.keywords"
                        :key="keyword"
                        size="small"
                        type="info"
                      >
                        {{ keyword }}
                      </el-tag>
                    </div>
                    <span v-else class="muted">无</span>
                  </el-descriptions-item>
                  <el-descriptions-item label="Alias">
                    <div v-if="store.documentProfileContext.published_profile.aliases.length" class="tag-wrap">
                      <el-tag
                        v-for="alias in store.documentProfileContext.published_profile.aliases"
                        :key="alias"
                        size="small"
                      >
                        {{ alias }}
                      </el-tag>
                    </div>
                    <span v-else class="muted">空 Alias（无用户改写）</span>
                  </el-descriptions-item>
                  <el-descriptions-item label="路由摘要">
                    <p class="wrapped-text">
                      {{ store.documentProfileContext.published_profile.routing_summary || "—" }}
                    </p>
                  </el-descriptions-item>
                  <el-descriptions-item label="启用状态">
                    <el-tag
                      :type="store.documentProfileContext.published_profile.enabled ? 'success' : 'info'"
                      size="small"
                    >
                      {{ store.documentProfileContext.published_profile.enabled ? "启用" : "停用" }}
                    </el-tag>
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
                  <el-descriptions-item label="候选 Index 版本">
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
                  <el-descriptions-item label="Alias">
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
                    <span v-else class="muted">空 Alias（无用户改写）</span>
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
            </div>
            <EmptyState
              v-else
              title="Profile 数据加载中"
              description="正在读取该文档的已发布 Profile 与候选草稿。"
            />
          </el-tab-pane>
        </el-tabs>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.toolbar-row {
  padding-bottom: 0;
}
.result-count {
  font-size: 12px;
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
  justify-content: flex-end;
  gap: 8px;
}

.hidden-input {
  display: none;
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

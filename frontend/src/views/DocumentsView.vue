<script setup lang="ts">
import { ref } from "vue";
import { Document, Search, UploadFilled } from "@element-plus/icons-vue";

import { useAdminStore } from "@/composables/adminStore";
import EmptyState from "@/components/ui/EmptyState.vue";
import StatusBadge from "@/components/ui/StatusBadge.vue";

const store = useAdminStore();

const pendingFile = ref<File | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);

function pickFile(event: Event): void {
  const input = event.target as HTMLInputElement;
  pendingFile.value = input.files?.[0] ?? null;
}

function openPicker(): void {
  fileInput.value?.click();
}

async function confirmUpload(): Promise<void> {
  if (!pendingFile.value) return;
  await store.upload(pendingFile.value);
  if (!store.error) {
    pendingFile.value = null;
    if (fileInput.value) fileInput.value.value = "";
  }
}

function formatSize(size: number): string {
  return `${Math.max(1, Math.ceil(size / 1024))} KB`;
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
        description="上传 Word 文档开始构建检索索引。上传后会自动解析、分块并生成 embedding。"
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
          文件，上传后将自动提交索引构建。
        </p>
        <button type="button" class="upload-dropzone" @click="openPicker">
          <el-icon :size="22"><UploadFilled /></el-icon>
          <template v-if="pendingFile">
            <b>{{ pendingFile.name }}</b>
            <small>{{ formatSize(pendingFile.size) }} · 点击重新选择</small>
          </template>
          <template v-else>
            <b>点击选择 .docx 文件</b>
            <small>单文件上传，重复文档会生成新版本</small>
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
        <el-tabs>
          <el-tab-pane :label="`Elements · ${store.elements.length}`">
            <div class="scroll-list">
              <pre v-for="item in store.elements" :key="String(item.id)">{{ item.element_type }} · {{ item.content }}</pre>
            </div>
          </el-tab-pane>
          <el-tab-pane :label="`Chunks · ${store.chunks.length}`">
            <div class="scroll-list">
              <pre v-for="item in store.chunks" :key="String(item.id)">{{ item.chunk_type }} · {{ item.content }}</pre>
            </div>
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
</style>

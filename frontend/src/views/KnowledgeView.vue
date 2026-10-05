<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { Collection, MoreFilled, Search } from "@element-plus/icons-vue";
import { ElMessageBox } from "element-plus";

import { useAdminStore } from "@/composables/adminStore";
import type { KnowledgeAssistantConfig, KnowledgeBase } from "@/types/admin";
import KnowledgeAssistantSettings from "@/components/KnowledgeAssistantSettings.vue";
import EmptyState from "@/components/ui/EmptyState.vue";
import ListSkeleton from "@/components/ui/ListSkeleton.vue";
import StatusBadge from "@/components/ui/StatusBadge.vue";

const store = useAdminStore();
const props = defineProps<{ navigationQuery?: string }>();

const detailTarget = ref<KnowledgeBase | null>(null);
const detailOpen = ref(false);
const descriptionDraft = ref("");

const statusOptions = computed(() => {
  const states = [...new Set(store.knowledgeBases.map((kb) => kb.runtime_state))];
  return states.map((state) => ({ label: state, value: state }));
});

function openDetail(kb: KnowledgeBase): void {
  detailTarget.value = kb;
  descriptionDraft.value = kb.description ?? "";
  detailOpen.value = true;
}

async function saveDescription(): Promise<void> {
  const knowledge = detailTarget.value;
  if (!knowledge || isDeleting(knowledge)) return;
  if (knowledge.assistant_online) {
    try {
      await ElMessageBox.confirm(
        "该知识库已对 Assistant 上线。保存后说明立即对外可见；启用专业技术助手时，新请求将使用新的 RAG 范围。",
        "确认修改公开范围",
        { confirmButtonText: "保存", cancelButtonText: "取消", type: "warning" },
      );
    } catch {
      return;
    }
  }
  try {
    const updated = await store.updateKnowledgeBaseDescription(knowledge, descriptionDraft.value);
    detailTarget.value = updated;
    descriptionDraft.value = updated.description ?? "";
  } catch {
    // Keep the unsaved draft so a version conflict never discards operator input.
  }
}

async function selectAndOpen(kb: KnowledgeBase): Promise<void> {
  openDetail(kb);
  if (store.selectedKnowledgeId !== kb.id) {
    store.selectedKnowledgeId = kb.id;
    await store.selectKnowledge();
  }
}

let openedFromQuery = "";
watch(
  () => [props.navigationQuery, store.knowledgeBases] as const,
  ([query]) => {
    const id = new URLSearchParams(query ?? "").get("id") ?? "";
    if (!id) { openedFromQuery = ""; return; }
    const kb = store.knowledgeBases.find((item) => item.id === id);
    if (kb && openedFromQuery !== id) { openedFromQuery = id; void selectAndOpen(kb); }
  },
  { immediate: true },
);

function assistantChanged(config: KnowledgeAssistantConfig): void {
  store.applyKnowledgeAssistantConfig(config);
  if (detailTarget.value?.id === config.knowledge_id && config.revision >= (detailTarget.value.assistant_config_revision ?? 0)) {
    detailTarget.value.assistant_online = config.assistant_online;
    detailTarget.value.assistant_config_revision = config.revision;
  }
}

async function deleteKnowledge(kb: KnowledgeBase): Promise<void> {
  await store.deleteKnowledgeBase(kb);
  if (!store.error && !store.knowledgeBases.some((item) => item.id === kb.id)) {
    if (detailTarget.value?.id === kb.id) detailOpen.value = false;
  }
}

function handleCommand(command: string, kb: KnowledgeBase): void {
  if (command === "detail") void selectAndOpen(kb);
  if (command === "rebuild") {
    store.selectedKnowledgeId = kb.id;
    rebuild();
  }
  if (command === "delete") void deleteKnowledge(kb);
}

function rebuild(): void {
  void store.buildIndex();
}

function regenerateProfiles(): void {
  const kb = detailTarget.value;
  if (!kb || kb.status !== "ENABLED") return;
  void store.regenerateKnowledgeProfiles(kb);
}

function profileGenerationDisabledReason(kb: KnowledgeBase): string {
  if (kb.status === "DELETING") return "知识库正在删除";
  if (kb.status !== "ENABLED") return "知识库已停用";
  return "";
}

function isDeleting(kb: KnowledgeBase): boolean {
  return kb.status === "DELETING";
}
</script>

<template>
  <div class="knowledge-view">
    <div class="panel">
      <div class="panel-body toolbar-row">
        <div class="toolbar">
          <el-input
            v-model="store.knowledgeSearch"
            class="search-input"
            placeholder="搜索知识库..."
            clearable
            :prefix-icon="Search"
          />
          <el-select
            v-model="store.knowledgeStatusFilter"
            placeholder="全部状态"
            clearable
          >
            <el-option
              v-for="option in statusOptions"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
          <div class="spacer" />
          <span class="muted result-count">{{ store.filteredKnowledgeBases.length }} 个知识库</span>
        </div>
      </div>

      <ListSkeleton v-if="store.loading && !store.knowledgeBases.length" :columns="6" />
      <el-table
        v-else-if="store.filteredKnowledgeBases.length"
        :data="store.filteredKnowledgeBases"
        :show-header="true"
        @row-click="(row: any) => selectAndOpen(row)"
      >
        <el-table-column label="名称" min-width="240">
          <template #default="scope">
            <div class="kb-name">
              <el-icon class="kb-icon"><Collection /></el-icon>
              <div class="cell-primary">
                <b>{{ scope.row.name }}</b>
                <small>{{ scope.row.description || "暂无说明" }}</small>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="document_count" label="文档数" width="110" />
        <el-table-column prop="active_chunk_count" label="切片数" width="100" />
        <el-table-column label="状态" width="120">
          <template #default="scope"><StatusBadge :status="scope.row.runtime_state" /></template>
        </el-table-column>
        <el-table-column label="Assistant" width="110">
          <template #default="scope">
            <el-tag :type="scope.row.assistant_online ? 'success' : 'info'" size="small">
              {{ scope.row.assistant_online ? "已上线" : "未上线" }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="更新时间" min-width="170">
          <template #default="scope">{{ store.formatDate(scope.row.updated_at) }}</template>
        </el-table-column>
        <el-table-column label="" width="60" align="right">
          <template #default="scope">
            <el-dropdown
              trigger="click"
              @command="(cmd: string) => handleCommand(cmd, scope.row)"
            >
              <el-button
                link
                type="info"
                class="more-button"
                @click.stop
              >
                <el-icon><MoreFilled /></el-icon>
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="detail">查看详情</el-dropdown-item>
                  <el-dropdown-item command="rebuild">重建索引</el-dropdown-item>
                  <el-dropdown-item
                    command="delete"
                    divided
                    :disabled="Boolean(scope.row.building_index_id) || isDeleting(scope.row)"
                  >
                    {{ scope.row.status === "DELETING" ? "删除任务处理中" : scope.row.building_index_id ? "构建中不可删除" : "删除知识库" }}
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </template>
        </el-table-column>
      </el-table>
      <EmptyState
        v-else
        :icon="Collection"
        title="暂无知识库"
        description="创建第一个知识库，开始上传文档并构建检索索引。"
      >
        <template #action>
          <el-button type="primary" @click="store.knowledgeDrawerOpen = true">新建知识库</el-button>
        </template>
      </EmptyState>
    </div>

    <!-- New knowledge base drawer -->
    <el-drawer
      v-model="store.knowledgeDrawerOpen"
      title="新建知识库"
      size="520px"
      :close-on-click-modal="false"
    >
      <el-form label-position="top" @submit.prevent>
        <div class="form-section">
          <h4>基本信息</h4>
          <p class="desc">知识库是文档与检索索引的隔离单元，可按产品线或业务域划分。</p>
          <el-form-item label="名称" required>
            <el-input
              v-model="store.newKnowledgeName"
              maxlength="200"
              placeholder="例如：VDR600 产品文档"
            />
          </el-form-item>
          <el-form-item label="知识库说明 / RAG 专业范围（Assistant 对外可见）">
            <el-input
              v-model="store.newKnowledgeDescription"
              type="textarea"
              :rows="3"
              maxlength="5000"
              placeholder="填写支持的产品、型号或模块、问题类型，以及直接相关的基础技术（可选）"
            />
            <p class="scope-editor__help">
              请勿填写系统 Prompt、阈值、密钥、内部地址或仅管理员可见的信息。
            </p>
          </el-form-item>
        </div>
      </el-form>
      <template #footer>
        <div class="drawer-footer">
          <el-button @click="store.knowledgeDrawerOpen = false">取消</el-button>
          <el-button
            type="primary"
            :disabled="!store.newKnowledgeName.trim()"
            @click="store.createKnowledgeBase()"
          >
            创建
          </el-button>
        </div>
      </template>
    </el-drawer>

    <!-- Knowledge base detail drawer -->
    <el-drawer v-model="detailOpen" title="知识库详情" size="560px">
      <template v-if="detailTarget">
        <div class="detail-head">
          <div class="cell-primary">
            <b class="detail-name">{{ detailTarget.name }}</b>
            <small>{{ detailTarget.description || "暂无说明" }}</small>
          </div>
          <StatusBadge :status="detailTarget.runtime_state" />
        </div>

        <div class="scope-editor">
          <div class="scope-editor__heading">
            <b>知识库说明 / RAG 专业范围（Assistant 对外可见）</b>
            <span>{{ descriptionDraft.length }}/5000</span>
          </div>
          <el-input
            v-model="descriptionDraft"
            type="textarea"
            :rows="4"
            maxlength="5000"
            placeholder="填写支持的产品、型号或模块、问题类型，以及直接相关的基础技术"
            :disabled="isDeleting(detailTarget)"
          />
          <p class="scope-editor__help">
            请勿填写系统 Prompt、阈值、密钥、内部地址或仅管理员可见的信息。修改不触发索引重建。
          </p>
          <el-button
            type="primary"
            plain
            :disabled="isDeleting(detailTarget) || descriptionDraft.trim() === (detailTarget.description ?? '')"
            :loading="store.isActionLoading(`update-knowledge:${detailTarget.id}`)"
            @click="saveDescription"
          >
            保存说明
          </el-button>
        </div>

        <div class="detail-metrics">
          <div class="detail-metric">
            <span>文档数（Documents）</span>
            <strong>{{ detailTarget.document_count }}</strong>
          </div>
          <div class="detail-metric">
            <span>生效切片（Active Chunks）</span>
            <strong>{{ detailTarget.active_chunk_count }}</strong>
          </div>
          <div class="detail-metric">
            <span>需要重建（Rebuild Required）</span>
            <strong>{{ detailTarget.rebuild_required ? "是" : "否" }}</strong>
          </div>
        </div>

        <el-descriptions
          :column="1"
          border
          size="small"
          class="detail-meta"
        >
          <el-descriptions-item label="ID">
            <span class="mono">{{ detailTarget.id }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="生效索引（Active Index）">
            <span class="mono">RAG：{{ detailTarget.rag_active_index_id ?? detailTarget.active_index_id ?? "—" }}</span>
            <span class="mono">Support：{{ detailTarget.support_active_index_id ?? "—" }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="Assistant 对外状态">
            <el-tag :type="detailTarget.assistant_online ? 'success' : 'info'" size="small">
              {{ detailTarget.assistant_online ? "已上线" : "未上线" }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="构建中索引（Building Index）">
            <span class="mono">{{ detailTarget.building_index_id ?? "—" }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="创建时间">
            {{ store.formatDate(detailTarget.created_at) }}
          </el-descriptions-item>
          <el-descriptions-item label="更新时间">
            {{ store.formatDate(detailTarget.updated_at) }}
          </el-descriptions-item>
        </el-descriptions>

        <KnowledgeAssistantSettings
          v-if="detailOpen && !isDeleting(detailTarget)"
          :key="detailTarget.id"
          :knowledge-id="detailTarget.id"
          :knowledge-name="detailTarget.name"
          @changed="assistantChanged"
        />

        <div class="drawer-footer">
          <el-button
            type="danger"
            plain
            :disabled="Boolean(detailTarget.building_index_id) || isDeleting(detailTarget)"
            :loading="store.isActionLoading(`delete-knowledge:${detailTarget.id}`)"
            @click="deleteKnowledge(detailTarget)"
          >
            {{ detailTarget.status === "DELETING" ? "删除任务处理中" : detailTarget.building_index_id ? "构建中不可删除" : "删除知识库" }}
          </el-button>
          <span class="spacer" />
          <el-button @click="rebuild">重建索引</el-button>
          <el-tooltip
            :content="profileGenerationDisabledReason(detailTarget)"
            :disabled="!profileGenerationDisabledReason(detailTarget)"
            placement="top"
          >
            <span>
              <el-button
                :disabled="Boolean(profileGenerationDisabledReason(detailTarget))"
                :loading="store.isActionLoading(`document-profile-generate:${detailTarget.id}`)"
                @click="regenerateProfiles"
              >
                重新生成待上线属性
              </el-button>
            </span>
          </el-tooltip>
          <el-button
            type="primary"
            @click="store.selectedKnowledgeId = detailTarget.id; store.selectKnowledge()"
          >
            设为当前知识库
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

.result-count {
  font-size: 12px;
}

.kb-name {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.kb-icon {
  flex-shrink: 0;
  color: var(--text-tertiary);
  font-size: 16px;
}

:deep(.el-table__row) {
  cursor: pointer;
}

.more-button {
  padding: 4px;
}

.drawer-footer {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 20px;
}

/* Wrap instead of overflowing left when the action row exceeds the drawer
 * width; the spacer keeps the destructive action separated when it fits. */
.drawer-footer .spacer {
  flex: 1;
}

.detail-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}

.detail-name {
  font-size: 16px;
  font-weight: 600;
}

.detail-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 16px;
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

.detail-meta {
  margin-bottom: 4px;
}

.scope-editor {
  display: grid;
  gap: 8px;
  margin-bottom: 16px;
  padding: 14px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
}

.scope-editor__heading {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.scope-editor__heading span,
.scope-editor__help {
  color: var(--text-tertiary);
  font-size: 12px;
}

.scope-editor__help {
  margin: 0;
  line-height: 1.6;
}
</style>

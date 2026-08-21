<script setup lang="ts">
import { computed, ref } from "vue";
import { Collection, MoreFilled, Search } from "@element-plus/icons-vue";

import { useAdminStore } from "@/composables/adminStore";
import type { KnowledgeBase } from "@/types/admin";
import EmptyState from "@/components/ui/EmptyState.vue";
import StatusBadge from "@/components/ui/StatusBadge.vue";

const store = useAdminStore();

const detailTarget = ref<KnowledgeBase | null>(null);
const detailOpen = ref(false);

const statusOptions = computed(() => {
  const states = [...new Set(store.knowledgeBases.map((kb) => kb.runtime_state))];
  return states.map((state) => ({ label: state, value: state }));
});

function openDetail(kb: KnowledgeBase): void {
  detailTarget.value = kb;
  detailOpen.value = true;
}

async function selectAndOpen(kb: KnowledgeBase): Promise<void> {
  if (store.selectedKnowledgeId !== kb.id) {
    store.selectedKnowledgeId = kb.id;
    await store.selectKnowledge();
  }
  openDetail(kb);
}

function rebuild(): void {
  void store.buildIndex();
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

      <el-table
        v-if="store.filteredKnowledgeBases.length"
        :data="store.filteredKnowledgeBases"
        :show-header="true"
        @row-click="(row: any) => selectAndOpen(row)"
      >
        <el-table-column label="Name" min-width="240">
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
        <el-table-column prop="document_count" label="Documents" width="110" />
        <el-table-column prop="active_chunk_count" label="Chunks" width="100" />
        <el-table-column label="Status" width="120">
          <template #default="scope"><StatusBadge :status="scope.row.runtime_state" /></template>
        </el-table-column>
        <el-table-column label="Updated" min-width="170">
          <template #default="scope">{{ store.formatDate(scope.row.updated_at) }}</template>
        </el-table-column>
        <el-table-column label="" width="60" align="right">
          <template #default="scope">
            <el-dropdown
              trigger="click"
              @command="(cmd: string) => {
                if (cmd === 'detail') selectAndOpen(scope.row);
                if (cmd === 'rebuild') { store.selectedKnowledgeId = scope.row.id; rebuild(); }
              }"
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
          <el-form-item label="说明">
            <el-input
              v-model="store.newKnowledgeDescription"
              type="textarea"
              :rows="3"
              placeholder="简要描述该知识库的用途（可选）"
            />
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
    <el-drawer v-model="detailOpen" title="知识库详情" size="520px">
      <template v-if="detailTarget">
        <div class="detail-head">
          <div class="cell-primary">
            <b class="detail-name">{{ detailTarget.name }}</b>
            <small>{{ detailTarget.description || "暂无说明" }}</small>
          </div>
          <StatusBadge :status="detailTarget.runtime_state" />
        </div>

        <div class="detail-metrics">
          <div class="detail-metric">
            <span>Documents</span>
            <strong>{{ detailTarget.document_count }}</strong>
          </div>
          <div class="detail-metric">
            <span>Active Chunks</span>
            <strong>{{ detailTarget.active_chunk_count }}</strong>
          </div>
          <div class="detail-metric">
            <span>Rebuild Required</span>
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
          <el-descriptions-item label="Active Index">
            <span class="mono">{{ detailTarget.active_index_id ?? "—" }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="Building Index">
            <span class="mono">{{ detailTarget.building_index_id ?? "—" }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="创建时间">
            {{ store.formatDate(detailTarget.created_at) }}
          </el-descriptions-item>
          <el-descriptions-item label="更新时间">
            {{ store.formatDate(detailTarget.updated_at) }}
          </el-descriptions-item>
        </el-descriptions>

        <div class="drawer-footer">
          <el-button @click="rebuild">重建索引</el-button>
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
  justify-content: flex-end;
  gap: 8px;
  margin-top: 20px;
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
</style>

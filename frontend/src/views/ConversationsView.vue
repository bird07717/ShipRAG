<script setup lang="ts">
import { ChatLineSquare, Search } from "@element-plus/icons-vue";
import { onBeforeUnmount, onMounted, ref, watch } from "vue";

import { adminApi } from "@/api/admin";
import EmptyState from "@/components/ui/EmptyState.vue";
import ListSkeleton from "@/components/ui/ListSkeleton.vue";
import { useAdminStore } from "@/composables/adminStore";
import type { ConversationListItem, TraceSource } from "@/types/admin";
import { isTraceSource, TRACE_SOURCE_LABELS, TRACE_SOURCE_TAG } from "@/utils/statusLabels";
import { clampedPage } from "@/utils/paging";
import ListPagination from "@/components/ui/ListPagination.vue";

/*
 * Read-only conversation browser over the admin GET /conversations list.
 * The conversation table has no channel column: `last_source` is the source
 * of the latest turn's trace and the source filter keeps conversations with
 * any turn from that channel (backend gateway/repository.py).
 */
const props = defineProps<{ navigationQuery?: string }>();
const store = useAdminStore();

const SOURCE_LABELS = TRACE_SOURCE_LABELS;
const SOURCE_TAG = TRACE_SOURCE_TAG;

const items = ref<ConversationListItem[]>([]);
const loading = ref(false);
const error = ref("");
const loaded = ref(false);
const source = ref<TraceSource | "">("ASSISTANT_API");
const userInput = ref("");
const appliedUser = ref("");
const activityRange = ref<[Date, Date] | null>(null);
const appliedActivityRange = ref<[Date, Date] | null>(null);
let generation = 0;
let controller: AbortController | undefined;

const page = ref(1);
const pageSize = ref(50);
const total = ref(0);

function applyNavigationQuery(query: string | undefined): void {
  const params = new URLSearchParams(query ?? "");
  const requested = params.get("source");
  if (requested === "" || requested === "ALL") source.value = "";
  else if (isTraceSource(requested)) source.value = requested;
  const user = params.get("user");
  if (user !== null) {
    userInput.value = user;
    appliedUser.value = user.trim();
  }
}

/** Reload from the first page (filters, scope or deep link changed). */
function load(): Promise<void> {
  page.value = 1;
  return loadPage();
}

async function loadPage(): Promise<void> {
  const knowledgeId = store.selectedKnowledgeId;
  const current = ++generation;
  controller?.abort();
  controller = undefined;
  if (!knowledgeId || !store.hasServiceCredential) {
    items.value = [];
    total.value = 0;
    loading.value = false;
    loaded.value = false;
    return;
  }
  controller = new AbortController();
  loading.value = true;
  error.value = "";
  try {
    const response = await adminApi.listConversations(
      knowledgeId,
      {
        source: source.value || undefined,
        userId: appliedUser.value || undefined,
        limit: pageSize.value,
        offset: (page.value - 1) * pageSize.value,
        activityFrom: appliedActivityRange.value?.[0].toISOString(),
        activityTo: appliedActivityRange.value?.[1].toISOString(),
      },
      { signal: controller.signal },
    );
    if (current !== generation || store.selectedKnowledgeId !== knowledgeId) return;
    const rows = Array.isArray(response?.items) ? response.items : [];
    const count = typeof response?.total === "number" ? response.total : rows.length;
    const target = clampedPage(page.value, pageSize.value, rows.length, count);
    if (target !== null) {
      page.value = target;
      void loadPage();
      return;
    }
    items.value = rows;
    total.value = count;
    loaded.value = true;
  } catch (cause) {
    if (current !== generation) return;
    if (cause instanceof DOMException && cause.name === "AbortError") return;
    items.value = [];
    total.value = 0;
    error.value = cause instanceof Error ? cause.message : "会话列表加载失败";
  } finally {
    if (current === generation) loading.value = false;
  }
}

function applyFilters(): void {
  appliedUser.value = userInput.value.trim();
  appliedActivityRange.value = activityRange.value
    ? [new Date(activityRange.value[0]), new Date(activityRange.value[1])]
    : null;
  void load();
}

function sourceLabel(value: TraceSource | null): string {
  return value ? SOURCE_LABELS[value] ?? value : "无链路记录";
}

watch(() => store.selectedKnowledgeId, () => void load());
watch(source, () => void load());
watch(() => props.navigationQuery, (query) => {
  applyNavigationQuery(query);
  void load();
});
onMounted(() => {
  applyNavigationQuery(props.navigationQuery);
  void load();
});
onBeforeUnmount(() => {
  generation++;
  controller?.abort();
});
</script>

<template>
  <div class="conversations-view">
    <div class="panel">
      <div class="panel-body toolbar">
        <el-select
          v-model="store.selectedKnowledgeId"
          placeholder="选择知识库"
          aria-label="知识库"
          @change="store.selectKnowledge()"
        >
          <el-option
            v-for="kb in store.knowledgeBases"
            :key="kb.id"
            :label="kb.name"
            :value="kb.id"
          />
        </el-select>
        <!-- "" (all sources) is a real choice here, not an empty value. -->
        <el-select
          v-model="source"
          aria-label="会话来源"
          class="source-select"
          :empty-values="[null, undefined]"
        >
          <el-option label="全部来源" value="" />
          <el-option
            v-for="(label, key) in SOURCE_LABELS"
            :key="key"
            :label="label"
            :value="key"
          />
        </el-select>
        <el-input
          v-model="userInput"
          class="search-input"
          placeholder="按外部用户 ID 精确查找"
          clearable
          :prefix-icon="Search"
          aria-label="用户 ID"
          @keyup.enter="applyFilters"
          @clear="applyFilters"
        />
        <el-date-picker
          v-model="activityRange"
          type="datetimerange"
          range-separator="至"
          start-placeholder="开始日期时间"
          end-placeholder="结束日期时间"
          format="YYYY-MM-DD HH:mm"
          aria-label="最近活动时间范围"
          class="activity-range"
        />
        <el-button @click="applyFilters">查询</el-button>
        <div class="spacer" />
        <span class="muted result-count">
          {{ loaded ? `共 ${total} 个会话` : "" }}
        </span>
      </div>
      <p class="scope-note muted">
        时间范围按会话最近活动时间筛选；来源取自最近一轮链路记录，按来源筛选时只要会话中有一轮来自该来源即会显示。用户 ID 为精确匹配。点击行查看完整对话。
      </p>

      <el-alert
        v-if="error"
        :title="error"
        type="error"
        :closable="false"
        show-icon
        class="list-alert"
      />
      <EmptyState
        v-if="!store.selectedKnowledgeId"
        :icon="ChatLineSquare"
        title="请选择知识库"
        description="会话按知识库保存，选择后显示该知识库的会话。"
      />
      <ListSkeleton v-else-if="loading && !items.length" :columns="6" />
      <el-table
        v-else-if="items.length"
        :data="items"
        class="clickable-table"
        @row-click="(row: ConversationListItem) => store.openConversation(row.id)"
      >
        <el-table-column label="会话" min-width="260">
          <template #default="scope">
            <div class="cell-primary">
              <b :title="scope.row.title || ''">{{ scope.row.title || "（无标题）" }}</b>
              <small class="mono">{{ scope.row.id }}</small>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="170">
          <template #default="scope">
            <el-tag v-if="scope.row.last_source" :type="SOURCE_TAG[scope.row.last_source as TraceSource]" size="small">
              {{ sourceLabel(scope.row.last_source) }}
            </el-tag>
            <span v-else class="muted">无链路记录</span>
          </template>
        </el-table-column>
        <el-table-column label="用户" min-width="150" show-overflow-tooltip>
          <template #default="scope">
            <span :class="{ muted: !scope.row.user_id }">{{ scope.row.user_id || "无用户 ID" }}</span>
          </template>
        </el-table-column>
        <el-table-column
          prop="message_count"
          label="消息"
          width="80"
          align="right"
        />
        <el-table-column label="最近活动" width="170">
          <template #default="scope">{{ store.formatDate(scope.row.updated_at) }}</template>
        </el-table-column>
        <el-table-column label="创建时间" width="170">
          <template #default="scope">{{ store.formatDate(scope.row.created_at) }}</template>
        </el-table-column>
      </el-table>
      <EmptyState
        v-else-if="loaded && !error"
        :icon="ChatLineSquare"
        title="该条件下没有会话"
        description="可以切换来源为“全部来源”，或清空用户 ID 后重试。"
      />
      <ListPagination
        v-model:page="page"
        v-model:page-size="pageSize"
        :total="total"
        @page-change="loadPage"
        @size-change="load"
      />
    </div>
  </div>
</template>

<style scoped>
.conversations-view {
  display: grid;
  gap: var(--space-content-gap);
}

.source-select {
  width: 190px;
}

.activity-range {
  width: 360px;
}

.scope-note {
  margin: 0;
  padding: 0 18px 12px;
  font-size: 12px;
  line-height: 1.6;
}

.list-alert {
  margin: 0 18px 12px;
  width: auto;
}

.result-count {
  font-size: 12px;
}

:deep(.clickable-table .el-table__row) {
  cursor: pointer;
}
</style>

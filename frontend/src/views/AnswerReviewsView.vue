<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import { reviewsApi } from "@/api/reviews";
import type { AnswerRecord, ReviewStatus } from "@/types/reviews";
import AnswerReviewDetail from "@/components/AnswerReviewDetail.vue";
const props = defineProps<{ knowledgeId: string }>();
const records = ref<AnswerRecord[]>([]);
const total = ref(0);
const page = ref(1);
const marked = ref(false);
const status = ref("");
const query = ref("");
const loading = ref(false);
const error = ref("");
const selected = ref("");
const statuses: Record<ReviewStatus, string> = { OPEN: "待处理", VERIFY: "待验证", CLOSED: "已关闭" };
let controller: AbortController | undefined;
let version = 0;
async function load(reset = false) {
  if (reset) page.value = 1;
  const current = ++version;
  controller?.abort(); controller = new AbortController();
  loading.value = true; error.value = "";
  try {
    const value = await reviewsApi.list(props.knowledgeId, { marked: marked.value, status: status.value, query: query.value, offset: (page.value - 1) * 20 }, controller.signal);
    if (current === version) { records.value = value.items; total.value = value.total; }
  } catch (cause) { if (current === version && !controller.signal.aborted) error.value = cause instanceof Error ? cause.message : "回答记录加载失败"; }
  finally { if (current === version) loading.value = false; }
}
watch(() => props.knowledgeId, () => { selected.value = ""; records.value = []; void load(true); }, { immediate: true });
onBeforeUnmount(() => { version++; controller?.abort(); });
</script>
<template>
  <section class="answer-reviews">
    <p>查看真实 Assistant 回答，标记问题并用当前配置复测。每个请求只展示一次，幂等重放不重复列出。</p>
    <div class="toolbar">
      <el-input
        v-model="query"
        placeholder="搜索问题"
        aria-label="搜索问题"
        clearable
        @keyup.enter="load(true)"
      />
      <el-checkbox v-model="marked" @change="load(true)">仅已标记</el-checkbox>
      <el-select
        v-model="status"
        placeholder="处理状态"
        clearable
        @change="load(true)"
      >
        <el-option
          v-for="(label, value) in statuses"
          :key="value"
          :label="label"
          :value="value"
        />
      </el-select>
      <el-button @click="load(true)">查询</el-button>
      <el-button @click="load()">刷新</el-button>
    </div>
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
    />
    <el-table v-loading="loading" :data="records" @row-click="(row: AnswerRecord) => selected = row.request_id">
      <el-table-column
        prop="question"
        label="用户问题"
        min-width="180"
        show-overflow-tooltip
      />
      <el-table-column
        prop="answer"
        label="回答摘要"
        min-width="200"
        show-overflow-tooltip
      />
      <el-table-column label="命中文档" min-width="160"><template #default="{ row }">{{ row.document?.title || '—' }}</template></el-table-column>
      <el-table-column prop="response_type" label="回答类型" width="180" />
      <el-table-column prop="request_status" label="请求状态" width="120" />
      <el-table-column label="处理状态" width="100"><template #default="{ row }">{{ row.review_status ? statuses[row.review_status as ReviewStatus] : '未标记' }}</template></el-table-column>
      <el-table-column prop="created_at" label="时间" width="210" />
      <el-table-column label="操作" width="90"><template #default="{ row }"><el-button text @click.stop="selected = row.request_id">查看</el-button></template></el-table-column>
    </el-table>
    <el-pagination
      v-model:current-page="page"
      :total="total"
      :page-size="20"
      layout="prev, pager, next, total"
      @current-change="load()"
    />
    <el-drawer
      :model-value="Boolean(selected)"
      title="Assistant 回答与优化"
      size="min(960px, 95vw)"
      @close="selected = ''"
    >
      <AnswerReviewDetail
        v-if="selected"
        :key="knowledgeId + selected"
        :knowledge-id="knowledgeId"
        :request-id="selected"
        @changed="load()"
      />
    </el-drawer>
  </section>
</template>
<style scoped>
.answer-reviews { display: grid; gap: 16px; }
.toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }
.toolbar .el-input { width: 260px; }
.toolbar .el-select { width: 150px; }
.el-table { cursor: pointer; }
</style>

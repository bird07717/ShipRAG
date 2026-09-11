<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { adminApi } from "@/api/admin";
import { reviewsApi } from "@/api/reviews";
import type { DocumentItem } from "@/types/admin";
import type { AnswerDetail, AnswerRetest, ReviewCategory, ReviewInput, ReviewStatus } from "@/types/reviews";
import AnswerContent from "@/components/AnswerContent.vue";
import DocumentPreview from "@/components/DocumentPreview.vue";
import CodeBlock from "@/components/ui/CodeBlock.vue";
import { useAdminStore } from "@/composables/adminStore";

const props = defineProps<{ knowledgeId: string; requestId: string }>();
const emit = defineEmits<{ changed: [] }>();
const store = useAdminStore();
const detail = ref<AnswerDetail | null>(null);
const documents = ref<DocumentItem[]>([]);
const documentError = ref("");
const error = ref("");
const busy = ref(false);
const testing = ref(false);
const loaded = ref(false);
const form = ref<ReviewInput>({ revision: 0, category: "WRONG_DOCUMENT", status: "OPEN", expected_document_id: null, expectation: "", notes: "" });
const baseline = ref("");
const dirty = computed(() => JSON.stringify(form.value) !== baseline.value);
const lastRun = computed(() => detail.value?.retests[0]);
function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
const routing = computed(() => record(record(detail.value?.snapshot.diagnostics.retrieval_result).doc_routing));
const candidates = computed(() => {
  const values = record(detail.value?.snapshot.diagnostics.rerank_result).candidates;
  const scores = Array.isArray(routing.value.doc_scores) ? routing.value.doc_scores.map(record) : [];
  return Array.isArray(values) ? values.map((value) => {
    const candidate = record(value);
    const score = scores.find((item) => item.document_id === candidate.document_id);
    return { ...candidate, document_title: score?.title || candidate.document_id || "未记录" };
  }) : [];
});
const routeLabel = computed(() => ({ DELIVER: "交付文档", CLARIFY: "请用户澄清", NO_MATCH: "未确认匹配", STAY: "沿用焦点文档", SWITCH: "切换文档" }[String(routing.value.decision)] || String(routing.value.decision || "未记录")));

const preview = ref<{ title: string; index: string; elements: Array<Record<string, unknown>> } | null>(null);
const previewError = ref("");
const loadingPreview = ref(false);
let alive = true;
let previewVersion = 0;
const controller = new AbortController();
const categories: Record<ReviewCategory, string> = { WRONG_DOCUMENT: "命中文档错误", MISSING_KNOWLEDGE: "未找到已有资料", INCOMPLETE_CONTENT: "正文或图片缺失", WRONG_ANSWER: "回答内容不正确", OTHER: "其他问题", ACCEPTABLE: "合理回答，无需修改" };
const statuses: Record<ReviewStatus, string> = { OPEN: "待处理", VERIFY: "待验证", CLOSED: "已关闭" };
function message(cause: unknown) { return cause instanceof Error ? cause.message : "操作失败，请重试"; }
function apply(value: AnswerDetail) {
  detail.value = value;
  const review = value.review;
  form.value = { revision: review?.revision ?? 0, category: review?.category ?? "WRONG_DOCUMENT", status: review?.status ?? "OPEN", expected_document_id: review?.expected_document_id ?? null, expectation: review?.expectation ?? "", notes: review?.notes ?? "" };
  baseline.value = JSON.stringify(form.value);
}
async function load() {
  busy.value = true;
  error.value = "";
  try {
    const value = await reviewsApi.detail(props.knowledgeId, props.requestId, controller.signal);
    if (alive) { apply(value); loaded.value = true; }
  } catch (cause) { if (alive) error.value = message(cause); }
  finally { if (alive) busy.value = false; }
}
async function loadDocuments() {
  try { const value = await adminApi.listDocuments(props.knowledgeId); if (alive) documents.value = value; }
  catch (cause) { if (alive) documentError.value = message(cause); }
}
async function save() {
  if (busy.value || testing.value) return;
  busy.value = true; error.value = "";
  try {
    const value = await reviewsApi.save(props.knowledgeId, props.requestId, { ...form.value, expected_document_id: form.value.expected_document_id || null });
    if (alive) { apply(value); emit("changed"); }
  } catch (cause) { if (alive) error.value = message(cause); }
  finally { if (alive) busy.value = false; }
}
async function retest() {
  const review = detail.value?.review;
  if (!review || dirty.value || testing.value) return;
  testing.value = true; error.value = "";
  try {
    const run = await reviewsApi.retest(props.knowledgeId, review.id, review.revision, crypto.randomUUID());
    if (alive && detail.value) detail.value.retests = [run, ...detail.value.retests.filter((item) => item.id !== run.id)];
  } catch (cause) { if (alive) error.value = message(cause); }
  finally { if (alive) testing.value = false; }
}
function matchLabel(run: AnswerRetest) {
  const expected = run.expectation.expected_document_id;
  if (run.status !== "COMPLETED") return "未完成，不作质量判定";
  if (!expected) return "请按预期表现人工核验";
  const response = run.turns.at(-1)?.response;
  const ids = new Set([response?.document?.document_id, response?.document?.id, ...response?.references?.map((item) => item.document_id) ?? []]);
  return ids.has(expected) ? "预期文档出现在结果中，正文仍需人工核验" : "结果未包含预期文档";
}
async function inspect(documentId: string, inspectedIndex?: unknown) {
  const document = documents.value.find((item) => item.id === documentId);
  if (!document) { previewError.value = "文档不在当前知识库或已删除"; return; }
  const version = ++previewVersion;
  loadingPreview.value = true; previewError.value = ""; preview.value = null;
  try {
    // Inspect the original turn's index, never silently substitute today's index.
    const index = inspectedIndex ?? detail.value?.snapshot.diagnostics.index_id;
    if (!index) throw new Error("原回答索引信息已过期，请到文档页选择索引检查");
    const result = await adminApi.listElements(documentId, String(index));
    if (alive && version === previewVersion) preview.value = { title: document.display_name, index: result.index_id, elements: result.items };
  } catch (cause) { if (alive && version === previewVersion) previewError.value = message(cause); }
  finally { if (alive && version === previewVersion) loadingPreview.value = false; }
}
function exportCase() {
  if (!detail.value?.review) return;
  const blob = new Blob([JSON.stringify({ schema_version: 1, ...detail.value }, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url; anchor.download = `answer-review-${detail.value.review.id}.json`; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
onMounted(() => { void load(); void loadDocuments(); });
onBeforeUnmount(() => { alive = false; controller.abort(); previewVersion++; });
</script>
<template>
  <div v-loading="busy" class="review-detail">
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
    />
    <div class="actions"><el-button :disabled="busy || testing" @click="load">重新加载记录</el-button></div>
    <template v-if="detail && loaded">
      <h3>{{ detail.snapshot.question || "（未保存问题内容）" }}</h3>
      <p class="muted">{{ detail.snapshot.created_at }} · {{ detail.snapshot.external_user_id }} · {{ detail.snapshot.status }}</p>
      <div class="actions">
        <el-button v-if="detail.snapshot.trace_id" text @click="store.openTrace(detail.snapshot.trace_id)">查看原 Trace</el-button>
        <el-button text @click="store.navigate('documents')">管理文档与索引</el-button>
      </div>
      <details v-if="detail.snapshot.history.length">
        <summary>前置对话（{{ detail.snapshot.history.length }} 条消息）</summary>
        <div v-for="item in detail.snapshot.history" :key="item.id" class="history-message">
          <b>{{ item.role === 'USER' ? '用户' : 'Assistant' }}</b>
          <p v-if="item.role === 'USER'">{{ item.content }}</p>
          <AnswerContent v-else :payload="Object.keys(item.response_payload).length ? item.response_payload : { answer: item.content }" @inspect="inspect" />
        </div>
      </details>
      <h4>当时的 Assistant 回答</h4>
      <AnswerContent :payload="detail.snapshot.response" @inspect="inspect" />
      <CodeBlock v-if="Object.keys(detail.snapshot.error).length" :code="JSON.stringify(detail.snapshot.error, null, 2)" />
      <section class="diagnostic-summary">
        <p>实际引擎：{{ detail.snapshot.diagnostics.engine || '未记录' }} · 文档路由：{{ routeLabel }}</p>
        <p v-if="routing.document_title">路由目标：{{ routing.document_title }}</p>
        <el-table v-if="candidates.length" :data="candidates" size="small">
          <el-table-column prop="document_title" label="重排候选文档" min-width="180" />
          <el-table-column prop="rerank_score" label="原始重排分数" width="130" />
          <el-table-column
            prop="content"
            label="候选内容"
            min-width="200"
            show-overflow-tooltip
          />
        </el-table>
        <p v-if="detail.snapshot.diagnostics.redacted_at" class="muted">Trace 详情已按保留规则清理，原回答快照仍可查看。</p>
      </section>
      <details><summary>召回、重排和运行版本</summary><CodeBlock :code="JSON.stringify(detail.snapshot.diagnostics, null, 2)" /></details>
      <el-alert
        v-if="previewError"
        :title="previewError"
        type="warning"
        :closable="false"
      />
      <p v-if="loadingPreview">正在加载文档…</p>
      <details v-if="preview" open>
        <summary>{{ preview.title }} · 索引 {{ preview.index }}</summary>
        <DocumentPreview :key="preview.index + preview.title" :elements="preview.elements" />
      </details>
      <h4>问题标记</h4>
      <el-alert
        v-if="documentError"
        :title="`文档选项加载失败：${documentError}`"
        type="warning"
        :closable="false"
      />
      <el-form label-position="top" :disabled="busy || testing" @submit.prevent>
        <div class="form-grid">
          <el-form-item label="问题类型">
            <el-select v-model="form.category" aria-label="问题类型">
              <el-option
                v-for="(label, value) in categories"
                :key="value"
                :label="label"
                :value="value"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="处理状态">
            <el-select v-model="form.status" aria-label="处理状态">
              <el-option
                v-for="(label, value) in statuses"
                :key="value"
                :label="label"
                :value="value"
              />
            </el-select>
          </el-form-item>
        </div>
        <el-form-item label="预期文档（可选，仅限当前知识库）">
          <el-select
            v-model="form.expected_document_id"
            clearable
            filterable
            aria-label="预期文档"
          >
            <el-option
              v-for="doc in documents"
              :key="doc.id"
              :label="doc.display_name"
              :value="doc.id"
            />
          </el-select>
          <small v-if="detail.review?.expected_document_title">保存时名称：{{ detail.review.expected_document_title }}</small>
        </el-form-item>
        <el-form-item label="预期表现">
          <el-input
            v-model="form.expectation"
            type="textarea"
            :rows="2"
            maxlength="2000"
            aria-label="预期表现"
            placeholder="例如：应交付接线盒检查步骤，并保留配图"
          />
        </el-form-item>
        <el-form-item label="处理备注">
          <el-input
            v-model="form.notes"
            type="textarea"
            :rows="2"
            maxlength="4000"
            aria-label="处理备注"
          />
        </el-form-item>
      </el-form>
      <p v-if="detail.snapshot.status === 'RUNNING'">回答尚未完成，请稍后重新加载再标记。</p>
      <div class="actions">
        <el-button type="primary" :disabled="busy || testing || detail.snapshot.status === 'RUNNING'" @click="save">保存标记</el-button>
        <el-button :disabled="!detail.review || dirty || busy || testing || !detail.snapshot.history_complete" :loading="testing" @click="retest">按当前配置复测</el-button>
        <el-button :disabled="!detail.review || dirty || testing" @click="exportCase">导出用例</el-button>
      </div>
      <p class="muted">先保存标记再复测。复测会以新内部会话重问前文和当前问题，使用当前配置并调用模型；不改原会话。导出文件为用例快照，暂不直接执行批量回归。</p>
      <p v-if="dirty">有未保存的标记修改。</p>
      <p v-if="!detail.snapshot.history_complete">原问题或前置对话不完整，无法可靠复测。</p>
      <p v-if="testing">正在复测（最多180秒）。离开页面后可重新打开查看结果。</p>
      <section v-if="lastRun" class="retest-result">
        <h4>最新复测 · {{ lastRun.status }} · {{ lastRun.created_at }}</h4>
        <p>按标记版本 {{ lastRun.review_revision }} 执行；{{ matchLabel(lastRun) }}</p>
        <p v-if="lastRun.expectation.expected_document_title">预期文档：{{ lastRun.expectation.expected_document_title }}</p>
        <p>{{ lastRun.expectation.expectation }}</p>
        <el-alert
          v-if="lastRun.error"
          :title="lastRun.error"
          type="warning"
          :closable="false"
        />
        <p v-if="lastRun.status === 'RUNNING'">执行尚未结束，请稍后重新加载；执行中断超过5分钟后可重新发起。</p>
        <div v-for="(turn, index) in lastRun.turns" :key="index" class="retest-turn">
          <h4>{{ index + 1 }}. {{ turn.question }}</h4>
          <AnswerContent :payload="turn.response" @inspect="(id) => inspect(id, turn.diagnostics.index_id)" />
          <details><summary>本轮版本与耗时</summary><CodeBlock :code="JSON.stringify(turn.diagnostics, null, 2)" /></details>
          <el-button v-if="turn.response.trace_id" text @click="store.openTrace(turn.response.trace_id)">查看复测 Trace</el-button>
        </div>
      </section>
      <details v-if="detail.retests.length > 1"><summary>此前复测记录（{{ detail.retests.length - 1 }} 次）</summary><CodeBlock :code="JSON.stringify(detail.retests.slice(1), null, 2)" /></details>
    </template>
  </div>
</template>
<style scoped>
.review-detail { display: grid; gap: 14px; }
h3, h4, p { margin: 0; }
.actions { display: flex; flex-wrap: wrap; gap: 8px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.el-select { width: 100%; }
.history-message, .retest-turn { border-bottom: 1px solid var(--border-default); padding: 12px 0; }
.retest-result { padding: 16px; border: 1px solid var(--border-default); border-radius: 8px; }
</style>

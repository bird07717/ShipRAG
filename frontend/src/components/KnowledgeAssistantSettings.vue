<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { ElMessageBox } from "element-plus";

import { adminApi, ApiRequestError } from "@/api/admin";
import { streamChat } from "@/api/chat";
import type { ChatDoneEvent } from "@/api/chat";
import type { KnowledgeAssistantConfig } from "@/types/admin";
import { renderMarkdown } from "@/utils/markdown";
import { useAdminStore } from "@/composables/adminStore";

// The parent keys this component by KB ID. No global selected-KB state is
// consulted after an asynchronous operation has started.
const props = defineProps<{ knowledgeId: string; knowledgeName: string }>();
const emit = defineEmits<{ changed: [config: KnowledgeAssistantConfig] }>();
const store = useAdminStore();
const config = ref<KnowledgeAssistantConfig | null>(null);
const questions = ref(["", "", ""]);
const ready = ref(false);
const blockers = ref<string[]>([]);
const busy = ref(false);
const error = ref("");
const notice = ref("");
const testing = ref(false);
const testedQuestion = ref("");
const answer = ref("");
const result = ref<ChatDoneEvent | null>(null);
const testError = ref("");
let mounted = true;
let controller: AbortController | undefined;

const normalized = computed(() => questions.value.map((q) => q.trim()).filter(Boolean));
const dirty = computed(() => JSON.stringify(normalized.value) !== JSON.stringify(config.value?.suggestions ?? []));
const validation = computed(() => {
  const values = normalized.value;
  if (values.some((q) => Array.from(q).length < 4 || Array.from(q).length > 200)) return "每条推荐问题须为 4～200 字";
  const keys = values.map((q) => q.normalize("NFKC").toLowerCase().replace(/\s/g, "").replace(/[?？]+$/, ""));
  if (new Set(keys).size !== keys.length) return "推荐问题不能重复";
  if (config.value?.assistant_online && values.length !== 3) return "已上线知识库须保留三条推荐问题";
  return "";
});
const contentText = computed(() => result.value?.content
  ?.filter((block) => (block.type === "text" || block.type === "table") && block.text && block.text !== result.value?.answer)
  .map((block) => block.text).join("\n\n") ?? "");

function report(cause: unknown): void {
  error.value = cause instanceof Error ? cause.message : "操作失败，请重试";
  if (cause instanceof ApiRequestError && Array.isArray(cause.details.blockers)) {
    blockers.value = cause.details.blockers.map(String);
    ready.value = false;
  }
}

function trialDisabledReason(question: string): string {
  if (busy.value) return "正在加载或保存配置，请稍候";
  if (testing.value) return "上一条试问尚未结束，请稍候";
  if (!question.trim()) return "请先输入要试问的问题";
  if (!ready.value) return `暂不能试问：${blockers.value.join("；") || "知识库运行未就绪"}。处理后点击“检查就绪”。`;
  return "";
}

async function checkReadiness(): Promise<void> {
  if (busy.value || testing.value) return;
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    const response = await adminApi.getKnowledgeAssistant(props.knowledgeId);
    if (!mounted) return;
    // Refresh runtime only. Keep local edits and their original write revision
    // so a concurrent configuration edit still produces a conflict on save.
    ready.value = response.ready;
    blockers.value = response.blockers;
    notice.value = response.ready ? "运行已就绪，可以试问" : "运行未就绪，请先处理下方原因";
  } catch (cause) { if (mounted) report(cause); }
  finally { if (mounted) busy.value = false; }
}

async function reload(): Promise<void> {
  if (busy.value) return;
  busy.value = true;
  try {
    if (dirty.value) {
      try { await ElMessageBox.confirm("重新加载会丢弃尚未保存的问题，是否继续？", "重新加载"); }
      catch { return; }
    }
    error.value = "";
    const response = await adminApi.getKnowledgeAssistant(props.knowledgeId);
    if (!mounted) return;
    config.value = response;
    questions.value = Array.from({ length: 3 }, (_, i) => response.suggestions[i] ?? "");
    ready.value = response.ready;
    blockers.value = response.blockers;
    emit("changed", response);
  } catch (cause) { if (mounted) report(cause); }
  finally { if (mounted) busy.value = false; }
}

async function save(): Promise<void> {
  if (!config.value || busy.value || validation.value) return;
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    const response = await adminApi.saveKnowledgeAssistant(props.knowledgeId, config.value.revision, normalized.value);
    store.applyKnowledgeAssistantConfig(response);
    if (!mounted) return;
    config.value = response;
    questions.value = Array.from({ length: 3 }, (_, i) => response.suggestions[i] ?? "");
    emit("changed", response);
    notice.value = response.assistant_online ? "已保存，对外推荐问题已更新" : "已保存，可试问后确认上线";
  } catch (cause) { if (mounted) report(cause); }
  finally { if (mounted) busy.value = false; }
}

async function publish(online: boolean): Promise<void> {
  if (!config.value || busy.value || (online && (dirty.value || validation.value || normalized.value.length !== 3))) return;
  busy.value = true;
  error.value = "";
  notice.value = "";
  const revision = config.value.revision;
  const id = props.knowledgeId;
  try {
    try {
      await ElMessageBox.confirm(
        online ? `确认已检查「${props.knowledgeName}」的回答及三条推荐问题，并对外上线？` : `下线「${props.knowledgeName}」后，业务端将无法继续访问该库的问答、历史会话和图片。`,
        online ? "确认上线" : "确认下线",
        { type: "warning", confirmButtonText: online ? "确认上线" : "确认下线", cancelButtonText: "取消" },
      );
    } catch { return; }
    if (!mounted) return;
    const response = await adminApi.setKnowledgeAssistantPublication(id, revision, online);
    store.applyKnowledgeAssistantConfig(response);
    if (!mounted) return;
    config.value = response;
    emit("changed", response);
    notice.value = online ? "知识库已对外上线" : "知识库已下线";
  } catch (cause) { if (mounted) report(cause); }
  finally { if (mounted) busy.value = false; }
}

async function tryQuestion(question: string): Promise<void> {
  if (trialDisabledReason(question)) return;
  controller = new AbortController();
  const signal = controller.signal;
  testing.value = true;
  testedQuestion.value = question.trim();
  answer.value = "";
  result.value = null;
  testError.value = "";
  const current = () => mounted && !signal.aborted;
  try {
    // Each trial starts a new internal conversation; offline KBs remain private.
    await streamChat(props.knowledgeId, question.trim(), undefined, {
      onTrace: () => {},
      onDelta: (text) => { if (current()) answer.value += text; },
      onDone: (event) => { if (current()) { result.value = event; answer.value = event.answer; } },
      onError: (message) => { if (current()) testError.value = message; },
    }, {}, signal);
  } catch (cause) { if (current()) testError.value = cause instanceof Error ? cause.message : "试问失败"; }
  finally { if (mounted) testing.value = false; }
}

onMounted(() => void reload());
onUnmounted(() => { mounted = false; controller?.abort(); });
</script>

<!-- Markdown is sanitized by renderMarkdown (DOMPurify). -->
<!-- eslint-disable vue/no-v-html -->
<template>
  <section class="assistant-settings">
    <div class="settings-header">
      <h4>Assistant 上线与推荐问题</h4>
      <div class="settings-actions">
        <el-button :disabled="busy || testing" @click="checkReadiness">检查就绪</el-button>
        <el-button :disabled="busy || testing" @click="reload">重新加载配置</el-button>
      </div>
    </div>
    <p class="muted">维护该知识库的三条快捷提问，试问检查回答后确认上线。已上线时保存立即生效。</p>
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
    />
    <el-alert
      v-if="notice"
      :title="notice"
      type="success"
      :closable="false"
    />
    <template v-if="config">
      <el-tag :type="config.assistant_online ? 'success' : 'info'">{{ config.assistant_online ? "已上线" : "未上线" }}</el-tag>
      <el-tag :type="ready ? 'success' : 'warning'">{{ ready ? "运行已就绪" : "运行未就绪" }}</el-tag>
      <ul v-if="blockers.length"><li v-for="blocker in blockers" :key="blocker">{{ blocker }}</li></ul>
      <el-form label-position="top" @submit.prevent>
        <el-form-item v-for="(_, index) in questions" :key="index" :label="`推荐问题 ${index + 1}`">
          <div class="question-row">
            <el-input
              v-model="questions[index]"
              :aria-label="`推荐问题 ${index + 1}`"
              :disabled="busy || testing"
              maxlength="200"
              placeholder="请输入完整的标准问题"
            />
            <el-tooltip
              :content="trialDisabledReason(questions[index] ?? '')"
              :disabled="!trialDisabledReason(questions[index] ?? '')"
              placement="top"
            >
              <span :title="trialDisabledReason(questions[index] ?? '')">
                <el-button :disabled="Boolean(trialDisabledReason(questions[index] ?? ''))" @click="tryQuestion(questions[index] ?? '')">试问</el-button>
              </span>
            </el-tooltip>
          </div>
        </el-form-item>
      </el-form>
      <p v-if="validation" class="muted">{{ validation }}</p>
      <p v-else-if="dirty" class="muted">有未保存的修改，请先保存再上线。</p>
      <div class="settings-actions">
        <el-button :disabled="busy || testing || !dirty || Boolean(validation)" @click="save">保存问题</el-button>
        <el-button
          v-if="config.assistant_online"
          type="warning"
          :disabled="busy"
          @click="publish(false)"
        >
          下线
        </el-button>
        <el-button
          v-else
          type="primary"
          :disabled="busy || testing || dirty || !ready || normalized.length !== 3 || Boolean(validation)"
          @click="publish(true)"
        >
          确认上线
        </el-button>
      </div>
    </template>
    <div v-if="testedQuestion" class="trial-result">
      <b>试问：{{ testedQuestion }}</b>
      <p v-if="testing">正在生成回答…</p>
      <el-alert
        v-if="testError"
        :title="testError"
        type="error"
        :closable="false"
      />
      <div class="markdown-body" v-html="renderMarkdown(answer)" />
      <div v-if="contentText" class="markdown-body" v-html="renderMarkdown(contentText)" />
      <div v-for="(section, index) in result?.sections ?? []" :key="index">
        <b>{{ section.title }}</b><div class="markdown-body" v-html="renderMarkdown(section.content)" />
      </div>
      <p v-if="result">结果：{{ result.response_type }}</p>
      <p v-for="reference in result?.references ?? []" :key="reference.document_id">来源：{{ reference.title }}</p>
      <p class="muted">试问使用独立的新会话。调整问题或运行配置后，请重新检查回答。</p>
    </div>
  </section>
</template>

<style scoped>
.assistant-settings { margin-top: 20px; display: grid; gap: 12px; }
.settings-header, .question-row, .settings-actions { display: flex; align-items: center; gap: 8px; }
.settings-header { justify-content: space-between; flex-wrap: wrap; }
.settings-header h4 { margin: 0; }
.question-row { width: 100%; }
.trial-result { border-top: 1px solid var(--border-subtle); padding-top: 16px; overflow-wrap: anywhere; }
</style>

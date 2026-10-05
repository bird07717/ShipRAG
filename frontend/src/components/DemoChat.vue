<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

import { fetchWelcome, streamChat, downloadDocument } from "@/api/chat";
import ReviewImage from "@/components/ReviewImage.vue";
import type {
  ChatContentBlock,
  ChatFallbackEvent,
  ChatReference,
  ChatSupportSection,
} from "@/api/chat";
import { useAdminStore } from "@/composables/adminStore";
import { renderMarkdown } from "@/utils/markdown";
import PageHeader from "@/components/ui/PageHeader.vue";
import ChatComposer from "@/components/ui/ChatComposer.vue";

interface UiMessage {
  id: string;
  role: "user" | "assistant";
  text?: string;
  content?: ChatContentBlock[];
  answer_mode?: string | null;
  support_mode?: string | null;
  response_type?: string | null;
  disclaimer?: string | null;
  references?: ChatReference[];
  sections?: ChatSupportSection[];
  clarification?: { type: string; message: string; choices: Array<{ choice_id: string; title: string }> } | null;
  escalation?: { required: boolean; reason: string | null } | null;
  latency_ms?: number;
  execution_engine?: "SUPPORT" | "RAG" | null;
  route_stage?: string;
  fallback?: ChatFallbackEvent | null;
  streaming?: boolean;
  stopped?: boolean;
}

const store = useAdminStore();
const knowledgeBases = computed(() => store.knowledgeBases);
const selectedKb = computed({
  get: () => store.selectedKnowledgeId,
  set: (value: string) => {
    if (store.selectedKnowledgeId === value) return;
    store.selectedKnowledgeId = value;
    void store.selectKnowledge();
  },
});
const messages = ref<UiMessage[]>([]);
const welcomeMessage = ref("你好！我是文档助手，请描述你的问题开始对话。");
const suggestions = ref<string[]>([]);
const input = ref("");
const loading = ref(false);
const streamingHint = ref("");
const conversationId = ref<string | undefined>(undefined);
const messagesContainer = ref<HTMLElement | null>(null);
const abortController = ref<AbortController | null>(null);

// 防抖滚动
let scrollTimer: ReturnType<typeof setTimeout> | null = null;
async function scrollToBottomDebounce() {
  if (scrollTimer) clearTimeout(scrollTimer);
  scrollTimer = setTimeout(async () => {
    await nextTick();
    if (messagesContainer.value) {
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight;
    }
  }, 40);
}

const modeLabels: Record<string, string> = {
  PRODUCT_KNOWLEDGE: "产品知识",
  PRODUCT_EXPLAINED: "产品解释",
  PRODUCT_GENERAL: "通用知识",
  UNCONFIRMED: "无法确认",
  OUT_OF_SCOPE: "范围外",
};
const modeTones: Record<string, string> = {
  PRODUCT_KNOWLEDGE: "success",
  PRODUCT_EXPLAINED: "brand",
  PRODUCT_GENERAL: "warning",
  UNCONFIRMED: "neutral",
  OUT_OF_SCOPE: "danger",
};
const stageLabels: Record<string, string> = {
  gateway_routing: "正在解析引擎路由",
  support_processing: "Support 正在匹配 Profile / 处理文档",
  rag_processing: "RAG 正在检索并生成回答",
};

const visibleMessages = computed(() =>
  messages.value.filter((message) => !message.id.startsWith("welcome-")),
);
const hasUserMessage = computed(() => visibleMessages.value.some((m) => m.role === "user"));
const showHero = computed(() => !hasUserMessage.value);

function modeLabel(m: string) {
  return modeLabels[m] ?? m;
}
function modeTone(m: string) {
  return modeTones[m] ?? "neutral";
}
function isText(b: ChatContentBlock): boolean {
  return b.type === "text" || b.type === "table";
}
function refSection(ref: ChatReference): string {
  const paths = ref.section_paths?.[0];
  return Array.isArray(paths) ? paths.join(" / ") : "";
}
function formatLatency(ms: number | undefined): string {
  if (!ms) return "";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}
function fallbackReason(reason: string): string {
  return reason === "NO_MATCH" ? "Support 未定位到可靠文档，本轮由 RAG 兜底（NO_MATCH）" : reason;
}

// 切换知识库：清空会话并拉取欢迎引导词（虚拟消息，不入会话历史）
async function loadWelcome(kbId: string) {
  suggestions.value = [];
  let message = "你好！我是文档助手，请描述你的问题开始对话。";
  let nextSuggestions: string[] = [];
  try {
    const payload = await fetchWelcome(kbId);
    message = payload.message;
    nextSuggestions = payload.suggestions;
  } catch {
    // 欢迎词失败不阻塞页面：使用通用兜底文案
  }
  // 快速切换知识库时丢弃过期响应，避免旧 KB 的欢迎词覆盖新选择
  if (selectedKb.value !== kbId) return;
  welcomeMessage.value = message;
  suggestions.value = nextSuggestions;
  scrollToBottomDebounce();
}

watch(selectedKb, (kbId) => {
  streamGeneration += 1;
  abortController.value?.abort();
  abortController.value = null;
  loading.value = false;
  messages.value = [];
  conversationId.value = undefined;
  if (kbId) {
    void loadWelcome(kbId);
  }
});

async function askQuestion(question: string) {
  input.value = question;
  await nextTick();
  await send();
}

async function confirmChoice(choiceId: string, title: string): Promise<void> {
  input.value = `确认切换到：${title}`;
  await nextTick();
  await send(choiceId);
}

// 流代际：清空会话/切换KB时递增，旧流的事件不再写回状态
let streamGeneration = 0;

async function send(choiceId?: string) {
  if (!input.value.trim() || !selectedKb.value || loading.value) return;
  const q = input.value.trim();
  input.value = "";
  suggestions.value = [];
  messages.value.push({ id: Date.now() + "-u", role: "user", text: q });
  const pid = Date.now() + "-a";
  const aiMsg: UiMessage = { id: pid, role: "assistant", text: "", streaming: true };
  messages.value.push(aiMsg);
  loading.value = true;
  streamingHint.value = "正在检索资料...";
  const hintTimer = setTimeout(() => {
    if (loading.value) streamingHint.value = "正在生成回答...";
  }, 2500);
  const generation = streamGeneration;
  const controller = new AbortController();
  abortController.value = controller;
  scrollToBottomDebounce();
  try {
    await streamChat(selectedKb.value, q, conversationId.value, {
      onTrace: (_traceId, convId) => {
        if (generation !== streamGeneration) return;
        if (!conversationId.value) conversationId.value = convId;
      },
      onDelta: (text) => {
        if (generation !== streamGeneration) return;
        const target = messages.value.find((m) => m.id === pid);
        if (target) {
          target.text = (target.text || "") + text;
        }
        scrollToBottomDebounce();
      },
      onStatus: (event) => {
        if (generation !== streamGeneration) return;
        const target = messages.value.find((m) => m.id === pid);
        if (target) {
          target.route_stage = event.stage;
          if (event.engine) target.execution_engine = event.engine;
        }
        streamingHint.value = stageLabels[event.stage] ?? "正在处理请求...";
      },
      onFallback: (event) => {
        if (generation !== streamGeneration) return;
        const target = messages.value.find((m) => m.id === pid);
        if (target) {
          target.fallback = event;
          target.execution_engine = event.to;
          target.route_stage = "rag_processing";
        }
        streamingHint.value = "Support 未定位到可靠文档，本轮已自动降级到 RAG...";
      },
      onDone: (event) => {
        if (generation !== streamGeneration) return;
        const idx = messages.value.findIndex((m) => m.id === pid);
        if (idx >= 0) {
          const current = messages.value[idx]!;
          messages.value[idx] = {
            id: event.message_id,
            role: "assistant",
            text: event.answer,
            content: event.content.length ? event.content : undefined,
            answer_mode: event.answer_mode,
            support_mode: event.support_mode,
            response_type: event.response_type,
            disclaimer: event.disclaimer,
            references: event.references,
            sections: event.sections,
            clarification: event.clarification,
            escalation: event.escalation,
            execution_engine: event.execution_engine ?? current.execution_engine,
            fallback: event.fallback
              ? {
                  from: event.fallback.from,
                  to: event.execution_engine ?? "RAG",
                  reason: event.fallback.reason,
                  support_trace_id: event.fallback.support_trace_id,
                }
              : current.fallback,
            route_stage: "completed",
            latency_ms: event.latency_ms,
            streaming: false,
          };
        }
        if (!conversationId.value) conversationId.value = event.conversation_id;
        // NO_MATCH 回合后重显"换个问法"建议；正常回合清空，避免过时引导
        suggestions.value = event.next_suggestions ?? [];
        scrollToBottomDebounce();
      },
      onError: (msg) => {
        if (generation !== streamGeneration) return;
        const idx = messages.value.findIndex((m) => m.id === pid);
        if (idx >= 0) {
          messages.value[idx] = {
            id: pid,
            role: "assistant",
            text: "回答失败：" + msg,
            streaming: false,
          };
        }
        scrollToBottomDebounce();
      },
    }, { choiceId }, controller.signal);
  } catch (e) {
    const aborted = e instanceof DOMException && e.name === "AbortError";
    const idx = messages.value.findIndex((m) => m.id === pid);
    if (idx >= 0) {
      const target = messages.value[idx]!;
      target.streaming = false;
      target.stopped = aborted;
      if (!aborted) {
        target.text = "回答失败：" + (e instanceof Error ? e.message : "未知错误");
      }
    }
  } finally {
    clearTimeout(hintTimer);
    streamingHint.value = "";
    abortController.value = null;
    // 旧流的收尾不得干扰新一轮：清空/切换后 loading 由清空方复位
    if (generation === streamGeneration) {
      loading.value = false;
    }
    scrollToBottomDebounce();
  }
}

onBeforeUnmount(() => {
  streamGeneration++;
  abortController.value?.abort();
  if (scrollTimer) clearTimeout(scrollTimer);
});

function stopGeneration(): void {
  streamGeneration += 1;
  abortController.value?.abort();
  const last = [...messages.value].reverse().find((m) => m.streaming);
  if (last) {
    last.streaming = false;
    last.stopped = true;
  }
  loading.value = false;
  streamingHint.value = "";
}

function handleDownload(r: ChatReference) {
  downloadDocument(r.document_id, r.title).catch((e) => {
    messages.value.push({
      id: Date.now() + "-d",
      role: "assistant",
      text: "文档下载失败：" + (e instanceof Error ? e.message : "未知错误"),
      streaming: false,
    });
    scrollToBottomDebounce();
  });
}

function clearChat() {
  streamGeneration += 1;
  abortController.value?.abort();
  abortController.value = null;
  loading.value = false;
  messages.value = [];
  conversationId.value = undefined;
  suggestions.value = [];
  if (selectedKb.value) {
    void loadWelcome(selectedKb.value);
  }
}

onMounted(async () => {
  try {
    if (!store.knowledgeBases.length) await store.loadKnowledgeBases();
    const first = store.knowledgeBases[0];
    if (!selectedKb.value && first) selectedKb.value = first.id;
    else if (selectedKb.value) void loadWelcome(selectedKb.value);
  } catch {
    // ignore
  }
});
</script>

<template>
  <div class="demo-page">
    <PageHeader title="问答测试（DemoChat）" description="通过统一 Gateway 验证当前知识库的 RAG V2 真实对外问答能力。">
      <template #actions>
        <el-button :disabled="!visibleMessages.length" @click="clearChat">新会话</el-button>
      </template>
    </PageHeader>

    <div class="chat-toolbar">
      <div class="chat-context">
        <span class="context-label">知识库（Knowledge Base）</span>
        <el-select
          v-model="selectedKb"
          placeholder="选择知识库"
          size="small"
          style="width: 220px"
        >
          <el-option
            v-for="kb in knowledgeBases"
            :key="kb.id"
            :label="kb.name"
            :value="kb.id"
          />
        </el-select>
      </div>
      <span class="muted context-hint">
        当前执行 RAG V2 链路（Support 优先模式已冻结）；切换知识库将开始新会话
      </span>
    </div>

    <div class="chat-panel">
      <!-- Centered empty / welcome state -->
      <div v-if="showHero" ref="messagesContainer" class="chat-hero">
        <div class="hero-mark">D</div>
        <h2>测试统一问答接口</h2>
        <p>{{ welcomeMessage }}</p>
        <div v-if="suggestions.length" class="hero-suggestions">
          <span class="suggestion-label">推荐问题（Suggested Questions）</span>
          <button
            v-for="question in suggestions"
            :key="question"
            type="button"
            class="suggestion-chip"
            @click="askQuestion(question)"
          >
            {{ question }}
          </button>
        </div>
      </div>

      <!-- Conversation -->
      <div v-else ref="messagesContainer" class="chat-messages">
        <div
          v-for="msg in visibleMessages"
          :key="msg.id"
          class="msg-row"
          :class="msg.role"
        >
          <template v-if="msg.role === 'user'">
            <div class="bubble user-bubble">{{ msg.text }}</div>
          </template>
          <template v-else>
            <div class="assistant-avatar">S</div>
            <div class="assistant-body">
              <div class="assistant-head">
                <span class="assistant-name">
                  {{ msg.execution_engine === "RAG" ? "RAG 助手" : msg.execution_engine === "SUPPORT" ? "Support 助手" : "问答助手" }}
                </span>
                <span
                  v-if="msg.execution_engine"
                  class="engine-tag"
                  :class="msg.execution_engine.toLowerCase()"
                >
                  {{ msg.execution_engine }} Engine
                </span>
                <span v-if="msg.answer_mode" class="mode-tag" :class="modeTone(msg.answer_mode)">
                  {{ modeLabel(msg.answer_mode) }}
                </span>
                <span v-if="msg.support_mode" class="mode-tag" :class="modeTone(msg.support_mode)">
                  {{ modeLabel(msg.support_mode) }}
                </span>
              </div>
              <div class="bubble ai-bubble">
                <template v-if="msg.content">
                  <div
                    v-for="(block, i) in msg.content"
                    :key="`${msg.id}-${i}`"
                    class="content-block"
                  >
                    <!-- eslint-disable-next-line vue/no-v-html -- DOMPurify 消毒后的 Markdown 输出 -->
                    <div v-if="isText(block)" class="text-block" v-html="renderMarkdown(block.text || '')"></div>
                    <ReviewImage v-else-if="block.type === 'image' && block.image_asset_id" :asset-id="block.image_asset_id" :caption="block.caption" />
                  </div>
                  <span v-if="msg.streaming" class="cursor">●●●</span>
                </template>
                <!-- eslint-disable-next-line vue/no-v-html -- DOMPurify 消毒后的 Markdown 输出 -->
                <div v-else-if="msg.text" class="text-block">
                  <!-- eslint-disable-next-line vue/no-v-html -- DOMPurify 消毒后的 Markdown 输出 -->
                  <span v-html="renderMarkdown(msg.text)"></span><span v-if="msg.streaming" class="cursor">●●●</span>
                </div>
                <p v-if="msg.stopped && !msg.text" class="text-block stopped-note">已停止生成</p>
              </div>
              <div v-if="msg.streaming && streamingHint" class="streaming-hint">
                {{ streamingHint }}
              </div>
              <div v-if="msg.fallback" class="route-notice">
                <b>自动降级：{{ msg.fallback.from }} → {{ msg.fallback.to }}</b>
                <span>{{ fallbackReason(msg.fallback.reason) }}</span>
              </div>
              <div v-if="msg.disclaimer" class="disclaimer-box">{{ msg.disclaimer }}</div>
              <div v-if="msg.sections?.length" class="support-sections">
                <div v-for="(section, index) in msg.sections" :key="index" class="support-section">
                  <b>{{ section.title }}</b>
                  <p>{{ section.content }}</p>
                  <div v-if="section.disclaimer" class="section-disclaimer">
                    {{ section.disclaimer }}
                  </div>
                </div>
              </div>
              <div v-if="msg.clarification" class="clarification-box">
                <p>{{ msg.clarification.message }}</p>
                <el-button
                  v-for="choice in msg.clarification.choices"
                  :key="choice.choice_id"
                  size="small"
                  @click="confirmChoice(choice.choice_id, choice.title)"
                >
                  {{ choice.title }}
                </el-button>
              </div>
              <div v-if="msg.escalation?.required" class="disclaimer-box danger">
                已建议人工升级：{{ msg.escalation.reason }}
              </div>
              <details
                v-if="msg.references && msg.references.length"
                class="references-section"
              >
                <summary class="references-toggle">
                  Sources · {{ msg.references.length }}
                </summary>
                <div
                  v-for="docRef in msg.references"
                  :key="docRef.document_id"
                  class="ref-item"
                >
                  <span class="ref-icon">DOC</span>
                  <div class="ref-meta">
                    <b>{{ docRef.title }}</b>
                    <small v-if="refSection(docRef)">{{ refSection(docRef) }}</small>
                  </div>
                  <el-button
                    link
                    type="primary"
                    size="small"
                    @click="handleDownload(docRef)"
                  >
                    下载
                  </el-button>
                </div>
              </details>
              <div v-if="msg.latency_ms" class="msg-latency mono">
                {{ formatLatency(msg.latency_ms) }}
              </div>
            </div>
          </template>
        </div>
      </div>

      <!-- In-chat retry suggestions after failed routing turns -->
      <div v-if="!showHero && !loading && suggestions.length" class="chat-suggestions">
        <span class="suggestion-label">换个问法试试</span>
        <button
          v-for="question in suggestions"
          :key="question"
          type="button"
          class="suggestion-chip"
          @click="askQuestion(question)"
        >
          {{ question }}
        </button>
      </div>

      <!-- Composer -->
      <div class="composer-area">
        <ChatComposer
          v-model="input"
          :disabled="!selectedKb"
          :loading="loading"
          class="composer"
          @send="send"
          @stop="stopGeneration"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.demo-page {
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex: 1;
}

.chat-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}

.chat-context {
  display: flex;
  align-items: center;
  gap: 10px;
}

.context-label {
  color: var(--text-tertiary);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.context-hint {
  font-size: 12px;
}

.chat-panel {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-xl);
  background: var(--bg-surface);
}

/* ---------- Hero empty state ---------- */

.chat-hero {
  display: grid;
  flex: 1;
  place-content: center;
  justify-items: center;
  gap: 6px;
  padding: 32px 24px;
  overflow-y: auto;
  text-align: center;
}

.hero-mark {
  display: grid;
  width: 44px;
  height: 44px;
  place-items: center;
  margin-bottom: 10px;
  border-radius: var(--radius-md);
  color: #11332a;
  background: var(--brand-strong);
  font-size: 20px;
  font-weight: 800;
}

.chat-hero h2 {
  color: var(--text-primary);
  font-size: 20px;
  font-weight: 600;
}

.chat-hero p {
  max-width: 460px;
  color: var(--text-secondary);
  font-size: 13px;
}

.hero-suggestions {
  display: grid;
  gap: 8px;
  justify-items: center;
  margin-top: 20px;
}

/* 会话内"换个问法"建议条：位于消息列表与输入区之间 */
.chat-suggestions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 10px 18px;
  border-top: 1px dashed var(--border-subtle);
}

.chat-suggestions .suggestion-label {
  font-size: 12px;
  text-transform: none;
  letter-spacing: 0.02em;
}

.suggestion-label {
  color: var(--text-tertiary);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.suggestion-chip {
  max-width: 480px;
  padding: 8px 16px;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  color: var(--text-primary);
  background: var(--bg-surface);
  font-size: 13px;
  cursor: pointer;
  transition:
    border-color var(--transition-fast),
    background-color var(--transition-fast);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.suggestion-chip:hover {
  border-color: var(--brand-primary);
  background: var(--brand-subtle);
}

/* ---------- Messages ---------- */

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 24px max(24px, calc((100% - 820px) / 2));
}

.msg-row {
  display: flex;
  margin-bottom: 20px;
}

.msg-row.user {
  justify-content: flex-end;
}

.msg-row.assistant {
  justify-content: flex-start;
}

.bubble {
  max-width: 100%;
  padding: 10px 14px;
  border-radius: var(--radius-lg);
  line-height: 1.7;
}

.user-bubble {
  color: #11332a;
  background: var(--brand-subtle);
  border: 1px solid #cfe6d9;
}

.assistant-avatar {
  display: grid;
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  place-items: center;
  margin-right: 10px;
  border-radius: var(--radius-sm);
  color: #11332a;
  background: var(--brand-strong);
  font-size: 13px;
  font-weight: 700;
}

.assistant-body {
  min-width: 0;
  max-width: 760px;
}

.assistant-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.assistant-name {
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 600;
}

.mode-tag {
  padding: 0 7px;
  border-radius: 5px;
  font-size: 11px;
  line-height: 18px;
}

.engine-tag {
  padding: 0 7px;
  border: 1px solid var(--border-default);
  border-radius: 5px;
  font-family: "JetBrains Mono Variable", monospace;
  font-size: 10px;
  line-height: 18px;
}

.engine-tag.support {
  color: var(--brand-primary);
  background: var(--brand-subtle);
}

.engine-tag.rag {
  color: var(--warning);
  background: var(--warning-bg);
}

.mode-tag.success {
  color: var(--success);
  background: var(--success-bg);
}
.mode-tag.brand {
  color: var(--brand-primary);
  background: var(--brand-subtle);
}
.mode-tag.warning {
  color: var(--warning);
  background: var(--warning-bg);
}
.mode-tag.neutral {
  color: var(--text-secondary);
  background: var(--bg-subtle);
}
.mode-tag.danger {
  color: var(--error);
  background: var(--error-bg);
}

.ai-bubble {
  color: var(--text-primary);
  background: var(--bg-subtle);
}

.route-notice {
  display: grid;
  gap: 2px;
  margin-top: 8px;
  padding: 8px 10px;
  border: 1px solid var(--warning);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  background: var(--warning-bg);
  font-size: 12px;
}

.route-notice b {
  color: var(--warning);
}

.content-block {
  margin: 4px 0;
}

.text-block {
  margin: 0;
  line-height: 1.7;
}

.text-block :deep(p) {
  margin: 6px 0;
}

.text-block :deep(p:first-child) {
  margin-top: 0;
}

.text-block :deep(p:last-child) {
  margin-bottom: 0;
}

.text-block :deep(ol),
.text-block :deep(ul) {
  margin: 6px 0;
  padding-left: 22px;
}

.text-block :deep(li) {
  margin: 4px 0;
}

.text-block :deep(strong) {
  font-weight: 600;
}

.text-block :deep(code) {
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--bg-subtle);
  font-family: "JetBrains Mono Variable", monospace;
  font-size: 0.92em;
}

.text-block :deep(pre) {
  margin: 8px 0;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  background: var(--bg-subtle);
  overflow-x: auto;
}

.text-block :deep(pre code) {
  padding: 0;
  background: none;
}

.text-block :deep(table) {
  margin: 8px 0;
  border-collapse: collapse;
  max-width: 100%;
}

.text-block :deep(th),
.text-block :deep(td) {
  padding: 4px 10px;
  border: 1px solid var(--border-default);
  text-align: left;
}

.text-block :deep(th) {
  background: var(--bg-subtle);
  font-weight: 600;
}

.stopped-note {
  color: var(--text-tertiary);
  font-style: italic;
}

.cursor {
  margin-left: 2px;
  color: var(--brand-primary);
  font-size: 10px;
  letter-spacing: 2px;
  vertical-align: middle;
  animation: blink 1.2s infinite;
}

.streaming-hint {
  margin-top: 4px;
  color: var(--text-tertiary);
  font-size: 12px;
}

.support-sections,
.clarification-box {
  display: grid;
  gap: 8px;
  margin-top: 10px;
}

.support-section,
.clarification-box {
  padding: 10px 12px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  background: var(--bg-subtle);
  font-size: 13px;
  line-height: 1.6;
}

.support-section p,
.clarification-box p {
  margin: 3px 0 0;
}

.section-disclaimer {
  margin-top: 8px;
  color: var(--warning);
  font-size: 12px;
  line-height: 1.5;
}

.disclaimer-box {
  margin-top: 10px;
  padding: 8px 12px;
  border: 1px solid #efe0b8;
  border-radius: var(--radius-sm);
  background: var(--warning-bg);
  color: var(--warning);
  font-size: 12px;
}

.disclaimer-box.danger {
  border-color: #f0c6c3;
  color: var(--danger);
  background: #fff4f3;
}

.references-section {
  margin-top: 10px;
  border-top: 1px solid var(--border-subtle);
}

.references-toggle {
  display: inline-block;
  padding: 8px 0 6px;
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  user-select: none;
}

.references-toggle:hover {
  color: var(--brand-primary);
}

.ref-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 10px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  margin-bottom: 6px;
  transition: background-color var(--transition-fast);
}

.ref-item:hover {
  background: var(--bg-hover);
}

.ref-icon {
  flex-shrink: 0;
  padding: 1px 6px;
  border-radius: 4px;
  color: var(--brand-primary);
  background: var(--brand-subtle);
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 700;
  line-height: 16px;
}

.ref-meta {
  display: grid;
  flex: 1;
  gap: 1px;
  min-width: 0;
}

.ref-meta b {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ref-meta small {
  overflow: hidden;
  color: var(--text-tertiary);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.msg-latency {
  margin-top: 6px;
  color: var(--text-tertiary);
  font-size: 11px;
}

/* ---------- Composer ---------- */

.composer-area {
  padding: 14px 18px 16px;
  border-top: 1px solid var(--border-subtle);
  background: var(--bg-surface);
}

.composer {
  width: min(820px, 100%);
  margin: 0 auto;
}

@keyframes blink {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.25;
  }
}
</style>

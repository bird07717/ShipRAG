<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from "vue";

import { adminApi } from "@/api/admin";
import { fetchImageBlobUrl, fetchWelcome, streamChat, downloadDocument } from "@/api/chat";
import type { KnowledgeBase } from "@/types/admin";
import type { ChatContentBlock, ChatReference } from "@/api/chat";
import { renderMarkdown } from "@/utils/markdown";
import PageHeader from "@/components/ui/PageHeader.vue";
import ChatComposer from "@/components/ui/ChatComposer.vue";

interface UiMessage {
  id: string;
  role: "user" | "assistant";
  text?: string;
  content?: ChatContentBlock[];
  answer_mode?: string | null;
  response_type?: string | null;
  disclaimer?: string | null;
  references?: ChatReference[];
  latency_ms?: number;
  streaming?: boolean;
  stopped?: boolean;
}

const knowledgeBases = ref<KnowledgeBase[]>([]);
const selectedKb = ref("");
const messages = ref<UiMessage[]>([]);
const welcomeMessage = ref("你好！我是文档助手，请描述你的问题开始对话。");
const suggestions = ref<string[]>([]);
const input = ref("");
const loading = ref(false);
const streamingHint = ref("");
const conversationId = ref<string | undefined>(undefined);
const imageUrls = reactive<Record<string, string>>({});
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

const selectedKbName = computed(
  () => knowledgeBases.value.find((kb) => kb.id === selectedKb.value)?.name ?? "",
);

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
  return b.type === "text";
}
function hasImage(b: ChatContentBlock): boolean {
  return b.type === "image" && !!b.image_asset_id && !!imageUrls[b.image_asset_id];
}
function refSection(ref: ChatReference): string {
  const paths = ref.section_paths?.[0];
  return Array.isArray(paths) ? paths.join(" / ") : "";
}
function formatLatency(ms: number | undefined): string {
  if (!ms) return "";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
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

// 流代际：清空会话/切换KB时递增，旧流的事件不再写回状态
let streamGeneration = 0;

async function send() {
  if (!input.value.trim() || !selectedKb.value || loading.value) return;
  const q = input.value.trim();
  input.value = "";
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
        const target = messages.value.find((m) => m.id === pid);
        if (target) {
          target.text = (target.text || "") + text;
        }
        scrollToBottomDebounce();
      },
      onDone: (event) => {
        const idx = messages.value.findIndex((m) => m.id === pid);
        if (idx >= 0) {
          messages.value[idx] = {
            id: event.message_id,
            role: "assistant",
            text: event.answer,
            content: event.content,
            answer_mode: event.answer_mode,
            response_type: event.response_type,
            disclaimer: event.disclaimer,
            references: event.references,
            latency_ms: event.latency_ms,
            streaming: false,
          };
        }
        for (const b of event.content) {
          const assetId = b.image_asset_id;
          if (b.type === "image" && assetId && !imageUrls[assetId]) {
            fetchImageBlobUrl(assetId)
              .then((url) => {
                imageUrls[assetId] = url;
              })
              .catch(() => {
                imageUrls[assetId] = "";
              });
          }
        }
        scrollToBottomDebounce();
      },
      onError: (msg) => {
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
    }, controller.signal);
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
    const kbs = await adminApi.listKnowledgeBases();
    knowledgeBases.value = kbs;
    const first = kbs[0];
    if (first) selectedKb.value = first.id;
  } catch {
    // ignore
  }
});
</script>

<template>
  <div class="demo-page">
    <PageHeader title="Demo Chat" description="Test the end-user RAG experience.">
      <template #actions>
        <el-button :disabled="!visibleMessages.length" @click="clearChat">新会话</el-button>
      </template>
    </PageHeader>

    <div class="chat-toolbar">
      <div class="chat-context">
        <span class="context-label">Knowledge Base</span>
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
        回答基于 {{ selectedKbName || "所选知识库" }} 的已索引文档并附引用来源
      </span>
    </div>

    <div class="chat-panel">
      <!-- Centered empty / welcome state -->
      <div v-if="showHero" ref="messagesContainer" class="chat-hero">
        <div class="hero-mark">R</div>
        <h2>Ask your knowledge base</h2>
        <p>{{ welcomeMessage }}</p>
        <div v-if="suggestions.length" class="hero-suggestions">
          <span class="suggestion-label">Suggested Questions</span>
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
            <div class="assistant-avatar">R</div>
            <div class="assistant-body">
              <div class="assistant-head">
                <span class="assistant-name">RAG Assistant</span>
                <span v-if="msg.answer_mode" class="mode-tag" :class="modeTone(msg.answer_mode)">
                  {{ modeLabel(msg.answer_mode) }}
                </span>
                <span v-if="msg.response_type === 'DOC_DELIVERED'" class="mode-tag neutral">
                  已提供完整文档
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
                    <div v-else-if="block.type === 'image'" class="image-wrap">
                      <img
                        v-if="hasImage(block)"
                        :src="imageUrls[block.image_asset_id!]"
                        class="content-image"
                        alt="文档图片"
                      />
                      <div v-else class="image-placeholder">图片加载中...</div>
                    </div>
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
              <div v-if="msg.disclaimer" class="disclaimer-box">{{ msg.disclaimer }}</div>
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
                  <span class="ref-icon">DOCX</span>
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

.image-wrap {
  margin: 8px 0;
}

.content-image {
  max-width: 100%;
  border-radius: var(--radius-sm);
}

.image-placeholder {
  padding: 24px;
  border: 1px dashed var(--border-default);
  border-radius: var(--radius-sm);
  color: var(--text-tertiary);
  background: var(--bg-subtle);
  text-align: center;
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

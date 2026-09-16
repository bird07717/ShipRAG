<script setup lang="ts">
import { computed } from "vue";
import { Operation } from "@element-plus/icons-vue";

import { useAdminStore } from "@/composables/adminStore";
import EmptyState from "@/components/ui/EmptyState.vue";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import CodeBlock from "@/components/ui/CodeBlock.vue";

const store = useAdminStore();

interface ContextLike {
  document_key?: string;
  document_id?: string;
  content_sha256?: string;
  document?: string;
  document_name?: string;
  section_path?: string | string[];
  content?: string;
  chunk_text?: string;
  score?: number;
}

function asContexts(value: unknown): ContextLike[] {
  return Array.isArray(value) ? (value as ContextLike[]) : [];
}

function sectionText(path: string | string[] | undefined): string {
  if (!path) return "";
  return Array.isArray(path) ? path.join(" / ") : String(path);
}

const contexts = computed(() => asContexts(store.playgroundTrace?.selected_context));

const latencyMs = computed(() => store.playgroundTrace?.latency?.total_ms);

const paramLabels: Record<string, string> = {
  vector_top_k: "向量召回 Top K（每查询）",
  bm25_top_k: "BM25 召回 Top K（每查询）",
  fusion_top_k: "RRF 融合候选上限（Chunk）",
};
</script>

<template>
  <div class="playground">
    <div class="playground-main">
      <!-- Query panel -->
      <section class="panel">
        <header class="panel-header">
          <div>
            <h3>问题（Query）</h3>
            <span class="sub">运行完整 RAG 链路：检索 → 融合 → 重排 → 生成</span>
          </div>
        </header>
        <div class="panel-body">
          <el-input
            v-model="store.question"
            type="textarea"
            :rows="3"
            resize="none"
            placeholder="输入要调试的问题..."
          />
          <div class="query-actions">
            <el-button
              type="primary"
              :loading="store.loading"
              :disabled="!store.selectedKnowledgeId || !store.question.trim()"
              @click="store.askPlayground()"
            >
              运行
            </el-button>
          </div>
        </div>
      </section>

      <!-- Result panel -->
      <section class="panel result-panel">
        <template v-if="store.playgroundTrace">
          <header class="panel-header">
            <div>
              <h3>结果（Result）</h3>
              <div class="result-meta">
                <StatusBadge :status="store.playgroundTrace.status" />
                <span v-if="latencyMs" class="muted mono">{{ latencyMs }} ms</span>
                <span class="muted">{{ store.playgroundTrace.mode }}</span>
              </div>
            </div>
          </header>
          <div class="panel-body">
            <el-tabs>
              <el-tab-pane label="回答（Answer）">
                <p class="answer-text">{{ store.playgroundTrace.answer || "（无答案）" }}</p>
                <el-alert
                  class="v2-context-note"
                  title="RAG V2 以完整文档作为模型隐藏上下文"
                  description="公开答案不交付原文、候选列表或来源元数据；下方仅显示进入上下文的候选文档标识与内容摘要（SHA-256）。"
                  type="info"
                  :closable="false"
                  show-icon
                />

                <template v-if="contexts.length">
                  <h4 class="section-label">隐藏上下文候选文档 · {{ contexts.length }}</h4>
                  <div class="context-doc-list">
                    <div v-for="(doc, index) in contexts" :key="doc.document_id ?? index" class="context-doc">
                      <div class="context-doc-main">
                        <b>{{ doc.document_key ?? doc.document ?? doc.document_name ?? "未知文档" }}</b>
                        <small v-if="sectionText(doc.section_path)">{{ sectionText(doc.section_path) }}</small>
                      </div>
                      <span v-if="doc.content_sha256" class="mono context-sha">{{ doc.content_sha256.slice(0, 12) }}…</span>
                    </div>
                  </div>
                </template>
              </el-tab-pane>

              <el-tab-pane label="检索与上下文（Retrieval）">
                <div class="retrieval-sections">
                  <h4 class="section-label">查询理解 / 检索 / 文档级重排 / 上下文</h4>
                  <CodeBlock :code="store.formatJson(store.playgroundTrace.retrieval_result)" max-height="300px" />
                  <h4 class="section-label">引用来源（Citations）</h4>
                  <CodeBlock :code="store.formatJson(store.playgroundTrace.citation_result)" max-height="300px" />
                </div>
              </el-tab-pane>

              <el-tab-pane label="原始数据（Raw）">
                <CodeBlock :code="store.formatJson(store.playgroundTrace)" />
              </el-tab-pane>
            </el-tabs>
          </div>
        </template>
        <template v-else>
          <EmptyState
            :icon="Operation"
            title="尚未运行"
            :description="store.loading
              ? '正在检索资料并生成答案...'
              : '输入问题并点击「运行」，查看答案、检索来源与完整链路数据。'"
          />
        </template>
      </section>
    </div>

    <!-- Settings panel -->
    <aside class="panel playground-settings">
      <header class="panel-header">
        <div>
          <h3>检索设置（Retrieval Settings）</h3>
          <span class="sub">参数调整后立即生效</span>
        </div>
      </header>
      <div class="panel-body">
        <div class="form-section">
          <h4>知识库（Knowledge Base）</h4>
          <p class="desc">查询目标知识库及其生效索引（Active Index）</p>
          <el-select
            v-model="store.selectedKnowledgeId"
            placeholder="选择知识库"
            style="width: 100%"
          >
            <el-option
              v-for="kb in store.knowledgeBases"
              :key="kb.id"
              :label="kb.name"
              :value="kb.id"
            />
          </el-select>
        </div>
        <div class="form-section">
          <h4>检索参数</h4>
          <p class="desc">多查询混合召回与 RRF 融合的候选数量；Rerank 与上下文预算由环境变量控制</p>
          <div class="param-list">
            <div v-for="(_value, key) in store.playgroundOptions" :key="key" class="param-row">
              <label>{{ paramLabels[key] ?? key }}</label>
              <el-input-number v-model="store.playgroundOptions[key]" :min="1" :max="100" />
            </div>
          </div>
        </div>
      </div>
    </aside>
  </div>
</template>

<style scoped>
.playground {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 340px;
  gap: var(--space-content-gap);
  align-items: start;
}

.playground-main {
  display: grid;
  gap: var(--space-content-gap);
  min-width: 0;
}

.query-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}

.result-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 6px;
}

.answer-text {
  color: var(--text-primary);
  font-size: 14px;
  line-height: 1.8;
  white-space: pre-wrap;
}

.section-label {
  margin: 16px 0 8px;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 600;
}

.v2-context-note {
  margin-top: 12px;
}

.context-doc-list {
  display: grid;
  gap: 8px;
}

.context-doc {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 12px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  background: var(--bg-subtle);
}

.context-doc-main {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.context-doc-main b {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.context-doc-main small {
  color: var(--text-tertiary);
  font-size: 12px;
}

.context-sha {
  flex-shrink: 0;
  color: var(--text-tertiary);
  font-size: 11px;
}

.retrieval-sections .section-label:first-child {
  margin-top: 0;
}

.param-list {
  display: grid;
  gap: 12px;
}

.param-row {
  display: grid;
  gap: 4px;
}

.param-row label {
  color: var(--text-secondary);
  font-size: 13px;
}

.param-row .el-input-number {
  width: 100%;
}

@media (max-width: 1100px) {
  .playground {
    grid-template-columns: 1fr;
  }
}
</style>

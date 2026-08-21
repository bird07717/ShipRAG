<script setup lang="ts">
import { computed } from "vue";
import { Operation } from "@element-plus/icons-vue";

import { useAdminStore } from "@/composables/adminStore";
import EmptyState from "@/components/ui/EmptyState.vue";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import SourceCitation from "@/components/ui/SourceCitation.vue";
import RetrievalChunk from "@/components/ui/RetrievalChunk.vue";
import CodeBlock from "@/components/ui/CodeBlock.vue";

const store = useAdminStore();

interface SourceLike {
  source_id?: string;
  document?: string;
  section_path?: string | string[];
  page?: number | string | null;
}

interface ContextLike {
  document?: string;
  document_name?: string;
  section_path?: string | string[];
  content?: string;
  chunk_text?: string;
  score?: number;
}

function asSources(value: unknown): SourceLike[] {
  return Array.isArray(value) ? (value as SourceLike[]) : [];
}

function asContexts(value: unknown): ContextLike[] {
  return Array.isArray(value) ? (value as ContextLike[]) : [];
}

function sectionText(path: string | string[] | undefined): string {
  if (!path) return "";
  return Array.isArray(path) ? path.join(" / ") : String(path);
}

const sources = computed(() => asSources(store.playgroundTrace?.sources));
const contexts = computed(() => asContexts(store.playgroundTrace?.selected_context));

const latencyMs = computed(() => store.playgroundTrace?.latency?.total_ms);

const paramLabels: Record<string, string> = {
  vector_top_k: "Vector Top K",
  bm25_top_k: "BM25 Top K",
  fusion_top_k: "Fusion Top K",
  rerank_top_n: "Rerank Top N",
};
</script>

<template>
  <div class="playground">
    <div class="playground-main">
      <!-- Query panel -->
      <section class="panel">
        <header class="panel-header">
          <div>
            <h3>Query</h3>
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
              <h3>Result</h3>
              <div class="result-meta">
                <StatusBadge :status="store.playgroundTrace.status" />
                <span v-if="latencyMs" class="muted mono">{{ latencyMs }} ms</span>
                <span class="muted">{{ store.playgroundTrace.mode }}</span>
              </div>
            </div>
          </header>
          <div class="panel-body">
            <el-tabs>
              <el-tab-pane label="Answer">
                <p class="answer-text">{{ store.playgroundTrace.answer || "（无答案）" }}</p>

                <template v-if="sources.length">
                  <h4 class="section-label">Sources · {{ sources.length }}</h4>
                  <div class="source-list">
                    <SourceCitation
                      v-for="(source, index) in sources"
                      :key="source.source_id ?? index"
                      :index="source.source_id ?? index + 1"
                      :document="source.document ?? '未知文档'"
                      :section="sectionText(source.section_path)"
                      :page="source.page"
                    />
                  </div>
                </template>

                <template v-if="contexts.length">
                  <h4 class="section-label">Retrieved Context · {{ contexts.length }}</h4>
                  <div class="chunk-list">
                    <RetrievalChunk
                      v-for="(chunk, index) in contexts"
                      :key="index"
                      :score="chunk.score"
                      :document="chunk.document ?? chunk.document_name ?? '未知文档'"
                      :section="sectionText(chunk.section_path)"
                      :content="chunk.content ?? chunk.chunk_text ?? ''"
                    />
                  </div>
                </template>
              </el-tab-pane>

              <el-tab-pane label="Retrieval">
                <div class="retrieval-sections">
                  <h4 class="section-label">Vector / BM25 Retrieval</h4>
                  <CodeBlock :code="store.formatJson(store.playgroundTrace.retrieval_result)" max-height="300px" />
                  <h4 class="section-label">Rerank</h4>
                  <CodeBlock :code="store.formatJson(store.playgroundTrace.rerank_result)" max-height="300px" />
                  <h4 class="section-label">Citations</h4>
                  <CodeBlock :code="store.formatJson(store.playgroundTrace.citation_result)" max-height="300px" />
                </div>
              </el-tab-pane>

              <el-tab-pane label="Raw">
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
          <h3>Retrieval Settings</h3>
          <span class="sub">参数调整后立即生效</span>
        </div>
      </header>
      <div class="panel-body">
        <div class="form-section">
          <h4>Knowledge Base</h4>
          <p class="desc">查询目标知识库及其 Active Index</p>
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
          <p class="desc">双路召回与融合重排的候选数量</p>
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

.source-list,
.chunk-list {
  display: grid;
  gap: 8px;
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

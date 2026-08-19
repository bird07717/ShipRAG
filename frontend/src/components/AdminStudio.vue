<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { ElMessageBox } from "element-plus";

import { adminApi } from "@/api/admin";
import type {
  DocumentItem,
  GarbageCollectionResult,
  KnowledgeBase,
  KnowledgeIndex,
  IndexTask,
  ModelConfig,
  PromptTemplate,
  RagTrace,
  TraceSummary,
} from "@/types/admin";

const props = defineProps<{ view: string }>();

const knowledgeBases = ref<KnowledgeBase[]>([]);
const documents = ref<DocumentItem[]>([]);
const indexes = ref<KnowledgeIndex[]>([]);
const traces = ref<TraceSummary[]>([]);
const models = ref<ModelConfig[]>([]);
const prompts = ref<PromptTemplate[]>([]);
const selectedKnowledgeId = ref("");
const selectedDocument = ref<DocumentItem | null>(null);
const selectedTrace = ref<RagTrace | null>(null);
const traceDrawerOpen = ref(false);
const elements = ref<Array<Record<string, unknown>>>([]);
const chunks = ref<Array<Record<string, unknown>>>([]);
const newKnowledgeName = ref("");
const newKnowledgeDescription = ref("");
const autoActivate = ref(true);
const question = ref("如何配置数据库？");
const playgroundOptions = ref({ vector_top_k: 10, bm25_top_k: 10, fusion_top_k: 20, rerank_top_n: 10 });
const loading = ref(false);
const error = ref("");
const notice = ref("");
const serviceToken = ref("");
const latestIndexTasks = ref<Record<string, IndexTask | undefined>>({});
let indexPollTimer: ReturnType<typeof setInterval> | undefined;

const totalDocuments = computed(() =>
  knowledgeBases.value.reduce((total, item) => total + item.document_count, 0),
);
const totalChunks = computed(() =>
  knowledgeBases.value.reduce((total, item) => total + item.active_chunk_count, 0),
);

function formatDate(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
}

function formatJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function statusType(status: string): "success" | "warning" | "danger" | "info" {
  if (["ACTIVE", "READY", "COMPLETED", "ENABLED"].includes(status)) return "success";
  if (["BUILDING", "RUNNING", "UPDATING"].includes(status)) return "warning";
  if (["FAILED", "CANCELLED", "DISABLED"].includes(status)) return "danger";
  return "info";
}

async function run(action: () => Promise<void>, success = ""): Promise<void> {
  loading.value = true;
  error.value = "";
  notice.value = "";
  try {
    await action();
    notice.value = success;
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "操作失败";
  } finally {
    loading.value = false;
  }
}

async function loadKnowledgeBases(): Promise<void> {
  knowledgeBases.value = await adminApi.listKnowledgeBases();
  if (!selectedKnowledgeId.value && knowledgeBases.value.length) {
    selectedKnowledgeId.value = knowledgeBases.value[0]?.id ?? "";
  }
}

async function loadKnowledgeDetails(): Promise<void> {
  if (!selectedKnowledgeId.value) {
    documents.value = [];
    indexes.value = [];
    return;
  }
  [documents.value, indexes.value] = await Promise.all([
    adminApi.listDocuments(selectedKnowledgeId.value),
    adminApi.listIndexes(selectedKnowledgeId.value),
  ]);
  await loadIndexTasks();
}

async function loadIndexTasks(): Promise<void> {
  const entries = await Promise.all(
    indexes.value.map(async (index) => {
      const tasks = await adminApi.listIndexTasks(index.id);
      return [index.id, tasks[0]] as const;
    }),
  );
  latestIndexTasks.value = Object.fromEntries(entries);
}

function taskMetadata(task: IndexTask | undefined, key: string): string | number | undefined {
  const value = task?.metadata[key];
  return typeof value === "string" || typeof value === "number" ? value : undefined;
}

async function refreshAll(): Promise<void> {
  await loadKnowledgeBases();
  await Promise.all([
    loadKnowledgeDetails(),
    adminApi.listTraces().then((value) => (traces.value = value)),
    adminApi.listModels().then((value) => (models.value = value)),
    adminApi.listPrompts().then((value) => (prompts.value = value)),
  ]);
}

async function selectKnowledge(): Promise<void> {
  selectedDocument.value = null;
  selectedTrace.value = null;
  await run(async () => {
    await loadKnowledgeDetails();
    traces.value = await adminApi.listTraces(selectedKnowledgeId.value || undefined);
  });
}

async function createKnowledgeBase(): Promise<void> {
  if (!newKnowledgeName.value.trim()) return;
  await run(async () => {
    const created = await adminApi.createKnowledgeBase(
      newKnowledgeName.value.trim(),
      newKnowledgeDescription.value.trim(),
    );
    newKnowledgeName.value = "";
    newKnowledgeDescription.value = "";
    await loadKnowledgeBases();
    selectedKnowledgeId.value = created.id;
    await loadKnowledgeDetails();
  }, "知识库已创建");
}

async function upload(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file || !selectedKnowledgeId.value) return;
  await run(async () => {
    await adminApi.uploadDocument(selectedKnowledgeId.value, file);
    await Promise.all([loadKnowledgeBases(), loadKnowledgeDetails()]);
    input.value = "";
  }, "文档已上传，索引构建已提交");
}

async function deleteDocument(document: DocumentItem): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确定删除“${document.display_name}”吗？新索引发布前，当前在线索引仍可能检索到该文档。`,
      "删除文档",
      { type: "warning", confirmButtonText: "删除", cancelButtonText: "取消" },
    );
  } catch {
    return;
  }
  let successMessage = "";
  await run(async () => {
    const result = await adminApi.deleteDocument(document.id);
    if (selectedDocument.value?.id === document.id) selectedDocument.value = null;
    await Promise.all([loadKnowledgeBases(), loadKnowledgeDetails()]);
    successMessage = result.build_request.coalesced
      ? "文档已删除，将在当前构建完成后更新索引"
      : "文档已删除，索引更新已提交";
  });
  if (successMessage) notice.value = successMessage;
}

async function inspectDocument(document: DocumentItem): Promise<void> {
  selectedDocument.value = document;
  await run(async () => {
    const [elementResult, chunkResult] = await Promise.all([
      adminApi.listElements(document.id),
      adminApi.listChunks(document.id),
    ]);
    elements.value = elementResult.items;
    chunks.value = chunkResult.items;
  });
}

async function buildIndex(): Promise<void> {
  if (!selectedKnowledgeId.value) return;
  await run(async () => {
    await adminApi.buildIndex(selectedKnowledgeId.value, autoActivate.value);
    await Promise.all([loadKnowledgeBases(), loadKnowledgeDetails()]);
  }, "索引构建已提交");
}

async function activateIndex(indexId: string): Promise<void> {
  await run(async () => {
    await adminApi.activateIndex(indexId);
    await Promise.all([loadKnowledgeBases(), loadKnowledgeDetails()]);
  }, "索引已原子切换");
}

async function gcIndexes(): Promise<void> {
  if (!selectedKnowledgeId.value) return;
  await run(async () => {
    const result = await adminApi.gcIndexes(selectedKnowledgeId.value);
    notice.value = `已回收 ${result.deleted_count} 个旧索引`;
    await loadKnowledgeDetails();
  });
}

async function deleteIndex(indexId: string): Promise<void> {
  try {
    await ElMessageBox.confirm(
      "确定删除此索引吗？该操作不可恢复，将删除关联的 chunk、embedding 和图片数据。",
      "删除索引",
      { type: "warning", confirmButtonText: "删除", cancelButtonText: "取消" },
    );
  } catch {
    return;
  }
  await run(async () => {
    await adminApi.deleteIndex(indexId);
    await loadKnowledgeDetails();
  }, "索引已删除");
}

async function askPlayground(): Promise<void> {
  if (!selectedKnowledgeId.value || !question.value.trim()) return;
  await run(async () => {
    selectedTrace.value = await adminApi.runPlayground(
      selectedKnowledgeId.value,
      question.value.trim(),
      playgroundOptions.value,
    );
    traces.value = await adminApi.listTraces(selectedKnowledgeId.value);
  });
}

async function openTrace(traceId: string): Promise<void> {
  await run(async () => {
    selectedTrace.value = await adminApi.getTrace(traceId);
    traceDrawerOpen.value = true;
  });
}

function saveToken(value: string): void {
  const token = value.trim();
  if (token) sessionStorage.setItem("rag_service_token", token);
  else sessionStorage.removeItem("rag_service_token");
  notice.value = "访问凭据仅保存在当前浏览器会话";
}

onMounted(() => {
  void run(refreshAll);
  indexPollTimer = setInterval(() => {
    if (props.view === "indexes" && indexes.value.some((index) => index.status === "BUILDING")) {
      void loadKnowledgeDetails().catch(() => undefined);
    }
  }, 3_000);
});
onUnmounted(() => clearInterval(indexPollTimer));
</script>

<template>
  <section v-loading="loading" class="studio-content">
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
      show-icon
    />
    <el-alert
      v-if="notice"
      :title="notice"
      type="success"
      show-icon
      @close="notice = ''"
    />

    <template v-if="props.view === 'dashboard'">
      <div class="metric-grid">
        <article><span>知识库</span><strong>{{ knowledgeBases.length }}</strong><small>独立产品域</small></article>
        <article><span>文档</span><strong>{{ totalDocuments }}</strong><small>有效 Word 源</small></article>
        <article><span>Active Chunks</span><strong>{{ totalChunks }}</strong><small>当前在线快照</small></article>
        <article><span>最近 Trace</span><strong>{{ traces.length }}</strong><small>最多展示 50 条</small></article>
      </div>
      <el-table :data="knowledgeBases" empty-text="暂无知识库">
        <el-table-column prop="name" label="知识库" min-width="180" />
        <el-table-column prop="runtime_state" label="运行状态" width="130">
          <template #default="scope"><el-tag :type="statusType(scope.row.runtime_state)">{{ scope.row.runtime_state }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="document_count" label="文档" width="90" />
        <el-table-column prop="active_chunk_count" label="Chunks" width="100" />
        <el-table-column label="更新时间" min-width="180"><template #default="scope">{{ formatDate(scope.row.updated_at) }}</template></el-table-column>
      </el-table>
    </template>

    <template v-else-if="props.view === 'knowledge'">
      <div class="split-panel">
        <el-card shadow="never">
          <template #header><strong>创建知识库</strong></template>
          <el-form label-position="top">
            <el-form-item label="名称"><el-input v-model="newKnowledgeName" maxlength="200" /></el-form-item>
            <el-form-item label="说明"><el-input v-model="newKnowledgeDescription" type="textarea" /></el-form-item>
            <el-button type="primary" @click="createKnowledgeBase">创建</el-button>
          </el-form>
        </el-card>
        <el-card shadow="never">
          <template #header><strong>知识库目录</strong></template>
          <button
            v-for="kb in knowledgeBases"
            :key="kb.id"
            class="resource-row"
            @click="selectedKnowledgeId = kb.id; selectKnowledge()"
          >
            <span><b>{{ kb.name }}</b><small>{{ kb.description || '暂无说明' }}</small></span>
            <el-tag :type="statusType(kb.runtime_state)">{{ kb.runtime_state }}</el-tag>
          </button>
        </el-card>
      </div>
    </template>

    <template v-else-if="props.view === 'documents'">
      <div class="toolbar">
        <el-select v-model="selectedKnowledgeId" placeholder="选择知识库" @change="selectKnowledge">
          <el-option
            v-for="kb in knowledgeBases"
            :key="kb.id"
            :label="kb.name"
            :value="kb.id"
          />
        </el-select>
        <label class="upload-button">上传 Word<input type="file" accept=".docx" @change="upload" /></label>
      </div>
      <el-table :data="documents" @row-click="inspectDocument">
        <el-table-column prop="display_name" label="文档" min-width="200" />
        <el-table-column prop="filename" label="源文件" min-width="220" />
        <el-table-column prop="status" label="状态" width="110" />
        <el-table-column label="大小" width="110"><template #default="scope">{{ Math.ceil(scope.row.file_size / 1024) }} KB</template></el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="scope">
            <el-button type="danger" link @click.stop="deleteDocument(scope.row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="selectedDocument" class="preview-grid">
        <el-card shadow="never"><template #header>原文档</template><h3>{{ selectedDocument.display_name }}</h3><p>{{ selectedDocument.filename }}</p><small>{{ selectedDocument.id }}</small></el-card>
        <el-card shadow="never"><template #header>Elements · {{ elements.length }}</template><div class="scroll-list"><pre v-for="item in elements" :key="String(item.id)">{{ item.element_type }} · {{ item.content }}</pre></div></el-card>
        <el-card shadow="never"><template #header>Chunks · {{ chunks.length }}</template><div class="scroll-list"><pre v-for="item in chunks" :key="String(item.id)">{{ item.chunk_type }} · {{ item.content }}</pre></div></el-card>
      </div>
    </template>

    <template v-else-if="props.view === 'indexes'">
      <div class="toolbar">
        <el-select v-model="selectedKnowledgeId" @change="selectKnowledge">
          <el-option
            v-for="kb in knowledgeBases"
            :key="kb.id"
            :label="kb.name"
            :value="kb.id"
          />
        </el-select>
        <el-switch v-model="autoActivate" active-text="完成后自动切换" inactive-text="完成后人工验证" />
        <el-button type="primary" :disabled="!selectedKnowledgeId" @click="buildIndex">重建索引</el-button>
        <el-button :disabled="!selectedKnowledgeId" @click="gcIndexes">回收旧索引</el-button>
      </div>
      <el-timeline>
        <el-timeline-item
          v-for="index in indexes"
          :key="index.id"
          :timestamp="formatDate(index.created_at)"
          placement="top"
        >
          <el-card shadow="never">
            <div class="index-title"><span><b>Index v{{ index.version }}</b><small>{{ index.id }}</small></span><el-tag :type="statusType(index.status)">{{ index.status }}</el-tag></div>
            <p>{{ index.document_count }} documents · {{ index.element_count }} elements · {{ index.chunk_count }} chunks</p>
            <p class="muted">{{ index.embedding_model_name }} · {{ index.bm25_engine }}</p>
            <template v-if="latestIndexTasks[index.id]">
              <el-progress :percentage="latestIndexTasks[index.id]?.progress ?? 0" />
              <p class="muted">
                {{ latestIndexTasks[index.id]?.stage }}
                <template v-if="taskMetadata(latestIndexTasks[index.id], 'current_document')">
                  · 第 {{ taskMetadata(latestIndexTasks[index.id], 'current_document_position') }}/{{ taskMetadata(latestIndexTasks[index.id], 'total_documents') }} 份
                  · {{ taskMetadata(latestIndexTasks[index.id], 'current_document') }}
                </template>
              </p>
            </template>
            <el-alert
              v-if="index.error_message"
              :title="index.error_message"
              type="error"
              :closable="false"
            />
            <el-button
              v-if="index.status === 'READY'"
              type="success"
              plain
              @click="activateIndex(index.id)"
            >
              激活此快照
            </el-button>
            <el-button
              v-if="index.status === 'DEPRECATED' || index.status === 'FAILED'"
              type="danger"
              plain
              @click="deleteIndex(index.id)"
            >
              删除
            </el-button>
          </el-card>
        </el-timeline-item>
      </el-timeline>
    </template>

    <template v-else-if="props.view === 'playground'">
      <div class="playground-grid">
        <el-card shadow="never">
          <template #header><strong>检索实验</strong></template>
          <el-select v-model="selectedKnowledgeId" placeholder="选择知识库">
            <el-option
              v-for="kb in knowledgeBases"
              :key="kb.id"
              :label="kb.name"
              :value="kb.id"
            />
          </el-select>
          <el-input
            v-model="question"
            type="textarea"
            :rows="5"
            class="question-input"
          />
          <div class="option-grid"><label v-for="(_, key) in playgroundOptions" :key="key">{{ key }}<el-input-number v-model="playgroundOptions[key]" :min="1" :max="100" /></label></div>
          <el-button type="primary" :disabled="!selectedKnowledgeId" @click="askPlayground">运行完整 RAG</el-button>
        </el-card>
        <el-card shadow="never">
          <template #header><strong>Answer</strong></template>
          <p class="answer">{{ selectedTrace?.answer || '运行后在此查看答案、检索阶段与来源。' }}</p>
          <el-divider>Sources</el-divider><pre>{{ formatJson(selectedTrace?.sources ?? []) }}</pre>
        </el-card>
      </div>
      <el-tabs v-if="selectedTrace" class="trace-tabs">
        <el-tab-pane label="Retrieval"><pre>{{ formatJson(selectedTrace.retrieval_result) }}</pre></el-tab-pane>
        <el-tab-pane label="Rerank"><pre>{{ formatJson(selectedTrace.rerank_result) }}</pre></el-tab-pane>
        <el-tab-pane label="Context"><pre>{{ formatJson(selectedTrace.selected_context) }}</pre></el-tab-pane>
        <el-tab-pane label="Prompt"><pre>{{ selectedTrace.prompt }}</pre></el-tab-pane>
        <el-tab-pane label="Citations"><pre>{{ formatJson(selectedTrace.citation_result) }}</pre></el-tab-pane>
      </el-tabs>
    </template>

    <template v-else-if="props.view === 'traces'">
      <el-table :data="traces" @row-click="(row: TraceSummary) => openTrace(row.trace_id)">
        <el-table-column prop="mode" label="模式" width="120" />
        <el-table-column
          prop="question"
          label="问题"
          min-width="260"
          show-overflow-tooltip
        />
        <el-table-column prop="status" label="状态" width="120"><template #default="scope"><el-tag :type="statusType(scope.row.status)">{{ scope.row.status }}</el-tag></template></el-table-column>
        <el-table-column label="耗时" width="110"><template #default="scope">{{ scope.row.latency.total_ms ?? '—' }} ms</template></el-table-column>
        <el-table-column label="时间" min-width="180"><template #default="scope">{{ formatDate(scope.row.created_at) }}</template></el-table-column>
      </el-table>
      <el-drawer v-model="traceDrawerOpen" title="RAG Trace" size="70%">
        <template v-if="selectedTrace"><h3>{{ selectedTrace.question }}</h3><p class="answer">{{ selectedTrace.answer }}</p><pre>{{ formatJson(selectedTrace) }}</pre></template>
      </el-drawer>
    </template>

    <template v-else-if="props.view === 'configuration'">
      <h2>全局模型</h2>
      <div class="model-grid"><el-card v-for="model in models" :key="model.id" shadow="never"><div class="index-title"><b>{{ model.model_type }}</b><el-tag :type="model.enabled ? 'success' : 'info'">{{ model.enabled ? 'ENABLED' : 'DISABLED' }}</el-tag></div><h3>{{ model.model_name }}</h3><p>{{ model.provider }}</p><small>API Key {{ model.api_key_configured ? '已配置' : '未配置' }}</small></el-card></div>
      <h2>Prompt 模板</h2>
      <el-card
        v-for="prompt in prompts"
        :key="prompt.id"
        shadow="never"
        class="prompt-card"
      >
        <div class="index-title"><b>{{ prompt.name }} · v{{ prompt.version }}</b><el-tag v-if="prompt.active" type="success">ACTIVE</el-tag></div><pre>{{ prompt.content }}</pre>
      </el-card>
    </template>

    <template v-else>
      <el-card shadow="never">
        <template #header><strong>服务访问凭据</strong></template><p class="muted">生产环境应由反向代理注入凭据。这里仅支持当前浏览器会话，不写入持久存储。</p><el-input
          v-model="serviceToken"
          type="password"
          show-password
          placeholder="留空以清除"
          @change="saveToken"
        />
      </el-card>
    </template>
  </section>
</template>

<style scoped>
.studio-content { display: grid; gap: 18px; }
.metric-grid, .model-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; }
.metric-grid article { padding: 22px; border: 1px solid var(--border); border-radius: 16px; background: white; }
.metric-grid span, .metric-grid small, .resource-row small, .index-title small { display: block; color: var(--muted); }
.metric-grid strong { display: block; margin: 8px 0; color: var(--accent); font-family: Georgia, serif; font-size: 36px; }
.split-panel, .playground-grid { display: grid; grid-template-columns: minmax(280px, 0.8fr) minmax(380px, 1.2fr); gap: 18px; }
.resource-row { display: flex; align-items: center; justify-content: space-between; width: 100%; padding: 14px 4px; border: 0; border-bottom: 1px solid var(--border); background: transparent; text-align: left; cursor: pointer; }
.toolbar { display: flex; align-items: center; gap: 14px; padding: 14px; border: 1px solid var(--border); border-radius: 14px; background: white; }
.toolbar .el-select { min-width: 240px; }
.upload-button { padding: 9px 15px; border-radius: 8px; color: white; background: var(--accent); cursor: pointer; }
.upload-button input { display: none; }
.preview-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.scroll-list { max-height: 430px; overflow: auto; }
pre { max-height: 480px; margin: 0 0 10px; padding: 12px; overflow: auto; border-radius: 8px; white-space: pre-wrap; overflow-wrap: anywhere; color: #33443b; background: #f4f7f3; font: 12px/1.55 "SFMono-Regular", Consolas, monospace; }
.index-title { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; }
.muted { color: var(--muted); }
.question-input { margin: 14px 0; }
.option-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 16px; }
.option-grid label { color: var(--muted); font-size: 12px; }
.option-grid .el-input-number { width: 100%; margin-top: 4px; }
.answer { white-space: pre-wrap; font-size: 16px; line-height: 1.8; }
.trace-tabs, .prompt-card { padding: 12px; border: 1px solid var(--border); border-radius: 14px; background: white; }
h2 { margin: 8px 0 0; }
@media (max-width: 980px) { .metric-grid, .model-grid { grid-template-columns: 1fr 1fr; } .preview-grid, .split-panel, .playground-grid { grid-template-columns: 1fr; } }
@media (max-width: 620px) { .metric-grid, .model-grid, .option-grid { grid-template-columns: 1fr; } .toolbar { align-items: stretch; flex-direction: column; } }
</style>

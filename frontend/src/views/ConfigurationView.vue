<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";

import { useAdminStore } from "@/composables/adminStore";
import type { ModelConfig, PromptTemplate } from "@/types/admin";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import EmptyState from "@/components/ui/EmptyState.vue";
import CodeBlock from "@/components/ui/CodeBlock.vue";

const store = useAdminStore();

const activeTab = ref("models");
const selectedPrompt = ref<PromptTemplate | null>(null);

const TYPE_LABELS: Record<string, string> = {
  LLM: "LLM",
  EMBEDDING: "Embedding",
  RERANK: "Rerank",
  OCR: "OCR",
  VISION: "Vision",
};

const EDITABLE_TYPES = new Set(["LLM", "RERANK"]);

const currentPrompt = computed(
  () =>
    selectedPrompt.value ??
    (store.prompts.find((prompt) => prompt.active) ?? store.prompts[0] ?? null),
);

function pickPrompt(prompt: PromptTemplate): void {
  selectedPrompt.value = prompt;
}

/* ---------- model edit dialog ---------- */

const editDialogOpen = ref(false);
const editingModel = ref<ModelConfig | null>(null);

const editForm = reactive({
  model_name: "",
  base_url: "",
  enabled: true,
  thinking_enabled: false,
  temperature: 0.1,
  max_tokens: 4096,
});

function openEdit(model: ModelConfig): void {
  editingModel.value = model;
  const thinking = model.parameters.thinking as { type?: string } | undefined;
  editForm.model_name = model.model_name;
  editForm.base_url = model.base_url;
  editForm.enabled = model.enabled;
  editForm.thinking_enabled = thinking?.type !== "disabled";
  editForm.temperature = Number(model.parameters.temperature ?? 0.1);
  editForm.max_tokens = Number(model.parameters.max_tokens ?? 4096);
  editDialogOpen.value = true;
}

async function saveEdit(): Promise<void> {
  const model = editingModel.value;
  if (!model) return;
  const isLlm = model.model_type === "LLM";
  const payload: {
    model_name: string;
    base_url: string;
    enabled: boolean;
    parameters?: Record<string, unknown>;
  } = {
    model_name: editForm.model_name.trim(),
    base_url: editForm.base_url.trim(),
    enabled: editForm.enabled,
  };
  if (isLlm) {
    payload.parameters = {
      ...model.parameters,
      temperature: editForm.temperature,
      max_tokens: editForm.max_tokens,
      thinking: { type: editForm.thinking_enabled ? "enabled" : "disabled" },
    };
  }
  await store.saveModelConfig(model.id, payload);
  if (!store.error) editDialogOpen.value = false;
}

/* ---------- rag retrieval config ---------- */

const RAG_FIELDS = [
  { key: "vector_top_k", label: "向量召回 Top K", desc: "向量检索返回的候选数量" },
  { key: "bm25_top_k", label: "BM25 召回 Top K", desc: "关键词检索返回的候选数量" },
  { key: "fusion_top_k", label: "RRF 融合 Top K", desc: "双路召回融合后进入重排的数量" },
  { key: "rerank_top_n", label: "Rerank Top N", desc: "重排后保留的候选数量" },
  { key: "context_max_chunks", label: "上下文最大片段数", desc: "送入 LLM 的上下文章节上限" },
] as const;

type RagFieldKey = (typeof RAG_FIELDS)[number]["key"];

const ragForm = reactive<Record<RagFieldKey, number>>({
  vector_top_k: 10,
  bm25_top_k: 10,
  fusion_top_k: 20,
  rerank_top_n: 10,
  context_max_chunks: 8,
});

watch(
  () => store.ragConfig,
  (config) => {
    if (!config) return;
    for (const field of RAG_FIELDS) {
      ragForm[field.key] = config[field.key];
    }
  },
  { immediate: true },
);

async function saveRagConfig(): Promise<void> {
  await store.saveRagConfig({ ...ragForm });
}

onMounted(() => {
  if (!store.ragConfig) void store.loadRagConfig().catch(() => undefined);
});
</script>

<template>
  <div class="configuration-view">
    <el-tabs v-model="activeTab" class="config-tabs">
      <el-tab-pane label="Models" name="models">
        <div class="panel">
          <el-table v-if="store.models.length" :data="store.models">
            <el-table-column label="Type" width="110">
              <template #default="scope">
                <span class="type-badge">{{ TYPE_LABELS[scope.row.model_type] ?? scope.row.model_type }}</span>
              </template>
            </el-table-column>
            <el-table-column label="Model" min-width="200">
              <template #default="scope">
                <div class="cell-primary">
                  <b>{{ scope.row.model_name }}</b>
                  <small>{{ scope.row.provider }}</small>
                </div>
              </template>
            </el-table-column>
            <el-table-column
              prop="name"
              label="Config Name"
              min-width="160"
              show-overflow-tooltip
            />
            <el-table-column label="Base URL" min-width="220" show-overflow-tooltip>
              <template #default="scope">
                <span class="mono params-preview">{{ scope.row.base_url }}</span>
              </template>
            </el-table-column>
            <el-table-column label="API Key" width="110">
              <template #default="scope">
                <span :class="scope.row.api_key_configured ? 'key-ok' : 'key-missing'">
                  {{ scope.row.api_key_configured ? "已配置" : "未配置" }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="Status" width="110">
              <template #default="scope">
                <StatusBadge :status="scope.row.enabled ? 'ENABLED' : 'DISABLED'" />
              </template>
            </el-table-column>
            <el-table-column label="Parameters" min-width="200">
              <template #default="scope">
                <span class="mono params-preview">{{ store.formatJson(scope.row.parameters) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="90" fixed="right">
              <template #default="scope">
                <el-button
                  v-if="EDITABLE_TYPES.has(scope.row.model_type)"
                  link
                  type="primary"
                  @click="openEdit(scope.row)"
                >
                  编辑
                </el-button>
              </template>
            </el-table-column>
          </el-table>
          <EmptyState
            v-else
            title="暂无模型配置"
            description="全局模型由后端配置管理，用于 Embedding、Rerank 与 LLM 生成。"
          />
        </div>
      </el-tab-pane>

      <el-tab-pane label="RAG 检索" name="rag-config">
        <div class="panel">
          <header class="panel-header">
            <div>
              <h3>检索配置</h3>
              <span class="sub">
                保存后下一轮对话立即生效（每轮快照读取），Playground 的手动参数仍可临时覆盖
              </span>
            </div>
          </header>
          <div class="panel-body rag-form">
            <div v-for="field in RAG_FIELDS" :key="field.key" class="param-row">
              <div class="param-label">
                <label>{{ field.label }}</label>
                <p class="desc">{{ field.desc }}</p>
              </div>
              <el-input-number v-model="ragForm[field.key]" :min="1" :max="100" />
            </div>
            <div class="rag-actions">
              <el-button type="primary" :loading="store.loading" @click="saveRagConfig">
                保存配置
              </el-button>
            </div>
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane label="Prompts" name="prompts">
        <div v-if="store.prompts.length" class="prompt-layout">
          <section class="panel prompt-list">
            <header class="panel-header">
              <div>
                <h3>Prompt Templates</h3>
                <span class="sub">{{ store.prompts.length }} 个模板</span>
              </div>
            </header>
            <div class="prompt-rows">
              <button
                v-for="prompt in store.prompts"
                :key="prompt.id"
                type="button"
                class="prompt-row"
                :class="{ active: currentPrompt?.id === prompt.id }"
                @click="pickPrompt(prompt)"
              >
                <div class="cell-primary">
                  <b>{{ prompt.name }}</b>
                  <small>v{{ prompt.version }}</small>
                </div>
                <StatusBadge v-if="prompt.active" status="ACTIVE" />
              </button>
            </div>
          </section>

          <section class="panel prompt-editor">
            <template v-if="currentPrompt">
              <header class="panel-header">
                <div>
                  <h3>{{ currentPrompt.name }}</h3>
                  <span class="sub">v{{ currentPrompt.version }}</span>
                </div>
                <StatusBadge v-if="currentPrompt.active" status="ACTIVE" />
              </header>
              <div class="panel-body">
                <h4 class="section-label">System Prompt</h4>
                <CodeBlock :code="currentPrompt.content" max-height="480px" />
              </div>
            </template>
            <EmptyState v-else title="未选择模板" description="从左侧选择一个 Prompt 模板查看内容。" />
          </section>
        </div>
        <div v-else class="panel">
          <EmptyState
            title="暂无 Prompt 模板"
            description="Prompt 模板由后端版本化管理，当前知识库还没有可用的模板。"
          />
        </div>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="editDialogOpen" title="编辑模型配置" width="520px">
      <template v-if="editingModel">
        <div class="dialog-form">
          <div class="dialog-field">
            <label>模型名称</label>
            <el-input v-model="editForm.model_name" placeholder="例如 glm-5.2" />
          </div>
          <div class="dialog-field">
            <label>Base URL</label>
            <el-input v-model="editForm.base_url" placeholder="以 http(s):// 开头并以 / 结尾" />
          </div>
          <template v-if="editingModel.model_type === 'LLM'">
            <div class="dialog-field">
              <label>思考模式（LLM Thinking）</label>
              <el-switch
                v-model="editForm.thinking_enabled"
                active-text="开启"
                inactive-text="关闭"
              />
              <p class="desc">关闭可加快响应；开启会消耗部分输出 Token 预算</p>
            </div>
            <div class="dialog-field">
              <label>Temperature</label>
              <el-input-number
                v-model="editForm.temperature"
                :min="0"
                :max="2"
                :step="0.1"
              />
            </div>
            <div class="dialog-field">
              <label>Max Tokens</label>
              <el-input-number
                v-model="editForm.max_tokens"
                :min="100"
                :max="100000"
                :step="256"
              />
            </div>
          </template>
          <div class="dialog-field">
            <label>启用</label>
            <el-switch v-model="editForm.enabled" active-text="启用" inactive-text="停用" />
          </div>
          <div class="dialog-field">
            <label>当前参数快照</label>
            <CodeBlock :code="store.formatJson(editingModel.parameters)" max-height="160px" />
          </div>
        </div>
      </template>
      <template #footer>
        <el-button @click="editDialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="store.loading" @click="saveEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.config-tabs :deep(.el-tabs__header) {
  margin-bottom: 14px;
}

.type-badge {
  display: inline-block;
  padding: 1px 8px;
  border: 1px solid var(--border-default);
  border-radius: 5px;
  color: var(--text-secondary);
  background: var(--bg-subtle);
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 18px;
}

.key-ok {
  color: var(--success);
  font-size: 13px;
}

.key-missing {
  color: var(--text-tertiary);
  font-size: 13px;
}

.params-preview {
  display: inline-block;
  overflow: hidden;
  max-width: 100%;
  color: var(--text-tertiary);
  text-overflow: ellipsis;
  vertical-align: middle;
  white-space: nowrap;
}

.prompt-layout {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  gap: var(--space-content-gap);
  align-items: start;
}

.prompt-rows {
  display: grid;
}

.prompt-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 18px;
  border: 0;
  border-top: 1px solid var(--border-subtle);
  background: transparent;
  text-align: left;
  cursor: pointer;
  transition: background-color var(--transition-fast);
}

.prompt-row:first-child {
  border-top: 0;
}

.prompt-row:hover {
  background: var(--bg-hover);
}

.prompt-row.active {
  background: var(--brand-subtle);
}

.section-label {
  margin-bottom: 8px;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 600;
}

.rag-form {
  display: grid;
  gap: 16px;
  max-width: 480px;
}

.param-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.param-label label {
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 500;
}

.desc {
  margin: 2px 0 0;
  color: var(--text-tertiary);
  font-size: 12px;
}

.rag-actions {
  display: flex;
  justify-content: flex-end;
}

.dialog-form {
  display: grid;
  gap: 16px;
}

.dialog-field {
  display: grid;
  gap: 6px;
}

.dialog-field > label {
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 500;
}

@media (max-width: 1000px) {
  .prompt-layout {
    grid-template-columns: 1fr;
  }
}
</style>

<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { ElMessageBox } from "element-plus";

import { useAdminStore } from "@/composables/adminStore";
import type { ModelConfig, PromptTemplate } from "@/types/admin";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import EmptyState from "@/components/ui/EmptyState.vue";
import CodeBlock from "@/components/ui/CodeBlock.vue";
import RagConfigurationView from "@/views/RagConfigurationView.vue";
import SettingsView from "@/views/SettingsView.vue";

const props = defineProps<{ navigationQuery?: string }>();
const store = useAdminStore();

const CONFIG_TABS = new Set(["models", "prompts", "rag", "credentials"]);

function tabFromQuery(query = ""): string {
  const tab = new URLSearchParams(query).get("tab") ?? "models";
  return CONFIG_TABS.has(tab) ? tab : "models";
}

const activeTab = ref(tabFromQuery(props.navigationQuery));
watch(
  () => props.navigationQuery,
  (query) => {
    activeTab.value = tabFromQuery(query);
  },
);
const selectedPrompt = ref<PromptTemplate | null>(null);
const TYPE_LABELS: Record<string, string> = {
  LLM: "LLM",
  EMBEDDING: "Embedding",
  RERANK: "Rerank",
  OCR: "OCR",
  VISION: "Vision",
};

const EDITABLE_TYPES = new Set(["LLM", "RERANK", "OCR", "VISION", "EMBEDDING"]);

const PROVIDER_DEFAULT_BASE_URLS: Readonly<Record<string, string>> = {
  siliconflow: "https://api.siliconflow.cn/v1/",
  zhipu: "https://open.bigmodel.cn/api/paas/v4/",
};

const isBuildBoundType = computed(
  () =>
    editingModel.value !== null &&
    ["OCR", "VISION", "EMBEDDING"].includes(editingModel.value.model_type),
);

const isEmbeddingType = computed(
  () => editingModel.value !== null && editingModel.value.model_type === "EMBEDDING",
);

const currentPrompt = computed(
  () => selectedPrompt.value ?? (store.prompts[0] ?? null),
);

const runtimePrompts = computed(() =>
  store.prompts.filter((prompt) => prompt.source === "RUNTIME"),
);

const databasePrompts = computed(() =>
  store.prompts.filter((prompt) => prompt.source !== "RUNTIME"),
);

function pickPrompt(prompt: PromptTemplate): void {
  selectedPrompt.value = prompt;
}

/* ---------- model edit dialog ---------- */

const editDialogOpen = ref(false);
const editingModel = ref<ModelConfig | null>(null);
const probing = ref(false);
const probeResult = ref<{ ok: boolean; detail: string } | null>(null);

const editForm = reactive({
  provider: "",
  model_name: "",
  base_url: "",
  enabled: true,
  thinking_enabled: false,
  temperature: 0.1,
  max_tokens: 4096,
  api_key: "",
});

const configuredProvider = computed(() => editForm.provider.trim() || "当前 Provider");

const availableProviders = computed(() => {
  const supported = editingModel.value?.supported_providers ?? [];
  return supported.length ? supported : [editingModel.value?.provider ?? ""];
});

const modelNamePlaceholder = computed(
  () => `请输入 ${configuredProvider.value} 在此地址可用的模型标识`,
);

const modelConfigurationHint = computed(() => {
  const baseUrl = editForm.base_url.trim() || "未配置";
  return `当前 Provider：${configuredProvider.value}；请求地址：${baseUrl}。模型名称必须与该 Provider 和地址实际支持的模型一致。`;
});

const apiKeyPlaceholder = computed(
  () => `输入 ${configuredProvider.value} 的 API 密钥；留空则保持现状`,
);

function defaultBaseUrlForProvider(provider: string): string | undefined {
  return PROVIDER_DEFAULT_BASE_URLS[provider.trim().toLowerCase()];
}

function handleProviderChange(provider: string): void {
  const nextBaseUrl = defaultBaseUrlForProvider(provider);
  if (nextBaseUrl) editForm.base_url = nextBaseUrl;
}

function openEdit(model: ModelConfig): void {
  editingModel.value = model;
  const thinking = model.parameters.thinking as { type?: string } | undefined;
  editForm.provider = model.provider;
  editForm.model_name = model.model_name;
  editForm.base_url = model.base_url;
  editForm.enabled = model.enabled;
  editForm.thinking_enabled = thinking?.type !== "disabled";
  editForm.temperature = Number(model.parameters.temperature ?? 0.1);
  editForm.max_tokens = Number(model.parameters.max_tokens ?? 4096);
  editForm.api_key = "";
  probeResult.value = null;
  editDialogOpen.value = true;
}

async function runProbe(): Promise<void> {
  const model = editingModel.value;
  if (!model) return;
  probing.value = true;
  probeResult.value = null;
  try {
    probeResult.value = await store.probeModel(model.id);
  } catch (error) {
    probeResult.value = {
      ok: false,
      detail: error instanceof Error ? error.message : "探测失败",
    };
  } finally {
    probing.value = false;
  }
}

async function clearStoredKey(): Promise<void> {
  const model = editingModel.value;
  if (!model) return;
  await store.clearModelApiKey(model.id);
  if (!store.error) {
    editingModel.value = store.models.find((item) => item.id === model.id) ?? model;
    probeResult.value = null;
  }
}

async function saveEdit(): Promise<void> {
  const model = editingModel.value;
  if (!model) return;
  const isLlm = model.model_type === "LLM";
  const payload: {
    provider: string;
    model_name: string;
    base_url: string;
    api_key?: string;
    enabled: boolean;
    parameters?: Record<string, unknown>;
    rebuild_all_confirmed?: boolean;
  } = {
    provider: editForm.provider.trim(),
    model_name: editForm.model_name.trim(),
    base_url: editForm.base_url.trim(),
    enabled: editForm.enabled,
  };
  const newKey = editForm.api_key.trim();
  if (newKey) payload.api_key = newKey;
  if (isLlm) {
    payload.parameters = {
      ...model.parameters,
      temperature: editForm.temperature,
      max_tokens: editForm.max_tokens,
      thinking: { type: editForm.thinking_enabled ? "enabled" : "disabled" },
    };
  }
  const embeddingIdentityChanged =
    model.model_type === "EMBEDDING" &&
    (payload.provider !== model.provider ||
      payload.model_name !== model.model_name ||
      payload.base_url !== model.base_url);
  if (embeddingIdentityChanged) {
    try {
      await ElMessageBox.confirm(
        "修改 Embedding Provider、模型或 Base URL 会改变向量空间：保存后系统会立即对全部知识库发起 RAG 全量重建（构建完成前检索继续使用旧模型向量）。",
        "确认全量重建 RAG 索引",
        { type: "warning", confirmButtonText: "确认修改并全量重建", cancelButtonText: "取消" },
      );
    } catch {
      return;
    }
    payload.rebuild_all_confirmed = true;
  }
  await store.saveModelConfig(model.id, payload);
  if (store.error) return;
  if (!store.error) editDialogOpen.value = false;
}

</script>

<template>
  <div class="configuration-view">
    <el-tabs v-model="activeTab" class="config-tabs">
      <el-tab-pane label="模型配置（Models）" name="models">
        <div class="panel">
          <el-table v-if="store.models.length" :data="store.models">
            <el-table-column label="类型（Type）" width="110">
              <template #default="scope">
                <span class="type-badge">{{ TYPE_LABELS[scope.row.model_type] ?? scope.row.model_type }}</span>
              </template>
            </el-table-column>
            <el-table-column label="模型（Model）" min-width="200">
              <template #default="scope">
                <div class="cell-primary">
                  <b>{{ scope.row.model_name }}</b>
                  <small>{{ scope.row.provider }}</small>
                </div>
              </template>
            </el-table-column>
            <el-table-column
              prop="name"
              label="配置名称（Config Name）"
              min-width="160"
              show-overflow-tooltip
            />
            <el-table-column label="请求地址（Base URL）" min-width="220" show-overflow-tooltip>
              <template #default="scope">
                <span class="mono params-preview">{{ scope.row.base_url }}</span>
              </template>
            </el-table-column>
            <el-table-column label="API 密钥（API Key）" width="140">
              <template #default="scope">
                <span :class="scope.row.api_key_configured ? 'key-ok' : 'key-missing'">
                  {{
                    scope.row.api_key_source === "DATABASE"
                      ? "已配置 · 数据库"
                      : scope.row.api_key_source === "ENV"
                        ? "已配置 · 环境变量"
                        : "未配置"
                  }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="状态（Status）" width="110">
              <template #default="scope">
                <StatusBadge :status="scope.row.enabled ? 'ENABLED' : 'DISABLED'" />
              </template>
            </el-table-column>
            <el-table-column label="参数（Parameters）" min-width="200">
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

      <el-tab-pane label="Prompt 模板（Prompts）" name="prompts">
        <div class="panel prompt-scope-note">
          <p>
            运行时内置模板是 Chat 主路径（文档问答、澄清与切换）实际使用的 Prompt，与解析逻辑强耦合，
            由代码版本化管理（<span class="mono">backend/app/rag/prompt.py</span>），此处只读展示；
            模板中的 <span class="mono">__PRODUCT_NAME__</span> /
            <span class="mono">__SCOPE_DESCRIPTION__</span> 占位符按请求注入。
            数据库模板仅在片段检索降级路径（CHUNK_RAG）生效。
          </p>
        </div>
        <div v-if="store.prompts.length" class="prompt-layout">
          <section class="panel prompt-list">
            <header class="panel-header">
              <div>
                <h3>Prompt 模板（Prompt Templates）</h3>
                <span class="sub">{{ store.prompts.length }} 个模板</span>
              </div>
            </header>
            <div class="prompt-rows">
              <template v-if="runtimePrompts.length">
                <div class="prompt-group-label">运行时内置（Chat 主路径）</div>
                <button
                  v-for="prompt in runtimePrompts"
                  :key="prompt.id"
                  type="button"
                  class="prompt-row"
                  :class="{ active: currentPrompt?.id === prompt.id }"
                  @click="pickPrompt(prompt)"
                >
                  <div class="cell-primary">
                    <b>{{ prompt.name }}</b>
                    <small>v{{ prompt.version }}</small>
                    <small v-if="prompt.description" class="prompt-desc">
                      {{ prompt.description }}
                    </small>
                  </div>
                  <StatusBadge status="RUNTIME" />
                </button>
              </template>
              <template v-if="databasePrompts.length">
                <div class="prompt-group-label">数据库模板（降级路径）</div>
                <button
                  v-for="prompt in databasePrompts"
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
              </template>
            </div>
          </section>

          <section class="panel prompt-editor">
            <template v-if="currentPrompt">
              <header class="panel-header">
                <div>
                  <h3>{{ currentPrompt.name }}</h3>
                  <span class="sub">v{{ currentPrompt.version }}</span>
                  <p v-if="currentPrompt.description" class="prompt-role">
                    {{ currentPrompt.description }}
                  </p>
                </div>
                <StatusBadge
                  v-if="currentPrompt.source === 'RUNTIME'"
                  status="RUNTIME"
                />
                <StatusBadge v-else-if="currentPrompt.active" status="ACTIVE" />
              </header>
              <div class="panel-body">
                <h4 class="section-label">系统 Prompt（System Prompt）</h4>
                <CodeBlock :code="currentPrompt.content" max-height="480px" />
              </div>
            </template>
            <EmptyState v-else title="未选择模板" description="从左侧选择一个 Prompt 模板查看内容。" />
          </section>
        </div>
        <div v-else class="panel">
          <EmptyState
            title="暂无 Prompt 模板"
            description="数据库中没有版本化管理的 Prompt 模板；文档路由主路径的 Prompt 由代码版本化管理。"
          />
        </div>
      </el-tab-pane>
      <el-tab-pane label="RAG 检索参数" name="rag" lazy>
        <RagConfigurationView />
      </el-tab-pane>
      <el-tab-pane label="服务凭证" name="credentials" lazy>
        <SettingsView />
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="editDialogOpen" title="编辑模型配置" width="520px">
      <template v-if="editingModel">
        <div class="dialog-form">
          <div class="dialog-field">
            <label>服务商（Provider）</label>
            <el-select
              v-model="editForm.provider"
              placeholder="选择服务商（Provider）"
              @change="handleProviderChange"
            >
              <el-option
                v-for="provider in availableProviders"
                :key="provider"
                :label="provider"
                :value="provider"
              />
            </el-select>
            <p class="desc">
              切换 Provider 时会自动带入该服务商的默认 Base URL；如使用私有网关，可再手动修改。
            </p>
          </div>
          <div class="dialog-field">
            <label>模型名称</label>
            <el-input v-model="editForm.model_name" :placeholder="modelNamePlaceholder" />
            <p class="desc">{{ modelConfigurationHint }}</p>
          </div>
          <div class="dialog-field">
            <label>请求地址（Base URL）</label>
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
              <label>温度（Temperature）</label>
              <el-input-number
                v-model="editForm.temperature"
                :min="0"
                :max="2"
                :step="0.1"
              />
            </div>
            <div class="dialog-field">
              <label>最大 Token 数（Max Tokens）</label>
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
          <div v-if="isBuildBoundType" class="dialog-field">
            <div class="build-bound-hint">
              该模型在索引构建时消费：更改与密钥写入在<b>下一次索引构建</b>生效，
              已有 Active Index 不受影响；保存后所有知识库将标记“需要重建”。
              <template v-if="isEmbeddingType">
                <br />
                修改 Provider、模型名称或 Base URL 时，服务端会先执行维度契约探测（输出必须为冻结维度
                1024）；确认修改后系统会<b>立即对全部知识库发起 RAG 全量重建</b>。
              </template>
            </div>
          </div>
          <div class="dialog-field">
            <label>API 密钥（API Key；数据库密钥优先于环境变量）</label>
            <el-input
              v-model="editForm.api_key"
              type="password"
              show-password
              :placeholder="apiKeyPlaceholder"
              autocomplete="new-password"
            />
            <div class="key-actions">
              <span class="desc">
                当前：{{
                  editingModel.api_key_source === "DATABASE"
                    ? "数据库密钥（加密存储）"
                    : editingModel.api_key_source === "ENV"
                      ? "环境变量密钥"
                      : "未配置"
                }}；保存时填写则写入数据库密钥
              </span>
              <el-button
                v-if="editingModel.api_key_source === 'DATABASE'"
                link
                type="danger"
                @click="clearStoredKey"
              >
                清除数据库密钥（回退环境变量）
              </el-button>
            </div>
          </div>
          <div class="dialog-field">
            <label>连通性测试</label>
            <div class="key-actions">
              <el-button :loading="probing" @click="runProbe">测试连接</el-button>
              <span v-if="probeResult" :class="probeResult.ok ? 'key-ok' : 'key-missing'">
                {{ probeResult.ok ? "连通正常" : probeResult.detail }}
              </span>
            </div>
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

.prompt-scope-note {
  margin-bottom: var(--space-content-gap);
  padding: 12px 18px;
}

.prompt-scope-note p {
  margin: 0;
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.6;
}

.key-ok {
  color: var(--success);
  font-size: 13px;
}

.key-missing {
  color: var(--text-tertiary);
  font-size: 13px;
}

.key-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.build-bound-hint {
  padding: 8px 12px;
  border: 1px solid var(--border-default);
  border-radius: 6px;
  background: var(--bg-subtle);
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.6;
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

.prompt-group-label {
  padding: 8px 18px 4px;
  color: var(--text-tertiary);
  background: var(--bg-subtle);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
}

.prompt-desc {
  display: block;
  margin-top: 2px;
  max-width: 150px;
  overflow: hidden;
  color: var(--text-tertiary);
  font-size: 11px;
  line-height: 1.5;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.prompt-role {
  margin: 6px 0 0;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.6;
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

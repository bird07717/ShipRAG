<script setup lang="ts">
import { computed, onUnmounted, reactive, ref, watch } from "vue";
import { ElMessageBox } from "element-plus";
import { EditPen } from "@element-plus/icons-vue";

import { useAdminStore } from "@/composables/adminStore";
import type { ModelConfig, PromptTemplate } from "@/types/admin";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import EmptyState from "@/components/ui/EmptyState.vue";
import CodeBlock from "@/components/ui/CodeBlock.vue";
import RagConfigurationView from "@/views/RagConfigurationView.vue";
import SettingsView from "@/views/SettingsView.vue";
import { replaceViewQuery } from "@/utils/viewQuery";

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
watch(activeTab, (tab) => replaceViewQuery("configuration", { tab }));
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
  dashscope: "https://dashscope.aliyuncs.com/compatible-mode/v1/",
  siliconflow: "https://api.siliconflow.cn/v1/",
  zhipu: "https://open.bigmodel.cn/api/paas/v4/",
};

const PROVIDER_LABELS: Readonly<Record<string, string>> = {
  dashscope: "千问（阿里云百炼 / DashScope）",
  siliconflow: "硅基流动（SiliconFlow）",
  zhipu: "智谱 AI（Zhipu）",
};

function providerLabel(provider: string): string {
  return PROVIDER_LABELS[provider.trim().toLowerCase()] ?? provider;
}

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
const saving = ref(false);
const clearingKey = ref(false);
const editError = ref("");
let editGeneration = 0;
let probeGeneration = 0;

const editForm = reactive({
  provider: "",
  model_name: "",
  base_url: "",
  enabled: true,
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

function resetProbe(): void {
  probeGeneration++;
  probing.value = false;
  probeResult.value = null;
}

function invalidateEditor(): void {
  editGeneration++;
  resetProbe();
  saving.value = false;
  clearingKey.value = false;
  editError.value = "";
  editForm.api_key = "";
}

function isCurrentEdit(generation: number, modelId: string, credential: string): boolean {
  return generation === editGeneration && editDialogOpen.value
    && editingModel.value?.id === modelId && store.hasServiceCredential
    && store.serviceToken === credential;
}

watch(editDialogOpen, (open) => { if (!open) invalidateEditor(); }, { flush: "sync" });
watch(() => [store.serviceToken, store.hasServiceCredential], () => { editDialogOpen.value = false; }, { flush: "sync" });
watch(() => [editForm.provider, editForm.model_name, editForm.base_url, editForm.enabled, editForm.api_key], resetProbe, { flush: "sync" });
onUnmounted(invalidateEditor);

function openEdit(model: ModelConfig): void {
  if (!store.hasServiceCredential) return;
  invalidateEditor();
  editingModel.value = model;
  editForm.provider = model.provider;
  editForm.model_name = model.model_name;
  editForm.base_url = model.base_url;
  editForm.enabled = model.enabled;
  editForm.api_key = "";
  probeResult.value = null;
  editDialogOpen.value = true;
}

async function runProbe(): Promise<void> {
  const model = editingModel.value;
  if (!model || probing.value || saving.value || clearingKey.value || !store.hasServiceCredential) return;
  const editor = editGeneration;
  const generation = ++probeGeneration;
  const credential = store.serviceToken;
  const isCurrent = () => generation === probeGeneration && isCurrentEdit(editor, model.id, credential);
  probing.value = true;
  probeResult.value = null;
  try {
    const result = await store.probeModel(model.id);
    if (isCurrent()) probeResult.value = result;
  } catch (error) {
    if (isCurrent()) probeResult.value = {
      ok: false,
      detail: error instanceof Error ? error.message : "探测失败",
    };
  } finally {
    if (isCurrent()) probing.value = false;
  }
}

async function clearStoredKey(): Promise<void> {
  const model = editingModel.value;
  if (!model || saving.value || clearingKey.value || !store.hasServiceCredential) return;
  const generation = editGeneration;
  const credential = store.serviceToken;
  clearingKey.value = true;
  editError.value = "";
  resetProbe();
  try {
    const result = await store.clearModelApiKey(model.id);
    if (!isCurrentEdit(generation, model.id, credential)) return;
    if (result.ok && result.listStale) {
      // The key is gone but the list could not be reloaded; the row shown here would be outdated.
      editDialogOpen.value = false;
    } else if (result.ok) {
      editingModel.value = store.models.find((item) => item.id === model.id) ?? model;
      resetProbe();
    } else editError.value = result.error;
  } catch (error) {
    if (isCurrentEdit(generation, model.id, credential)) editError.value = error instanceof Error ? error.message : "密钥清除失败";
  } finally {
    if (isCurrentEdit(generation, model.id, credential)) clearingKey.value = false;
  }
}

async function saveEdit(): Promise<void> {
  const model = editingModel.value;
  if (!model || saving.value || clearingKey.value || !store.hasServiceCredential) return;
  const generation = editGeneration;
  const credential = store.serviceToken;
  const payload: {
    provider: string;
    model_name: string;
    base_url: string;
    api_key?: string;
    enabled: boolean;
    rebuild_all_confirmed?: boolean;
  } = {
    provider: editForm.provider.trim(),
    model_name: editForm.model_name.trim(),
    base_url: editForm.base_url.trim(),
    enabled: editForm.enabled,
  };
  const newKey = editForm.api_key.trim();
  if (newKey) payload.api_key = newKey;
  const embeddingIdentityChanged =
    model.model_type === "EMBEDDING" &&
    (payload.provider !== model.provider ||
      payload.model_name !== model.model_name ||
      payload.base_url !== model.base_url);
  saving.value = true;
  editError.value = "";
  resetProbe();
  try {
    if (embeddingIdentityChanged) {
      try {
        await ElMessageBox.confirm(
          "修改 Embedding Provider、模型或 Base URL 可能改变向量空间。保存前先校验输出维度 1024，通过后为启用且有有效文档的知识库提交重建。切换期间 Assistant 可能暂不可用；完成后须核对活动索引、文档理解确认状态，并用新会话验收。不能保证无中断切换。",
          "确认全量重建 RAG 索引",
          { type: "warning", confirmButtonText: "确认修改并全量重建", cancelButtonText: "取消" },
        );
      } catch {
        return;
      }
      payload.rebuild_all_confirmed = true;
    }
    if (!isCurrentEdit(generation, model.id, credential)) return;
    const result = await store.saveModelConfig(model.id, payload);
    if (!isCurrentEdit(generation, model.id, credential)) return;
    if (result.ok) editDialogOpen.value = false;
    else editError.value = result.error;
  } catch (error) {
    if (isCurrentEdit(generation, model.id, credential)) editError.value = error instanceof Error ? error.message : "保存失败";
  } finally {
    if (isCurrentEdit(generation, model.id, credential)) saving.value = false;
  }
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
            <el-table-column label="已保存参数（Parameters）" min-width="200">
              <template #default="scope">
                <span class="mono params-preview">{{ store.formatJson(scope.row.parameters) }}</span>
                <small v-if="scope.row.model_type === 'LLM'" class="desc">不覆盖 V2 阶段生成参数</small>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="90" fixed="right">
              <template #default="scope">
                <el-tooltip
                  v-if="EDITABLE_TYPES.has(scope.row.model_type)"
                  content="编辑模型配置"
                  placement="top"
                >
                  <el-button
                    link
                    circle
                    type="primary"
                    :icon="EditPen"
                    aria-label="编辑模型配置"
                    @click="openEdit(scope.row)"
                  />
                </el-tooltip>
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
            运行时内置模板是 RAG V2 主路径实际使用的两个 Prompt（查询理解与回答生成），
            与结构化输出和校验逻辑强耦合，
            由代码版本化管理（<span class="mono">backend/app/rag/prompt.py</span>），此处只读展示；
            数据库模板仅保留用于管理和历史查看，不控制 RAG V2 回答。
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
                <div class="prompt-group-label">数据库模板（Playground 路径）</div>
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
        <el-alert
          v-if="editError"
          :title="editError"
          type="error"
          :closable="false"
        />
        <div class="dialog-form">
          <div class="dialog-field">
            <label>服务商（Provider）</label>
            <el-select
              v-model="editForm.provider"
              :disabled="saving || clearingKey"
              placeholder="选择服务商（Provider）"
              @change="handleProviderChange"
            >
              <el-option
                v-for="provider in availableProviders"
                :key="provider"
                :label="providerLabel(provider)"
                :value="provider"
              />
            </el-select>
            <p class="desc">
              切换 Provider 时会自动带入该服务商的默认 Base URL；如使用私有网关，可再手动修改。
            </p>
          </div>
          <div class="dialog-field">
            <label>模型名称</label>
            <el-input
              v-model="editForm.model_name"
              aria-label="模型名称"
              :disabled="saving || clearingKey"
              :placeholder="modelNamePlaceholder"
            />
            <p class="desc">{{ modelConfigurationHint }}</p>
          </div>
          <div class="dialog-field">
            <label>请求地址（Base URL）</label>
            <el-input
              v-model="editForm.base_url"
              aria-label="请求地址"
              :disabled="saving || clearingKey"
              placeholder="以 http(s):// 开头并以 / 结尾"
            />
          </div>
          <template v-if="editingModel.model_type === 'LLM'">
            <div class="dialog-field build-bound-hint">
              <label>V2 生成参数（只读说明）</label>
              <p>温度（Temperature）由代码按阶段控制；最大 Token 数（Max Tokens）由后端阶段预算控制。</p>
              <p>查询理解预算：<span class="mono">M3_RAG_QUERY_INTERPRETER_MAX_TOKENS</span>；技术回答预算：<span class="mono">M3_RAG_ANSWER_MAX_TOKENS</span>。</p>
              <p>技术回答思考开关：<span class="mono">M3_RAG_ANSWER_THINKING_ENABLED</span>；查询理解和非文档回答按代码控制。实际请求仍受模型能力及供应商限制。</p>
              <p>本页不编辑或覆盖这些 V2 参数，也不把已保存 JSON 当作运行时生效值；实际用量及调用情况请核对 Trace。修改后端环境配置需重建相应容器。</p>
            </div>
          </template>
          <div class="dialog-field">
            <label>启用</label>
            <el-switch
              v-model="editForm.enabled"
              :disabled="saving || clearingKey"
              active-text="启用"
              inactive-text="停用"
            />
            <p v-if="['LLM', 'RERANK', 'EMBEDDING'].includes(editingModel.model_type)" class="desc">停用会影响 RAG / Assistant 的服务就绪，不是仅关闭某个可选功能；请核对上线检查清单。</p>
          </div>
          <div v-if="isEmbeddingType" class="dialog-field">
            <div class="build-bound-hint">
              <p>Embedding 同时用于索引向量构建和查询向量生成。查询侧每轮读取配置，密钥变更可影响后续查询；已有索引向量不会自动转换。</p>
              <p>修改 Provider、模型名称或 Base URL 需确认重建，并先通过输出维度 1024 校验；保存后为启用且有有效文档的知识库提交重建。切换或重建失败期间 Assistant 可能暂不可用，不保证继续使用完整的旧模型配置。</p>
              <p>新索引保存实际构建模型的供应商、名称、地址和维度。查询与卡片生成会校验身份；不匹配时停止，不改用另一模型检索旧向量。完成后检查活动索引、文档理解生成/确认及上线就绪，再用新会话试问；旧会话不会自动迁移。</p>
            </div>
          </div>
          <div v-else-if="isBuildBoundType" class="dialog-field">
            <div class="build-bound-hint">
              <p>OCR / Vision 在索引 Worker 开始任务时读取配置，分别用于文字识别与图片描述。保存配置会标记知识库待重建，不会立即重做旧文档，也不替换正在运行的任务快照。</p>
              <p>重建会比较实际构建模型指纹：OCR / Vision 身份变化时不复用旧解析和图片描述；身份未变且文件及解析版本一致时才可复用。没有指纹的旧文档首次重建会重新解析。请核对任务模型身份、逐文档处理方式与结果。</p>
              <p>停用数据库配置时可能回退环境模型，并不代表关闭识别功能。已有索引内容不会因保存设置而直接改变。</p>
            </div>
          </div>
          <div v-else class="dialog-field build-bound-hint">
            同一后端和数据库下，新配置从下一轮管理问答与 Assistant 请求读取；已开始的轮次不被中途替换。LLM / Rerank 身份切换不要求重建索引。
          </div>
          <div class="dialog-field">
            <label>API 密钥（API Key；数据库密钥优先于环境变量）</label>
            <el-input
              v-model="editForm.api_key"
              aria-label="模型 API 密钥"
              :disabled="saving || clearingKey"
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
                :loading="clearingKey"
                :disabled="saving"
                @click="clearStoredKey"
              >
                清除数据库密钥（回退环境配置）
              </el-button>
            </div>
            <p class="desc">清除会立即删除已保存的数据库密钥；有环境密钥时回退，否则模型调用可能不可用，不是仅清空草稿输入。</p>
          </div>
          <div class="dialog-field">
            <label>已保存配置的连通性测试</label>
            <div class="key-actions">
              <el-button :loading="probing" :disabled="saving || clearingKey" @click="runProbe">测试已保存配置</el-button>
              <span v-if="probeResult" :class="probeResult.ok ? 'key-ok' : 'key-missing'">
                {{ probeResult.ok ? "已保存配置连通正常" : probeResult.detail }}
              </span>
            </div>
            <p class="desc">仅测试已保存的模型、请求地址和有效密钥，不测试编辑中的草稿。新的模型身份或密钥会在保存时自动校验；连通正常不代表 V2 结构化输出或回答质量已验收。</p>
          </div>
          <div class="dialog-field">
            <label>{{ editingModel.model_type === 'LLM' ? '已保存参数（非 V2 生效快照，只读保留）' : '已保存参数（只读）' }}</label>
            <CodeBlock :code="store.formatJson(editingModel.parameters)" max-height="160px" />
          </div>
        </div>
      </template>
      <template #footer>
        <el-button @click="editDialogOpen = false">取消</el-button>
        <el-button
          type="primary"
          :loading="saving"
          :disabled="clearingKey || !store.hasServiceCredential"
          @click="saveEdit"
        >
          保存
        </el-button>
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

import { reactive } from "vue";
import { ElMessageBox } from "element-plus";

import { adminApi } from "@/api/admin";
import type {
  DocumentItem,
  KnowledgeBase,
  KnowledgeIndex,
  IndexTask,
  ModelConfig,
  ModelConfigUpdatePayload,
  PromptTemplate,
  RagConfig,
  RagConfigUpdatePayload,
  RagTrace,
  TraceSummary,
} from "@/types/admin";

/*
 * Module-level singleton store shared by all admin views.
 * State survives view switches so Documents / Index Build / Trace
 * operate on the same selected knowledge base without refetching.
 */
export const adminStore = reactive({
  /* data */
  knowledgeBases: [] as KnowledgeBase[],
  documents: [] as DocumentItem[],
  indexes: [] as KnowledgeIndex[],
  traces: [] as TraceSummary[],
  models: [] as ModelConfig[],
  ragConfig: null as RagConfig | null,
  prompts: [] as PromptTemplate[],
  elements: [] as Array<Record<string, unknown>>,
  chunks: [] as Array<Record<string, unknown>>,
  latestIndexTasks: {} as Record<string, IndexTask | undefined>,

  /* selection / ui state */
  selectedKnowledgeId: "",
  selectedDocument: null as DocumentItem | null,
  documentDrawerOpen: false,
  selectedTrace: null as RagTrace | null,
  traceDrawerOpen: false,
  knowledgeDrawerOpen: false,
  uploadDrawerOpen: false,
  knowledgeSearch: "",
  knowledgeStatusFilter: "",
  documentSearch: "",
  autoActivate: true,
  loading: false,
  error: "",
  notice: "",

  /* forms */
  newKnowledgeName: "",
  newKnowledgeDescription: "",
  serviceToken: "",

  /* playground */
  question: "如何配置数据库？",
  playgroundOptions: { vector_top_k: 10, bm25_top_k: 10, fusion_top_k: 20, rerank_top_n: 10 },
  playgroundTrace: null as RagTrace | null,

  /* ---------- derived ---------- */
  get totalDocuments(): number {
    return this.knowledgeBases.reduce((total, item) => total + item.document_count, 0);
  },
  get totalChunks(): number {
    return this.knowledgeBases.reduce((total, item) => total + item.active_chunk_count, 0);
  },
  get selectedKnowledge(): KnowledgeBase | null {
    return this.knowledgeBases.find((kb) => kb.id === this.selectedKnowledgeId) ?? null;
  },
  get filteredKnowledgeBases(): KnowledgeBase[] {
    const keyword = this.knowledgeSearch.trim().toLowerCase();
    return this.knowledgeBases.filter((kb) => {
      const matchKeyword =
        !keyword ||
        kb.name.toLowerCase().includes(keyword) ||
        (kb.description ?? "").toLowerCase().includes(keyword);
      const matchStatus = !this.knowledgeStatusFilter || kb.runtime_state === this.knowledgeStatusFilter;
      return matchKeyword && matchStatus;
    });
  },
  get filteredDocuments(): DocumentItem[] {
    const keyword = this.documentSearch.trim().toLowerCase();
    if (!keyword) return this.documents;
    return this.documents.filter(
      (doc) =>
        doc.display_name.toLowerCase().includes(keyword) ||
        doc.filename.toLowerCase().includes(keyword),
    );
  },

  /* ---------- helpers ---------- */
  formatDate(value: string | null | undefined): string {
    return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
  },
  formatJson(value: unknown): string {
    return JSON.stringify(value, null, 2);
  },
  taskMetadata(task: IndexTask | undefined, key: string): string | number | undefined {
    const value = task?.metadata[key];
    return typeof value === "string" || typeof value === "number" ? value : undefined;
  },

  /* ---------- core ---------- */
  async run(action: () => Promise<void>, success = ""): Promise<void> {
    this.loading = true;
    this.error = "";
    this.notice = "";
    try {
      await action();
      if (success) this.notice = success;
    } catch (cause) {
      this.error = cause instanceof Error ? cause.message : "操作失败";
    } finally {
      this.loading = false;
    }
  },

  async loadKnowledgeBases(): Promise<void> {
    this.knowledgeBases = await adminApi.listKnowledgeBases();
    if (!this.selectedKnowledgeId && this.knowledgeBases.length) {
      this.selectedKnowledgeId = this.knowledgeBases[0]?.id ?? "";
    }
  },

  async loadKnowledgeDetails(): Promise<void> {
    if (!this.selectedKnowledgeId) {
      this.documents = [];
      this.indexes = [];
      return;
    }
    [this.documents, this.indexes] = await Promise.all([
      adminApi.listDocuments(this.selectedKnowledgeId),
      adminApi.listIndexes(this.selectedKnowledgeId),
    ]);
    await this.loadIndexTasks();
  },

  async loadIndexTasks(): Promise<void> {
    const entries = await Promise.all(
      this.indexes.map(async (index) => {
        const tasks = await adminApi.listIndexTasks(index.id);
        return [index.id, tasks[0]] as const;
      }),
    );
    this.latestIndexTasks = Object.fromEntries(entries);
  },

  async refreshAll(): Promise<void> {
    await this.loadKnowledgeBases();
    await Promise.all([
      this.loadKnowledgeDetails(),
      adminApi.listTraces().then((value) => (this.traces = value)),
      adminApi.listModels().then((value) => (this.models = value)),
      adminApi.listPrompts().then((value) => (this.prompts = value)),
    ]);
  },

  async selectKnowledge(): Promise<void> {
    this.selectedDocument = null;
    this.selectedTrace = null;
    await this.run(async () => {
      await this.loadKnowledgeDetails();
      this.traces = await adminApi.listTraces(this.selectedKnowledgeId || undefined);
    });
  },

  async createKnowledgeBase(): Promise<void> {
    if (!this.newKnowledgeName.trim()) return;
    await this.run(async () => {
      const created = await adminApi.createKnowledgeBase(
        this.newKnowledgeName.trim(),
        this.newKnowledgeDescription.trim(),
      );
      this.newKnowledgeName = "";
      this.newKnowledgeDescription = "";
      await this.loadKnowledgeBases();
      this.selectedKnowledgeId = created.id;
      await this.loadKnowledgeDetails();
    }, "知识库已创建");
    if (!this.error) this.knowledgeDrawerOpen = false;
  },

  async upload(file: File): Promise<void> {
    if (!this.selectedKnowledgeId) return;
    await this.run(async () => {
      await adminApi.uploadDocument(this.selectedKnowledgeId, file);
      await Promise.all([this.loadKnowledgeBases(), this.loadKnowledgeDetails()]);
    }, "文档已上传，索引构建已提交");
    if (!this.error) this.uploadDrawerOpen = false;
  },

  async deleteDocument(document: DocumentItem): Promise<void> {
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
    await this.run(async () => {
      const result = await adminApi.deleteDocument(document.id);
      if (this.selectedDocument?.id === document.id) {
        this.selectedDocument = null;
        this.documentDrawerOpen = false;
      }
      await Promise.all([this.loadKnowledgeBases(), this.loadKnowledgeDetails()]);
      successMessage = result.build_request.coalesced
        ? "文档已删除，将在当前构建完成后更新索引"
        : "文档已删除，索引更新已提交";
    });
    if (successMessage) this.notice = successMessage;
  },

  async inspectDocument(document: DocumentItem): Promise<void> {
    this.selectedDocument = document;
    this.documentDrawerOpen = true;
    await this.run(async () => {
      const [elementResult, chunkResult] = await Promise.all([
        adminApi.listElements(document.id),
        adminApi.listChunks(document.id),
      ]);
      this.elements = elementResult.items;
      this.chunks = chunkResult.items;
    });
  },

  async buildIndex(): Promise<void> {
    if (!this.selectedKnowledgeId) return;
    await this.run(async () => {
      await adminApi.buildIndex(this.selectedKnowledgeId, this.autoActivate);
      await Promise.all([this.loadKnowledgeBases(), this.loadKnowledgeDetails()]);
    }, "索引构建已提交");
  },

  async activateIndex(indexId: string): Promise<void> {
    await this.run(async () => {
      await adminApi.activateIndex(indexId);
      await Promise.all([this.loadKnowledgeBases(), this.loadKnowledgeDetails()]);
    }, "索引已原子切换");
  },

  async gcIndexes(): Promise<void> {
    if (!this.selectedKnowledgeId) return;
    await this.run(async () => {
      const result = await adminApi.gcIndexes(this.selectedKnowledgeId);
      this.notice = `已回收 ${result.deleted_count} 个旧索引`;
      await this.loadKnowledgeDetails();
    });
  },

  async deleteIndex(indexId: string): Promise<void> {
    try {
      await ElMessageBox.confirm(
        "确定删除此索引吗？该操作不可恢复，将删除关联的 chunk、embedding 和图片数据。",
        "删除索引",
        { type: "warning", confirmButtonText: "删除", cancelButtonText: "取消" },
      );
    } catch {
      return;
    }
    await this.run(async () => {
      await adminApi.deleteIndex(indexId);
      await this.loadKnowledgeDetails();
    }, "索引已删除");
  },

  async askPlayground(): Promise<void> {
    if (!this.selectedKnowledgeId || !this.question.trim()) return;
    await this.run(async () => {
      this.playgroundTrace = await adminApi.runPlayground(
        this.selectedKnowledgeId,
        this.question.trim(),
        this.playgroundOptions,
      );
      this.traces = await adminApi.listTraces(this.selectedKnowledgeId);
    });
  },

  async loadRagConfig(): Promise<void> {
    this.ragConfig = await adminApi.getRagConfig();
  },

  async saveRagConfig(payload: RagConfigUpdatePayload): Promise<void> {
    await this.run(async () => {
      await adminApi.updateRagConfig(payload);
      this.ragConfig = await adminApi.getRagConfig();
    }, "RAG 检索配置已保存，下一轮对话生效");
  },

  async saveModelConfig(modelId: string, payload: ModelConfigUpdatePayload): Promise<void> {
    await this.run(async () => {
      await adminApi.updateModel(modelId, payload);
      this.models = await adminApi.listModels();
    }, "模型配置已保存，下一轮对话生效");
  },

  async openTraceSilent(traceId: string): Promise<RagTrace> {
    this.selectedTrace = await adminApi.getTrace(traceId);
    return this.selectedTrace;
  },

  async openTrace(traceId: string): Promise<void> {
    await this.run(async () => {
      this.selectedTrace = await adminApi.getTrace(traceId);
      this.traceDrawerOpen = true;
    });
  },

  saveToken(value: string): void {
    const token = value.trim();
    if (token) sessionStorage.setItem("rag_service_token", token);
    else sessionStorage.removeItem("rag_service_token");
    this.notice = "访问凭据仅保存在当前浏览器会话";
  },

  clearToken(): void {
    sessionStorage.removeItem("rag_service_token");
    this.serviceToken = "";
    this.notice = "本地访问凭据已清除";
  },
});

export function useAdminStore() {
  return adminStore;
}

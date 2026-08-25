import { reactive } from "vue";
import { ElMessageBox } from "element-plus";

import { adminApi } from "@/api/admin";
import type {
  DocumentItem,
  KnowledgeBase,
  KnowledgeIndex,
  KnowledgeRelease,
  IndexTask,
  ModelConfig,
  ModelConfigUpdatePayload,
  ModelProbeResult,
  PromptTemplate,
  ProblemProfile,
  ProfileDraftPatch,
  ProfileFeedback,
  RagConfig,
  RagConfigUpdatePayload,
  RagTrace,
  SystemInfo,
  TraceSummary,
} from "@/types/admin";

const SELECTED_KNOWLEDGE_STORAGE_KEY = "rag_admin_selected_knowledge_id";

function readSelectedKnowledgeId(): string {
  try {
    return localStorage.getItem(SELECTED_KNOWLEDGE_STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

function writeSelectedKnowledgeId(knowledgeId: string): void {
  try {
    if (knowledgeId) localStorage.setItem(SELECTED_KNOWLEDGE_STORAGE_KEY, knowledgeId);
    else localStorage.removeItem(SELECTED_KNOWLEDGE_STORAGE_KEY);
  } catch {
    // Storage can be unavailable in privacy-restricted browser contexts.
  }
}

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
  problemProfiles: [] as ProblemProfile[],
  knowledgeReleases: [] as KnowledgeRelease[],
  selectedRelease: null as KnowledgeRelease | null,
  profileFeedback: [] as ProfileFeedback[],
  elements: [] as Array<Record<string, unknown>>,
  chunks: [] as Array<Record<string, unknown>>,
  documentInspectionIndexId: "",
  latestIndexTasks: {} as Record<string, IndexTask | undefined>,
  systemInfo: null as SystemInfo | null,

  /* selection / ui state */
  selectedKnowledgeId: "",
  selectedDocument: null as DocumentItem | null,
  documentDrawerOpen: false,
  selectedTrace: null as RagTrace | null,
  traceDrawerOpen: false,
  conversationId: "",
  conversationDrawerOpen: false,
  knowledgeDrawerOpen: false,
  uploadDrawerOpen: false,
  knowledgeSearch: "",
  knowledgeStatusFilter: "",
  documentSearch: "",
  requestedView: "",
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
  get activeIndex(): KnowledgeIndex | null {
    const activeId = this.selectedKnowledge?.active_index_id;
    return this.indexes.find((index) => index.id === activeId) ?? null;
  },
  get documentInspectionIndexes(): Array<{ id: string; label: string }> {
    const result: Array<{ id: string; label: string }> = [];
    if (this.selectedRelease) {
      result.push({
        id: this.selectedRelease.index_id,
        label: `候选 Index v${this.selectedRelease.index_version}`,
      });
    }
    if (this.activeIndex && !result.some((item) => item.id === this.activeIndex!.id)) {
      result.push({ id: this.activeIndex.id, label: `线上 Active Index v${this.activeIndex.version}` });
    }
    return result;
  },
  get pendingReviewCount(): number {
    return this.selectedRelease?.drafts?.filter((draft) => draft.status !== "APPROVED").length ?? 0;
  },
  get buildingCount(): number {
    return this.indexes.filter((index) => index.status === "BUILDING").length;
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
  releaseForIndex(indexId: string): KnowledgeRelease | undefined {
    return this.knowledgeReleases.find((release) => release.index_id === indexId);
  },
  isActionableReleaseIndex(indexId: string): boolean {
    const release = this.releaseForIndex(indexId);
    return Boolean(release && !["PUBLISHED", "CANCELLED"].includes(release.status));
  },
  navigate(view: string): void {
    this.requestedView = view;
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
    const currentStillExists = this.knowledgeBases.some((kb) => kb.id === this.selectedKnowledgeId);
    const savedKnowledgeId = readSelectedKnowledgeId();
    const savedStillExists = this.knowledgeBases.some((kb) => kb.id === savedKnowledgeId);
    if (!currentStillExists) {
      this.selectedKnowledgeId = savedStillExists
        ? savedKnowledgeId
        : (this.knowledgeBases[0]?.id ?? "");
    }
    writeSelectedKnowledgeId(this.selectedKnowledgeId);
  },

  async loadSystemInfo(): Promise<void> {
    try {
      this.systemInfo = await adminApi.getSystemInfo();
    } catch {
      this.systemInfo = null;
    }
  },

  async loadKnowledgeDetails(): Promise<void> {
    if (!this.selectedKnowledgeId) {
      this.documents = [];
      this.indexes = [];
      this.knowledgeReleases = [];
      this.selectedRelease = null;
      this.profileFeedback = [];
      return;
    }
    const knowledgeId = this.selectedKnowledgeId;
    const [documents, indexes] = await Promise.all([
      adminApi.listDocuments(knowledgeId),
      adminApi.listIndexes(knowledgeId),
    ]);
    if (this.selectedKnowledgeId !== knowledgeId) return;
    this.documents = documents;
    this.indexes = indexes;
    await this.loadIndexTasks(indexes, knowledgeId);
  },

  async loadIndexTasks(
    indexes?: KnowledgeIndex[],
    knowledgeId?: string,
  ): Promise<void> {
    const targetIndexes = indexes ?? this.indexes;
    const targetKnowledgeId = knowledgeId ?? this.selectedKnowledgeId;
    const results = await Promise.allSettled(
      targetIndexes.map(async (index) => {
        const tasks = await adminApi.listIndexTasks(index.id);
        return [index.id, tasks[0]] as const;
      }),
    );
    if (this.selectedKnowledgeId !== targetKnowledgeId) return;
    const entries = results.flatMap((result) =>
      result.status === "fulfilled" ? [result.value] : [],
    );
    this.latestIndexTasks = Object.fromEntries(entries);
  },

  async refreshAll(): Promise<void> {
    await this.loadKnowledgeBases();
    await this.loadKnowledgeDetails();
    await Promise.allSettled([
      adminApi.listTraces().then((value) => (this.traces = value)),
      adminApi.listModels().then((value) => (this.models = value)),
      adminApi.listPrompts().then((value) => (this.prompts = value)),
      this.loadProblemProfiles(),
      this.loadReleaseWorkspace(),
      this.loadSystemInfo(),
    ]);
  },

  async selectKnowledge(): Promise<void> {
    const knowledgeId = this.selectedKnowledgeId;
    writeSelectedKnowledgeId(knowledgeId);
    this.selectedDocument = null;
    this.selectedTrace = null;
    await this.run(async () => {
      const [, traces, profiles] = await Promise.all([
        this.loadKnowledgeDetails(),
        adminApi.listTraces(knowledgeId || undefined),
        knowledgeId ? adminApi.listProblemProfiles(knowledgeId) : Promise.resolve([]),
        this.loadReleaseWorkspace(),
      ]);
      if (this.selectedKnowledgeId !== knowledgeId) return;
      this.traces = traces;
      this.problemProfiles = profiles;
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
      writeSelectedKnowledgeId(created.id);
      await this.loadKnowledgeDetails();
    }, "知识库已创建");
    if (!this.error) this.knowledgeDrawerOpen = false;
  },

  async deleteKnowledgeBase(knowledge: KnowledgeBase): Promise<void> {
    try {
      await ElMessageBox.prompt(
        `删除后将永久移除“${knowledge.name}”中的文档、索引、Profile、会话与 Trace。请输入知识库名称确认。`,
        "删除知识库",
        {
          type: "error",
          confirmButtonText: "永久删除",
          cancelButtonText: "取消",
          inputPlaceholder: knowledge.name,
          inputValidator: (value) => value === knowledge.name || "输入的知识库名称不一致",
        },
      );
    } catch {
      return;
    }
    let cleanupFailedCount = 0;
    await this.run(async () => {
      const result = await adminApi.deleteKnowledgeBase(knowledge.id);
      cleanupFailedCount = result.cleanup_failed_count;
      if (this.selectedKnowledgeId === knowledge.id) {
        this.selectedKnowledgeId = "";
        writeSelectedKnowledgeId("");
        this.selectedDocument = null;
        this.documentDrawerOpen = false;
      }
      await this.loadKnowledgeBases();
      await this.loadKnowledgeDetails();
    });
    if (!this.error) {
      this.notice = cleanupFailedCount
        ? `知识库已删除；${cleanupFailedCount} 个存储对象回收失败，请检查后端日志`
        : "知识库已删除";
    }
  },

  async upload(file: File, idempotencyKey: string = crypto.randomUUID()): Promise<void> {
    if (!this.selectedKnowledgeId) return;
    const knowledgeId = this.selectedKnowledgeId;
    await this.run(async () => {
      await adminApi.uploadDocument(knowledgeId, file, idempotencyKey);
      await Promise.all([
        this.loadKnowledgeBases(),
        this.loadKnowledgeDetails(),
      ]);
      await this.loadReleaseWorkspace().catch(() => undefined);
    }, "文档已上传，候选索引构建已提交；完成后请审核 Profile 草稿");
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
    this.documentInspectionIndexId =
      this.selectedRelease?.index_id ?? this.activeIndex?.id ?? "";
    await this.loadDocumentInspection();
  },

  async loadDocumentInspection(): Promise<void> {
    const document = this.selectedDocument;
    const indexId = this.documentInspectionIndexId;
    if (!document || !indexId) {
      this.elements = [];
      this.chunks = [];
      return;
    }
    await this.run(async () => {
      const [elementResult, chunkResult] = await Promise.all([
        adminApi.listElements(document.id, indexId),
        adminApi.listChunks(document.id, indexId),
      ]);
      if (this.selectedDocument?.id !== document.id || this.documentInspectionIndexId !== indexId) {
        return;
      }
      this.documentInspectionIndexId = elementResult.index_id;
      this.elements = elementResult.items;
      this.chunks = chunkResult.items;
    });
  },

  async buildIndex(): Promise<void> {
    if (!this.selectedKnowledgeId) return;
    await this.run(async () => {
      await adminApi.buildIndex(this.selectedKnowledgeId);
      await Promise.all([this.loadKnowledgeBases(), this.loadKnowledgeDetails()]);
      await this.loadReleaseWorkspace().catch(() => undefined);
    }, "候选索引构建已提交，完成后需要审核发布");
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

  async loadProblemProfiles(): Promise<void> {
    this.problemProfiles = this.selectedKnowledgeId
      ? await adminApi.listProblemProfiles(this.selectedKnowledgeId)
      : [];
  },

  async loadReleaseWorkspace(): Promise<void> {
    if (!this.selectedKnowledgeId) {
      this.knowledgeReleases = [];
      this.selectedRelease = null;
      this.profileFeedback = [];
      return;
    }
    const knowledgeId = this.selectedKnowledgeId;
    const [releases, feedback] = await Promise.all([
      adminApi.listKnowledgeReleases(knowledgeId),
      adminApi.listProfileFeedback(knowledgeId),
    ]);
    if (this.selectedKnowledgeId !== knowledgeId) return;
    this.knowledgeReleases = releases;
    this.profileFeedback = feedback;
    const candidate = releases.find(
      (release) => !["PUBLISHED", "CANCELLED"].includes(release.status),
    );
    const selected = candidate ? await adminApi.getKnowledgeRelease(candidate.id) : null;
    if (this.selectedKnowledgeId !== knowledgeId) return;
    this.selectedRelease = selected;
  },

  async refreshOperationalState(): Promise<void> {
    if (!this.selectedKnowledgeId) return;
    await Promise.allSettled([
      this.loadKnowledgeBases(),
      this.loadKnowledgeDetails(),
      this.loadReleaseWorkspace(),
    ]);
  },

  async refreshSelectedRelease(): Promise<void> {
    if (!this.selectedRelease) {
      await this.loadReleaseWorkspace();
      return;
    }
    this.selectedRelease = await adminApi.getKnowledgeRelease(this.selectedRelease.id);
  },

  async generateProfileDrafts(): Promise<void> {
    const release = this.selectedRelease;
    if (!release) return;
    await this.run(async () => {
      await adminApi.generateProfileDrafts(release.index_id);
      await this.refreshSelectedRelease();
    }, "Profile 草稿已重新生成，请审核后发布");
  },

  async saveProfileDraft(draftId: string, payload: ProfileDraftPatch): Promise<void> {
    await this.run(async () => {
      await adminApi.updateProfileDraft(draftId, payload);
      await this.refreshSelectedRelease();
    }, "Profile 草稿已保存，尚未影响线上问答");
  },

  async regenerateProfileDraft(draftId: string): Promise<void> {
    await this.run(async () => {
      await adminApi.regenerateProfileDraft(draftId);
      await this.refreshSelectedRelease();
    }, "Profile 草稿已重新生成");
  },

  async rejectProfileDraft(draftId: string): Promise<void> {
    await this.run(async () => {
      await adminApi.updateProfileDraft(draftId, { status: "REJECTED" });
      await this.refreshSelectedRelease();
    }, "草稿已退回；修改、重新生成或改为仅参考后可再次审核");
  },

  async validateKnowledgeRelease(): Promise<void> {
    if (!this.selectedRelease) return;
    await this.run(async () => {
      await adminApi.validateKnowledgeRelease(this.selectedRelease!.id);
      await this.refreshSelectedRelease();
    }, "发布前验证已完成");
  },

  async publishKnowledgeRelease(): Promise<void> {
    if (!this.selectedRelease) return;
    await this.run(async () => {
      await adminApi.publishKnowledgeRelease(this.selectedRelease!.id);
      await Promise.all([
        this.loadKnowledgeBases(),
        this.loadKnowledgeDetails(),
        this.loadProblemProfiles(),
      ]);
      await this.loadReleaseWorkspace();
    }, "候选索引与 Profile 已原子发布");
  },

  async saveModelConfig(modelId: string, payload: ModelConfigUpdatePayload): Promise<void> {
    await this.run(async () => {
      await adminApi.updateModel(modelId, payload);
      this.models = await adminApi.listModels();
    }, "模型配置已保存，下一轮对话生效");
  },

  async putModelApiKey(modelId: string, apiKey: string): Promise<void> {
    await this.run(async () => {
      await adminApi.putApiKey(modelId, apiKey);
      this.models = await adminApi.listModels();
    }, "API 密钥已加密保存（数据库密钥优先于环境变量）");
  },

  async clearModelApiKey(modelId: string): Promise<void> {
    await this.run(async () => {
      await adminApi.deleteApiKey(modelId);
      this.models = await adminApi.listModels();
    }, "数据库密钥已清除，回退到环境变量密钥");
  },

  async probeModel(modelId: string): Promise<ModelProbeResult> {
    return await adminApi.probeModel(modelId);
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

  /* Conversation replay drawer (shared by Traces and Logs views). */
  openConversation(conversationId: string): void {
    this.conversationId = conversationId;
    this.conversationDrawerOpen = true;
  },

  saveToken(value: string): void {
    const token = value.trim();
    this.serviceToken = token;
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

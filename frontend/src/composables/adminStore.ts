import { reactive } from "vue";
import { ElMessageBox, ElNotification } from "element-plus";

import { adminApi } from "@/api/admin";
import type {
  DocumentItem,
  DocumentProfileContext,
  KnowledgeBase,
  KnowledgeIndex,
  IndexTask,
  KnowledgeRelease,
  ModelConfig,
  ModelConfigUpdatePayload,
  ModelProbeResult,
  ProfileDraftPatch,
  PromptTemplate,
  RagConfig,
  RagConfigUpdatePayload,
  RagTrace,
  SystemInfo,
  TaskRecord,
  TaskSubmission,
  TraceSummary,
} from "@/types/admin";

const SELECTED_KNOWLEDGE_STORAGE_KEY = "rag_admin_selected_knowledge_id";
const TRACKED_TASKS_STORAGE_KEY = "rag_admin_tracked_tasks";

type TrackedTask = {
  taskId: string;
  taskType: string;
  successMessage: string;
  knowledgeId?: string;
};

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

function readTrackedTasks(): TrackedTask[] {
  try {
    const raw = sessionStorage.getItem(TRACKED_TASKS_STORAGE_KEY);
    const value: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(value)) return [];
    return value.filter(
      (item): item is TrackedTask =>
        typeof item === "object" &&
        item !== null &&
        typeof (item as Record<string, unknown>).taskId === "string" &&
        typeof (item as Record<string, unknown>).taskType === "string" &&
        typeof (item as Record<string, unknown>).successMessage === "string",
    );
  } catch {
    return [];
  }
}

function writeTrackedTasks(tasks: TrackedTask[]): void {
  try {
    sessionStorage.setItem(TRACKED_TASKS_STORAGE_KEY, JSON.stringify(tasks));
  } catch {
    // Task completion notices remain available for this page lifetime.
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
  elements: [] as Array<Record<string, unknown>>,
  chunks: [] as Array<Record<string, unknown>>,
  documentProfileContext: null as DocumentProfileContext | null,
  documentInspectionIndexId: "",
  latestIndexTasks: {} as Record<string, IndexTask | undefined>,
  latestProfileTasks: {} as Record<string, IndexTask | undefined>,
  releases: [] as KnowledgeRelease[],
  releaseDetails: {} as Record<string, KnowledgeRelease | undefined>,
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
  requestedViewQuery: "",
  requestedIndexDetailId: "",
  loading: false,
  activeRequestCount: 0,
  actionLoading: {} as Record<string, boolean>,
  trackedTasks: [] as TrackedTask[],
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
  releaseForIndex(indexId: string): KnowledgeRelease | undefined {
    return this.releases.find((release) => release.index_id === indexId);
  },
  isActionableReleaseIndex(indexId: string): boolean {
    const release = this.releaseForIndex(indexId);
    return Boolean(release && !["PUBLISHED", "CANCELLED"].includes(release.status));
  },
  get documentInspectionIndexes(): Array<{ id: string; label: string }> {
    const result: Array<{ id: string; label: string }> = [];
    if (this.activeIndex) {
      result.push({ id: this.activeIndex.id, label: `线上 Active Index v${this.activeIndex.version}` });
    }
    return result;
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
  navigate(view: string, query = ""): void {
    this.requestedViewQuery = query;
    this.requestedView = view;
  },
  isActionLoading(key: string): boolean {
    return Boolean(this.actionLoading[key]);
  },

  /* ---------- core ---------- */
  async run(action: () => Promise<void>, success = ""): Promise<void> {
    this.activeRequestCount += 1;
    this.loading = true;
    this.error = "";
    this.notice = "";
    try {
      await action();
      if (success) this.notice = success;
    } catch (cause) {
      this.error = cause instanceof Error ? cause.message : "操作失败";
    } finally {
      this.activeRequestCount = Math.max(0, this.activeRequestCount - 1);
      this.loading = this.activeRequestCount > 0;
    }
  },

  async runAction(actionKey: string, action: () => Promise<void>): Promise<void> {
    this.actionLoading[actionKey] = true;
    this.error = "";
    try {
      await action();
    } catch (cause) {
      this.error = cause instanceof Error ? cause.message : "操作失败";
    } finally {
      delete this.actionLoading[actionKey];
    }
  },

  restoreTrackedTasks(): void {
    if (!this.trackedTasks.length) this.trackedTasks = readTrackedTasks();
  },

  trackTask(submission: TaskSubmission, task: Omit<TrackedTask, "taskId" | "taskType">): void {
    if (!this.trackedTasks.some((item) => item.taskId === submission.task_id)) {
      this.trackedTasks.push({
        taskId: submission.task_id,
        taskType: submission.task_type,
        ...task,
      });
      writeTrackedTasks(this.trackedTasks);
    }
  },

  async pollTrackedTasks(): Promise<void> {
    const tracked = [...this.trackedTasks];
    if (!tracked.length) return;
    const results = await Promise.allSettled(
      tracked.map(async (item) => [item, await adminApi.getTask(item.taskId)] as const),
    );
    for (const result of results) {
      if (result.status !== "fulfilled") continue;
      const [trackedTask, task] = result.value;
      if (task.status !== "SUCCEEDED" && task.status !== "FAILED") continue;
      this.trackedTasks = this.trackedTasks.filter((item) => item.taskId !== trackedTask.taskId);
      writeTrackedTasks(this.trackedTasks);
      const succeeded = task.status === "SUCCEEDED";
      const message = succeeded
        ? trackedTask.successMessage
        : task.error_message || "后台任务执行失败，请在任务中心查看详情";
      this.notice = message;
      ElNotification({
        title: succeeded ? "任务已完成" : "任务执行失败",
        message,
        type: succeeded ? "success" : "error",
        duration: succeeded ? 5_000 : 0,
      });
      if (succeeded) {
        this.trackChainedProfileTask(task, trackedTask);
      }
      if (trackedTask.taskType === "KNOWLEDGE_BASE_DELETE" && succeeded) {
        if (this.selectedKnowledgeId === trackedTask.knowledgeId) {
          this.selectedKnowledgeId = "";
          writeSelectedKnowledgeId("");
          this.selectedDocument = null;
          this.documentDrawerOpen = false;
        }
        await this.loadKnowledgeBases();
      } else if (succeeded && trackedTask.knowledgeId === this.selectedKnowledgeId) {
        await this.refreshOperationalState();
      }
    }
  },

  trackChainedProfileTask(task: TaskRecord, trackedTask: TrackedTask): void {
    /* An orchestrated Profile rebuild returns an INDEX_BUILD task whose
     * completion only means the refresh was *submitted*; the worker chains
     * the PROFILE_REFRESH task id in metadata so tracking follows through. */
    const chainedId = task.metadata?.profile_task_id;
    if (typeof chainedId !== "string" || !chainedId) return;
    if (this.trackedTasks.some((item) => item.taskId === chainedId)) return;
    this.trackedTasks.push({
      taskId: chainedId,
      taskType: "PROFILE_REFRESH",
      successMessage: "Profile 重建已完成，请前往 Index Build 审核候选草稿",
      knowledgeId: trackedTask.knowledgeId,
    });
    writeTrackedTasks(this.trackedTasks);
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
      this.releases = [];
      this.releaseDetails = {};
      this.latestIndexTasks = {};
      this.latestProfileTasks = {};
      return;
    }
    const knowledgeId = this.selectedKnowledgeId;
    const [documents, indexes, releases] = await Promise.all([
      adminApi.listDocuments(knowledgeId),
      adminApi.listIndexes(knowledgeId),
      adminApi.listKnowledgeReleases(knowledgeId).catch(() => []),
    ]);
    if (this.selectedKnowledgeId !== knowledgeId) return;
    this.documents = documents;
    this.indexes = indexes;
    this.releases = releases;
    this.releaseDetails = Object.fromEntries(
      Object.entries(this.releaseDetails).filter(([releaseId]) =>
        releases.some((release) => release.id === releaseId),
      ),
    );
    await this.loadIndexTasks(indexes, knowledgeId);
  },

  async loadReleaseForIndex(indexId: string): Promise<KnowledgeRelease | null> {
    const release = this.releaseForIndex(indexId);
    if (!release) return null;
    const detail = await adminApi.getKnowledgeRelease(release.id);
    this.releaseDetails[detail.id] = detail;
    this.releases = this.releases.map((item) => (item.id === detail.id ? { ...item, ...detail } : item));
    return detail;
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
        return [
          index.id,
          tasks.find((task) => (task.task_type ?? "INDEX_BUILD") === "INDEX_BUILD"),
          tasks.find((task) => task.task_type?.startsWith("PROFILE_")),
        ] as const;
      }),
    );
    if (this.selectedKnowledgeId !== targetKnowledgeId) return;
    const entries = results.flatMap((result) => (result.status === "fulfilled" ? [result.value] : []));
    this.latestIndexTasks = Object.fromEntries(entries.map(([indexId, task]) => [indexId, task]));
    this.latestProfileTasks = Object.fromEntries(
      entries.map(([indexId, , profileTask]) => [indexId, profileTask]),
    );
  },

  async refreshAll(): Promise<void> {
    await this.loadKnowledgeBases();
    await this.loadKnowledgeDetails();
    await Promise.allSettled([
      adminApi.listTraces().then((value) => (this.traces = value)),
      adminApi.listModels().then((value) => (this.models = value)),
      adminApi.listPrompts().then((value) => (this.prompts = value)),
      this.loadSystemInfo(),
    ]);
  },

  async selectKnowledge(): Promise<void> {
    const knowledgeId = this.selectedKnowledgeId;
    writeSelectedKnowledgeId(knowledgeId);
    this.selectedDocument = null;
    this.documentProfileContext = null;
    this.selectedTrace = null;
    await this.run(async () => {
      const [, traces] = await Promise.all([
        this.loadKnowledgeDetails(),
        adminApi.listTraces(knowledgeId || undefined),
      ]);
      if (this.selectedKnowledgeId !== knowledgeId) return;
      this.traces = traces;
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
    await this.runAction(`delete-knowledge:${knowledge.id}`, async () => {
      const result = await adminApi.deleteKnowledgeBase(knowledge.id);
      this.trackTask(result, {
        successMessage: `知识库“${knowledge.name}”已永久删除`,
        knowledgeId: knowledge.id,
      });
      await this.loadKnowledgeBases();
    });
    if (!this.error) this.notice = "知识库删除任务已提交；完成后将弹出提醒";
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
    }, "文档已上传，RAG 索引构建已提交；完成后请在 Index Build 审核发布");
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
    this.documentProfileContext = null;
    this.documentInspectionIndexId = this.activeIndex?.id ?? "";
    await this.loadDocumentInspection();
  },

  async loadDocumentInspection(): Promise<void> {
    const document = this.selectedDocument;
    const indexId = this.documentInspectionIndexId;
    if (!document) {
      this.elements = [];
      this.chunks = [];
      this.documentProfileContext = null;
      return;
    }
    if (!indexId) {
      this.elements = [];
      this.chunks = [];
    }
    await this.run(async () => {
      // Profile context is index-independent; elements/chunks follow the
      // selected inspection index. Stale responses for either are dropped
      // when the drawer moved on to another document meanwhile.
      const [elementResult, chunkResult, profileContext] = await Promise.all([
        indexId
          ? adminApi.listElements(document.id, indexId)
          : Promise.resolve({ index_id: "", items: [] as Array<Record<string, unknown>> }),
        indexId
          ? adminApi.listChunks(document.id, indexId)
          : Promise.resolve({ index_id: "", items: [] as Array<Record<string, unknown>> }),
        adminApi.getDocumentProfileContext(document.id),
      ]);
      if (this.selectedDocument?.id !== document.id) {
        return;
      }
      if (indexId) {
        if (this.documentInspectionIndexId !== indexId) {
          return;
        }
        this.documentInspectionIndexId = elementResult.index_id;
        this.elements = elementResult.items;
        this.chunks = chunkResult.items;
      }
      this.documentProfileContext = profileContext;
    });
  },

  goToCandidateReview(indexId: string): void {
    /* Documents only shows Profile state; edits happen in the candidate
     * review drawer. Navigate there and preselect the candidate's index. */
    this.requestedIndexDetailId = indexId;
    this.documentDrawerOpen = false;
    this.navigate("indexes");
  },

  async rebuildKnowledgeProfiles(knowledge: KnowledgeBase): Promise<void> {
    try {
      await ElMessageBox.confirm(
        `将提交“${knowledge.name}”当前候选的 Profile 重建任务。该操作不会直接修改线上 Profile：` +
          "有可编辑候选时会覆盖其中未发布的自动草稿；没有候选时会先创建候选构建，" +
          "Profile 刷新在构建完成后自动执行。完成后请在 Index Build 审核发布。",
        "重建 Profile",
        { type: "warning", confirmButtonText: "提交重建任务", cancelButtonText: "取消" },
      );
    } catch {
      return;
    }
    await this.runAction(`rebuild-profile:${knowledge.id}`, async () => {
      const submission = await adminApi.rebuildKnowledgeProfiles(knowledge.id);
      this.trackTask(submission, {
        successMessage:
          submission.task_type === "INDEX_BUILD"
            ? "候选索引构建完成，Profile 重建任务已自动提交"
            : "Profile 重建已完成，请前往 Index Build 审核候选草稿",
        knowledgeId: knowledge.id,
      });
    });
    if (!this.error) this.notice = "Profile 重建任务已提交；完成后将弹出提醒";
  },

  async buildIndex(): Promise<void> {
    if (!this.selectedKnowledgeId) return;
    await this.run(async () => {
      const build = await adminApi.buildIndex(this.selectedKnowledgeId);
      if (build.task_id) {
        this.trackTask(
          { task_id: build.task_id, task_type: "INDEX_BUILD", status: "QUEUED", replayed: build.coalesced },
          { successMessage: "RAG 索引构建已完成，请审核发布", knowledgeId: this.selectedKnowledgeId },
        );
      }
      await Promise.all([this.loadKnowledgeBases(), this.loadKnowledgeDetails()]);
    }, "RAG 索引构建已提交，完成后请在 Index Build 审核发布");
  },

  async refreshProfileDrafts(indexId: string): Promise<void> {
    await this.runAction(`refresh-profile:${indexId}`, async () => {
      const submission = await adminApi.refreshProfileDrafts(indexId);
      this.trackTask(submission, {
        successMessage: "Profile 重建已完成，请刷新候选草稿进行审核",
        knowledgeId: this.selectedKnowledgeId,
      });
    });
    if (!this.error) this.notice = "Profile 重建任务已提交；完成后将弹出提醒";
  },

  async updateProfileDraft(draftId: string, payload: ProfileDraftPatch): Promise<void> {
    await this.runAction(`profile-draft:${draftId}`, async () => {
      const draft = await adminApi.updateProfileDraft(draftId, payload);
      const release = await adminApi.getKnowledgeRelease(draft.release_id);
      this.releaseDetails[release.id] = release;
      this.releases = this.releases.map((item) =>
        item.id === release.id ? { ...item, ...release } : item,
      );
    });
  },

  async validateKnowledgeRelease(releaseId: string): Promise<void> {
    await this.runAction(`validate-release:${releaseId}`, async () => {
      const validation = await adminApi.validateKnowledgeRelease(releaseId);
      const release = await adminApi.getKnowledgeRelease(releaseId);
      this.releaseDetails[releaseId] = { ...release, validation_result: validation };
      this.releases = this.releases.map((item) =>
        item.id === releaseId ? { ...item, ...release, validation_result: validation } : item,
      );
    });
  },

  async publishKnowledgeRelease(releaseId: string): Promise<void> {
    await this.runAction(`publish-release:${releaseId}`, async () => {
      const published = await adminApi.publishKnowledgeRelease(releaseId);
      this.releaseDetails[releaseId] = published;
      await Promise.all([this.loadKnowledgeBases(), this.loadKnowledgeDetails()]);
    });
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

  async refreshOperationalState(): Promise<void> {
    if (!this.selectedKnowledgeId) return;
    await Promise.allSettled([
      this.loadKnowledgeBases(),
      this.loadKnowledgeDetails(),
    ]);
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

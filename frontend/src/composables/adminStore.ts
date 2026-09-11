import { reactive } from "vue";
import { ElMessageBox, ElNotification } from "element-plus";

import { adminApi, ApiRequestError } from "@/api/admin";
import type {
  DocumentItem,
  DocumentProfileContext,
  KnowledgeProfileSummary,
  ProfilePublishCheck,
  DocumentProfileIntent,
  KnowledgeBase,
  KnowledgeIndex,
  IndexTask,
  KnowledgeRelease,
  ModelConfig,
  ModelConfigUpdatePayload,
  ModelProbeResult,
  ProfileDraftPatch,
  ProfileFeedback,
  PromptTemplate,
  RagConfig,
  RagConfigUpdatePayload,
  RagTrace,
  ReleaseRebaseResult,
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
  releaseId?: string;
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

function readServiceToken(): string {
  try {
    return sessionStorage.getItem("rag_service_token")?.trim() ?? "";
  } catch {
    return "";
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
  knowledgeProfileSummary: null as KnowledgeProfileSummary | null,
  documentInspectionIndexId: "",
  latestIndexTasks: {} as Record<string, IndexTask | undefined>,
  latestProfileTasks: {} as Record<string, IndexTask | undefined>,
  latestDocumentProfileTasks: {} as Record<string, IndexTask | undefined>,
  releases: [] as KnowledgeRelease[],
  releaseDetails: {} as Record<string, KnowledgeRelease | undefined>,
  profileFeedback: [] as ProfileFeedback[],
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
  credentialRejected: false,

  /* forms */
  newKnowledgeName: "",
  newKnowledgeDescription: "",
  serviceToken: readServiceToken(),

  /* playground */
  question: "如何配置数据库？",
  playgroundOptions: { vector_top_k: 10, bm25_top_k: 10, fusion_top_k: 20, rerank_top_n: 10 },
  playgroundTrace: null as RagTrace | null,

  /* ---------- derived ---------- */
  get totalDocuments(): number {
    return this.knowledgeBases.reduce((total, item) => total + item.document_count, 0);
  },
  get hasServiceCredential(): boolean {
    return Boolean(this.serviceToken.trim()) && !this.credentialRejected;
  },
  get totalChunks(): number {
    return this.knowledgeBases.reduce((total, item) => total + item.active_chunk_count, 0);
  },
  get selectedKnowledge(): KnowledgeBase | null {
    return this.knowledgeBases.find((kb) => kb.id === this.selectedKnowledgeId) ?? null;
  },
  get activeIndex(): KnowledgeIndex | null {
    const activeId =
      this.selectedKnowledge?.rag_active_index_id ?? this.selectedKnowledge?.active_index_id;
    return this.indexes.find((index) => index.id === activeId) ?? null;
  },
  get supportActiveIndex(): KnowledgeIndex | null {
    const activeId = this.selectedKnowledge?.support_active_index_id;
    return this.indexes.find((index) => index.id === activeId) ?? null;
  },
  get supportActiveRelease(): KnowledgeRelease | null {
    const releaseId = this.selectedKnowledge?.support_active_release_id;
    return this.releases.find((release) => release.id === releaseId) ?? null;
  },
  get pendingSupportReviewCount(): number {
    return this.releases
      .filter((release) => !["PUBLISHED", "CANCELLED"].includes(release.status))
      .reduce(
        (total, release) => total + Math.max(0, release.draft_count - release.approved_count),
        0,
      );
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
    return JSON.stringify(value, null, 2) ?? "(empty)";
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
    if (!this.hasServiceCredential) return;
    const tracked = [...this.trackedTasks];
    if (!tracked.length) return;
    const results = await Promise.allSettled(
      tracked.map(async (item) => [item, await adminApi.getTask(item.taskId)] as const),
    );
    for (const result of results) {
      if (result.status !== "fulfilled") continue;
      const [trackedTask, task] = result.value;
      this.syncPolledTask(task);
      if (!["SUCCEEDED", "FAILED", "CANCELLED"].includes(task.status)) continue;
      this.trackedTasks = this.trackedTasks.filter((item) => item.taskId !== trackedTask.taskId);
      writeTrackedTasks(this.trackedTasks);
      const succeeded = task.status === "SUCCEEDED";
      const cancelled = task.status === "CANCELLED";
      const message = succeeded
        ? trackedTask.successMessage
        : cancelled
          ? "生成任务已停止；已完成文档的待上线属性保留，可继续编辑或重新生成"
          : task.error_message || "后台任务执行失败，请在任务中心查看详情";
      this.notice = message;
      ElNotification({
        title: cancelled ? "任务已停止" : succeeded ? "任务已完成" : "任务执行失败",
        message,
        type: cancelled ? "warning" : succeeded ? "success" : "error",
        duration: succeeded || cancelled ? 5_000 : 0,
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
      } else if ((succeeded || cancelled) && trackedTask.knowledgeId === this.selectedKnowledgeId) {
        await this.refreshOperationalState();
        await this.loadKnowledgeProfiles();
        const releaseId = this.taskReleaseId(task, trackedTask);
        if (releaseId) await this.refreshReleaseDetail(releaseId);
      }
    }
  },

  registerProfileTaskSubmission(submission: TaskSubmission, indexId: string): void {
    /* Render a QUEUED task immediately.  The index task list is eventually
     * consistent and may lag just behind a 202 response, so it must not be
     * the only source of UI feedback for a user-initiated Profile action. */
    this.latestProfileTasks[indexId] = {
      id: submission.task_id,
      task_type: submission.task_type,
      status: submission.status,
      stage: "QUEUED",
      progress: 0,
      attempt: 0,
      index_id: indexId,
      error_code: null,
      error_message: null,
      metadata: {},
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
  },

  async hydrateTrackedTask(taskId: string): Promise<void> {
    try {
      this.syncPolledTask(await adminApi.getTask(taskId));
    } catch {
      // The optimistic QUEUED state remains visible and the regular tracker
      // will retry. Do not turn a successful 202 submission into a UI error.
    }
  },

  syncPolledTask(task: TaskRecord): void {
    const indexId = task.index_id;
    const indexTask: IndexTask = { ...task, index_id: indexId ?? "" };
    if (task.task_type === "DOCUMENT_PROFILE_GENERATE" && task.kb_id) {
      this.latestDocumentProfileTasks[task.kb_id] = indexTask;
    } else if (!indexId) {
      return;
    } else if (task.task_type.startsWith("PROFILE_")) {
      this.latestProfileTasks[indexId] = indexTask;
    } else if (task.task_type === "INDEX_BUILD") {
      this.latestIndexTasks[indexId] = indexTask;
    }
  },

  taskReleaseId(task: TaskRecord, trackedTask: TrackedTask): string | undefined {
    const metadataReleaseId = task.metadata?.release_id;
    return typeof metadataReleaseId === "string" && metadataReleaseId
      ? metadataReleaseId
      : trackedTask.releaseId;
  },

  trackChainedProfileTask(task: TaskRecord, trackedTask: TrackedTask): void {
    /* An INDEX_BUILD can enqueue either a legacy index-bound Profile refresh
     * or the independent document-attribute generator.  Its completion only
     * means that follow-up was submitted, so continue tracking it here. */
    const chainedId = task.metadata?.profile_task_id;
    if (typeof chainedId !== "string" || !chainedId) return;
    if (this.trackedTasks.some((item) => item.taskId === chainedId)) return;
    const chainedType = task.metadata?.profile_task_type;
    const taskType = typeof chainedType === "string" && chainedType
      ? chainedType
      : "PROFILE_REFRESH";
    this.trackedTasks.push({
      taskId: chainedId,
      taskType,
      successMessage: taskType === "DOCUMENT_PROFILE_GENERATE"
        ? "Profile 属性已生成，请前往 Profile Review 检查并上线"
        : "Profile 重建已完成，请前往 Profile Review 审核候选草稿",
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
      this.profileFeedback = [];
      this.latestIndexTasks = {};
      this.latestProfileTasks = {};
      this.latestDocumentProfileTasks = {};
      this.knowledgeProfileSummary = null;
      return;
    }
    const knowledgeId = this.selectedKnowledgeId;
    const [documents, indexes, profileSummary] = await Promise.all([
      adminApi.listDocuments(knowledgeId),
      adminApi.listIndexes(knowledgeId),
      adminApi.getKnowledgeProfiles(knowledgeId).catch(() => null),
    ]);
    if (this.selectedKnowledgeId !== knowledgeId) return;
    this.documents = documents;
    this.indexes = indexes;
    this.releases = [];
    this.profileFeedback = [];
    this.knowledgeProfileSummary = profileSummary;
    this.releaseDetails = {};
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

  async refreshReleaseDetail(releaseId: string): Promise<void> {
    const detail = await adminApi.getKnowledgeRelease(releaseId);
    this.releaseDetails[detail.id] = detail;
    this.releases = this.releases.map((item) =>
      item.id === detail.id ? { ...item, ...detail } : item,
    );
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

  async loadKnowledgeProfiles(): Promise<void> {
    if (!this.selectedKnowledgeId) {
      this.knowledgeProfileSummary = null;
      return;
    }
    const knowledgeId = this.selectedKnowledgeId;
    try {
      const summary = await adminApi.getKnowledgeProfiles(knowledgeId);
      if (this.selectedKnowledgeId === knowledgeId) this.knowledgeProfileSummary = summary;
    } catch {
      // A rolling API upgrade can expose the new UI before the attribute
      // endpoint is available; task polling must remain usable meanwhile.
    }
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
    }, "知识库已创建，默认不对 Assistant 上线");
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
    }, "文档已上传，RAG 候选索引构建已提交；构建完成后需明确设为当前索引，Profile 请在 Profile Review 审核");
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
    /* Documents only shows Profile state; edits happen in the dedicated
     * Support review workspace. Navigate there and preselect the release. */
    this.requestedIndexDetailId = indexId;
    this.documentDrawerOpen = false;
    this.navigate("support-review");
  },

  async regenerateKnowledgeProfiles(knowledge: KnowledgeBase): Promise<void> {
    try {
      await ElMessageBox.confirm(
        `将为“${knowledge.name}”全部文档重新生成待上线 Profile 属性。` +
          "不会直接修改线上 Profile，也不会影响 RAG；完成后请在 Profile Review 检查并上线。",
        "重新生成待上线属性",
        { type: "warning", confirmButtonText: "提交生成任务", cancelButtonText: "取消" },
      );
    } catch {
      return;
    }
    await this.runAction(`document-profile-generate:${knowledge.id}`, async () => {
      const submission = await adminApi.generateKnowledgeProfiles(knowledge.id);
      this.trackTask(submission, {
        successMessage: "Profile 属性已生成，请在 Profile Review 检查并上线",
        knowledgeId: knowledge.id,
      });
      if (knowledge.id === this.selectedKnowledgeId) await this.loadKnowledgeDetails();
    });
    if (!this.error) this.notice = "全部待上线 Profile 属性生成任务已提交；完成后将弹出提醒";
  },

  async generateKnowledgeProfiles(): Promise<void> {
    if (!this.selectedKnowledgeId) return;
    const knowledgeId = this.selectedKnowledgeId;
    await this.runAction(`document-profile-generate:${knowledgeId}`, async () => {
      const submission = await adminApi.generateKnowledgeProfiles(knowledgeId);
      this.trackTask(submission, { successMessage: "Profile 属性已生成，请检查并上线", knowledgeId });
      this.latestDocumentProfileTasks[knowledgeId] = {
        id: submission.task_id, task_type: submission.task_type, status: submission.status,
        stage: "QUEUED", progress: 0, attempt: 0, index_id: "", error_code: null,
        error_message: null, metadata: {}, created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
      };
      await this.hydrateTrackedTask(submission.task_id);
    });
  },

  async generateDocumentProfile(documentId: string): Promise<void> {
    if (!this.selectedKnowledgeId) return;
    const knowledgeId = this.selectedKnowledgeId;
    await this.runAction(`document-profile-generate:${documentId}`, async () => {
      const submission = await adminApi.generateDocumentProfile(knowledgeId, documentId);
      this.trackTask(submission, { successMessage: "文档 Profile 属性已生成", knowledgeId });
      await this.hydrateTrackedTask(submission.task_id);
      await this.loadKnowledgeProfiles();
    });
  },

  async cancelDocumentProfileTask(taskId: string): Promise<void> {
    /* Cooperative stop: the backend flips task status to CANCELLED, the
     * worker observes it between documents, and the next poll settles the
     * UI.  In-flight documents finish; the rest are skipped. */
    await this.runAction(`task-cancel:${taskId}`, async () => {
      const task = await adminApi.cancelTask(taskId);
      this.syncPolledTask(task);
      this.notice = "已请求停止任务；正在处理的文档完成后即停止";
    });
  },

  async updateDocumentProfile(documentId: string, payload: DocumentProfileIntent): Promise<void> {
    if (!this.selectedKnowledgeId) return;
    await this.runAction(`document-profile-edit:${documentId}`, async () => {
      await adminApi.updateDocumentProfile(this.selectedKnowledgeId, documentId, payload);
      await this.loadKnowledgeProfiles();
    });
  },

  async checkKnowledgeProfiles(): Promise<ProfilePublishCheck | null> {
    if (!this.selectedKnowledgeId) return null;
    let result: ProfilePublishCheck | null = null;
    await this.runAction(`document-profile-check:${this.selectedKnowledgeId}`, async () => {
      result = await adminApi.checkKnowledgeProfiles(this.selectedKnowledgeId);
    });
    return result;
  },

  async publishKnowledgeProfiles(): Promise<void> {
    if (!this.selectedKnowledgeId) return;
    await this.runAction(`document-profile-publish:${this.selectedKnowledgeId}`, async () => {
      const result = await adminApi.publishKnowledgeProfiles(this.selectedKnowledgeId);
      this.knowledgeProfileSummary = result.summary;
      this.notice = `已上线 ${result.published_count} 项 Profile 属性`;
    });
  },

  async discardPendingKnowledgeProfiles(): Promise<void> {
    if (!this.selectedKnowledgeId) return;
    await this.runAction(`document-profile-discard:${this.selectedKnowledgeId}`, async () => {
      const result = await adminApi.discardPendingKnowledgeProfiles(this.selectedKnowledgeId);
      await this.loadKnowledgeProfiles();
      this.notice = `已取消 ${result.discarded_count} 项待上线变更`;
    });
  },

  async buildIndex(): Promise<void> {
    if (!this.selectedKnowledgeId) return;
    await this.run(async () => {
      const build = await adminApi.buildIndex(this.selectedKnowledgeId);
      if (build.task_id) {
        this.trackTask(
          { task_id: build.task_id, task_type: "INDEX_BUILD", status: "QUEUED", replayed: build.coalesced },
          {
            successMessage: "RAG 候选索引构建已完成，请检查后设为当前索引；Profile 将在后台单独生成",
            knowledgeId: this.selectedKnowledgeId,
          },
        );
      }
      await Promise.all([this.loadKnowledgeBases(), this.loadKnowledgeDetails()]);
    }, "RAG 候选索引构建已提交；完成后需明确设为当前索引，Profile 将在后台单独生成");
  },

  async refreshProfileDrafts(indexId: string): Promise<void> {
    await this.runAction(`refresh-profile:${indexId}`, async () => {
      const submission = await adminApi.refreshProfileDrafts(indexId);
      this.trackTask(submission, {
        successMessage: "Profile 重建已完成，请刷新候选草稿进行审核",
        knowledgeId: this.selectedKnowledgeId,
        releaseId: this.releaseForIndex(indexId)?.id,
      });
      this.registerProfileTaskSubmission(submission, indexId);
      await this.loadIndexTasks();
      if (this.latestProfileTasks[indexId]?.id !== submission.task_id) {
        this.registerProfileTaskSubmission(submission, indexId);
      }
      await this.hydrateTrackedTask(submission.task_id);
    });
    if (!this.error) this.notice = "Profile 重建任务已提交；完成后将弹出提醒";
  },

  async regenerateProfileDraft(draftId: string): Promise<void> {
    await this.runAction(`regenerate-profile:${draftId}`, async () => {
      const submission = await adminApi.regenerateProfileDraft(draftId);
      this.trackTask(submission, {
        successMessage: "Profile 草稿重新生成完成，请返回审核工作台复核",
        knowledgeId: this.selectedKnowledgeId,
      });
      await this.hydrateTrackedTask(submission.task_id);
      await this.loadIndexTasks();
    });
    if (!this.error) this.notice = "Profile 草稿重新生成任务已提交";
  },

  async updateProfileDraft(draftId: string, payload: ProfileDraftPatch): Promise<void> {
    await this.runAction(`profile-draft:${draftId}`, async () => {
      const draft = await adminApi.updateProfileDraft(draftId, payload);
      const release = await adminApi.getKnowledgeRelease(draft.release_id);
      this.releaseDetails[release.id] = release;
      this.releases = this.releases.map((item) =>
        item.id === release.id ? { ...item, ...release } : item,
      );
      if (payload.status === "APPROVED") {
        this.notice = "草稿已批准；全部草稿完成后请执行发布前验证";
      }
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
      if (validation.publishable) {
        this.notice = "发布前验证通过，当前 Support Release 可以发布";
      } else if (validation.review_required_count) {
        this.notice = `发布前验证完成：仍有 ${validation.review_required_count} 篇草稿待审核或批准`;
      } else if (validation.valid) {
        this.notice = "发布前验证完成，但当前候选暂不可发布";
      }
    });
  },

  async publishKnowledgeRelease(releaseId: string): Promise<void> {
    await this.runAction(`publish-release:${releaseId}`, async () => {
      const published = await adminApi.publishKnowledgeRelease(releaseId);
      this.releaseDetails[releaseId] = published;
      await Promise.all([this.loadKnowledgeBases(), this.loadKnowledgeDetails()]);
    });
  },

  async rebaseKnowledgeRelease(releaseId: string): Promise<ReleaseRebaseResult | null> {
    let result: ReleaseRebaseResult | null = null;
    await this.runAction(`rebase-release:${releaseId}`, async () => {
      result = await adminApi.rebaseKnowledgeRelease(releaseId);
      await Promise.all([this.loadKnowledgeBases(), this.loadKnowledgeDetails()]);
    });
    return result;
  },

  async rollbackSupportRelease(releaseId: string): Promise<void> {
    await this.runAction(`rollback-support-release:${releaseId}`, async () => {
      await adminApi.rollbackSupportRelease(this.selectedKnowledgeId, releaseId);
      await Promise.all([this.loadKnowledgeBases(), this.loadKnowledgeDetails()]);
    });
  },

  async rollbackRagIndex(indexId: string): Promise<void> {
    await this.runAction(`rollback-rag-index:${indexId}`, async () => {
      await adminApi.rollbackRagIndex(this.selectedKnowledgeId, indexId);
      await Promise.all([this.loadKnowledgeBases(), this.loadKnowledgeDetails()]);
    });
  },

  async activateIndex(indexId: string): Promise<void> {
    await this.runAction(`activate-index:${indexId}`, async () => {
      await adminApi.activateIndex(indexId);
      await Promise.all([this.loadKnowledgeBases(), this.loadKnowledgeDetails()]);
    });
    if (!this.error) this.notice = "当前 RAG 索引已切换";
  },

  async gcIndexes(): Promise<void> {
    if (!this.selectedKnowledgeId) return;
    await this.run(async () => {
      const result = await adminApi.gcIndexes(this.selectedKnowledgeId);
      const parts = [`已回收 ${result.deleted_count} 个旧索引`];
      if (result.purged_conversation_count) {
        parts.push(`清理 ${result.purged_conversation_count} 个超过保留期的会话`);
      }
      if (result.skipped_count) {
        parts.push(`${result.skipped_count} 个索引因新增引用被跳过`);
      }
      this.notice = parts.join("；");
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
      let forced = false;
      let released = 0;
      try {
        await adminApi.deleteIndex(indexId);
      } catch (cause) {
        const frozenCount =
          cause instanceof ApiRequestError && cause.status === 409
            ? Number(cause.details.frozen_conversation_count ?? 0)
            : 0;
        if (frozenCount <= 0) throw cause;
        const confirmed = await ElMessageBox.confirm(
          `该索引仍被 ${frozenCount} 个冻结会话引用。强制删除会解绑这些会话（焦点会话将重置，聊天历史保留），是否继续？`,
          "强制删除索引",
          { type: "warning", confirmButtonText: "强制删除", cancelButtonText: "取消" },
        )
          .then(() => true)
          .catch(() => false);
        if (!confirmed) return;
        forced = true;
        released = (await adminApi.deleteIndex(indexId, true)).released_conversation_count ?? 0;
      }
      await this.loadKnowledgeDetails();
      this.notice = forced
        ? released
          ? `索引已删除；解绑 ${released} 个冻结会话引用`
          : "索引已删除"
        : "索引已删除";
    });
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
    let successMessage = "模型配置已保存，下一轮对话生效";
    await this.run(async () => {
      const result = await adminApi.updateModel(modelId, payload);
      this.models = await adminApi.listModels();
      if (payload.rebuild_all_confirmed && result.rebuilds?.length) {
        successMessage = `模型配置已保存；已对 ${result.rebuilds.length} 个知识库自动发起 RAG 全量重建`;
      }
    }, "");
    if (!this.error) this.notice = successMessage;
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
    this.credentialRejected = false;
    if (token) sessionStorage.setItem("rag_service_token", token);
    else sessionStorage.removeItem("rag_service_token");
    this.notice = "访问凭据仅保存在当前浏览器会话";
  },

  clearToken(): void {
    sessionStorage.removeItem("rag_service_token");
    this.serviceToken = "";
    this.credentialRejected = false;
    this.notice = "本地访问凭据已清除";
  },
});

export function useAdminStore() {
  return adminStore;
}

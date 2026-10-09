import { h, reactive } from "vue";
import { ElButton, ElMessageBox, ElNotification } from "element-plus";

import { adminApi, ApiRequestError } from "@/api/admin";
import { clampedPage } from "@/utils/paging";
import {
  clearServiceCredential,
  readServiceCredential,
  saveServiceCredential,
} from "@/api/serviceCredential";
import type { ServiceCredentialRejectionReason } from "@/api/serviceCredential";
import type {
  DocumentCardState,
  DocumentItem,
  DocumentListQuery,
  Page,
  DocumentProfileContext,
  KnowledgeProfileSummary,
  ProfilePublishCheck,
  DocumentProfileIntent,
  KnowledgeBase,
  KnowledgeAssistantConfig,
  KnowledgeIndex,
  IndexTask,
  KnowledgeRelease,
  ModelConfig,
  ModelConfigUpdatePayload,
  ModelProbeResult,
  ProfileDraftPatch,
  ProfileFeedback,
  PromptTemplate,
  RagDocumentCard,
  RagDocumentCardPayload,
  RagDocumentCardReadiness,
  RagTrace,
  ReleaseRebaseResult,
  SystemInfo,
  TaskRecord,
  ModelRuntimeStatus,
  TaskSubmission,
  TraceSummary,
} from "@/types/admin";

const SELECTED_KNOWLEDGE_STORAGE_KEY = "rag_admin_selected_knowledge_id";
const TRACKED_TASKS_STORAGE_KEY = "rag_admin_tracked_tasks";
let systemInfoLoadGeneration = 0;
let modelListLoadGeneration = 0;
let modelRuntimeLoadGeneration = 0;
let modelActionGeneration = 0;
/* pollTrackedTasks is reached from the poller, task submissions and view
 * actions; overlapping callers share one in-flight run so a finished task is
 * announced and refreshed exactly once. */
let trackedTaskPoll: Promise<void> | null = null;
/* Bumped whenever the document list query (filters/sort/page/knowledge base)
 * changes, so a slower response for an older query never replaces the page. */
let documentQueryGeneration = 0;
/** Skipped documents listed in the alert; the total is shown when there are more. */
const SKIPPED_ALERT_LIMIT = 50;

/** `listStale`: the write succeeded but the follow-up list refresh failed, so `models` may be outdated. */
export type ModelActionResult = { ok: true; listStale?: boolean } | { ok: false; error: string };
/**
 * How a notice is presented: `success` is a short transient toast; `caveat`
 * is a completed action whose effect boundary must be read (stays until
 * closed); `warning` means the outcome needs attention.
 */
export type NoticeLevel = "success" | "caveat" | "warning";
type ModelActionNotice = string | { message: string; level: NoticeLevel; listStale?: boolean };

const TASK_FOLLOW_UPS: Record<string, { view: string; label: string } | undefined> = {
  INDEX_BUILD: { view: "indexes", label: "前往索引管理（检查后设为当前 RAG）" },
  RAG_DOCUMENT_CARD_GENERATE: { view: "documents", label: "前往文档管理确认 RAG 理解" },
};

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
  /** Current server page of the document list (see documentFilters). */
  documents: [] as DocumentItem[],
  documentTotal: 0,
  documentPage: 1,
  documentPageSize: 50,
  documentFilters: {
    buildStatus: "",
    cardState: "" as DocumentCardState | "",
    sort: "created_at" as NonNullable<DocumentListQuery["sort"]>,
    order: "desc" as NonNullable<DocumentListQuery["order"]>,
  },
  documentsLoading: false,
  /** Documents skipped by the latest build (ADR-0022 alert), independent of paging. */
  skippedDocuments: [] as DocumentItem[],
  skippedDocumentTotal: 0,
  indexes: [] as KnowledgeIndex[],
  traces: [] as TraceSummary[],
  models: [] as ModelConfig[],
  prompts: [] as PromptTemplate[],
  elements: [] as Array<Record<string, unknown>>,
  chunks: [] as Array<Record<string, unknown>>,
  documentProfileContext: null as DocumentProfileContext | null,
  knowledgeProfileSummary: null as KnowledgeProfileSummary | null,
  ragDocumentCardReadiness: null as RagDocumentCardReadiness | null,
  ragDocumentCard: null as RagDocumentCard | null,
  documentInspectionIndexId: "",
  latestIndexTasks: {} as Record<string, IndexTask | undefined>,
  latestProfileTasks: {} as Record<string, IndexTask | undefined>,
  latestDocumentProfileTasks: {} as Record<string, IndexTask | undefined>,
  latestRagDocumentCardTasks: {} as Record<string, IndexTask | undefined>,
  releases: [] as KnowledgeRelease[],
  releaseDetails: {} as Record<string, KnowledgeRelease | undefined>,
  profileFeedback: [] as ProfileFeedback[],
  systemInfo: null as SystemInfo | null,
  /** Actual provider per model type (null = not loaded / unknown). */
  modelRuntime: null as ModelRuntimeStatus | null,

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
  noticeLevel: "success" as NoticeLevel,
  credentialRejected: false,

  /* forms */
  newKnowledgeName: "",
  newKnowledgeDescription: "",
  serviceToken: readServiceCredential(),

  /* playground */
  question: "如何配置数据库？",
  playgroundOptions: { vector_top_k: 10, bm25_top_k: 10, fusion_top_k: 20 },
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
  /** Set a notice together with its presentation level (see NoticeLevel). */
  announce(message: string, level: NoticeLevel = "success"): void {
    this.noticeLevel = level;
    this.notice = message;
  },

  async run(action: () => Promise<void>, success = ""): Promise<void> {
    this.activeRequestCount += 1;
    this.loading = true;
    this.error = "";
    this.notice = "";
    this.noticeLevel = "success";
    try {
      await action();
      if (success) this.announce(success);
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

  async openTaskFollowUp(trackedTask: TrackedTask, view: string): Promise<void> {
    if (trackedTask.knowledgeId && this.selectedKnowledgeId !== trackedTask.knowledgeId) {
      if (!this.knowledgeBases.some((kb) => kb.id === trackedTask.knowledgeId)) return;
      this.selectedKnowledgeId = trackedTask.knowledgeId;
      await this.selectKnowledge();
    }
    this.navigate(view);
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

  pollTrackedTasks(): Promise<void> {
    if (!trackedTaskPoll) {
      trackedTaskPoll = this.pollTrackedTasksOnce().finally(() => {
        trackedTaskPoll = null;
      });
    }
    return trackedTaskPoll;
  },

  async pollTrackedTasksOnce(): Promise<void> {
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
      const generatedCount = Number(task.metadata.generated_count ?? 0);
      const failedCount = Number(task.metadata.failed_count ?? 0);
      const completedWithFailures = succeeded && Number.isFinite(failedCount) && failedCount > 0;
      const message = completedWithFailures
        ? `RAG 文档理解生成完成：成功 ${generatedCount} 份，失败 ${failedCount} 份；请查看失败文档后重试`
        : succeeded
          ? trackedTask.successMessage
          : cancelled
            ? "生成任务已停止；已完成文档的待上线属性保留，可继续编辑或重新生成"
            : task.error_message || "后台任务执行失败，请在任务中心查看详情";
      // Task completion is announced once, as a notification; it is not
      // duplicated into the transient success notice.
      // A finished build or card batch leads to a manual next step on another
      // page; offer it from the notification instead of only describing it.
      const followUp = succeeded || completedWithFailures ? TASK_FOLLOW_UPS[trackedTask.taskType] : undefined;
      const notification = ElNotification({
        title: cancelled
          ? "任务已停止"
          : completedWithFailures
            ? "任务部分失败"
            : succeeded
              ? "任务已完成"
              : "任务执行失败",
        message: followUp
          ? h("div", { class: "task-follow-up" }, [
              h("p", message),
              h(
                ElButton,
                {
                  type: "primary",
                  size: "small",
                  link: true,
                  onClick: () => {
                    notification.close();
                    void this.openTaskFollowUp(trackedTask, followUp.view);
                  },
                },
                () => followUp.label,
              ),
            ])
          : message,
        type: cancelled || completedWithFailures ? "warning" : succeeded ? "success" : "error",
        // Keep actionable success notices until the operator acts or closes them.
        duration: followUp ? 0 : (succeeded && !completedWithFailures) || cancelled ? 5_000 : 0,
      });
      if (succeeded) {
        this.trackChainedProfileTask(task, trackedTask);
        this.trackChainedRagCardTask(task, trackedTask);
      }
      if (trackedTask.taskType === "KNOWLEDGE_BASE_DELETE" && succeeded) {
        if (this.selectedKnowledgeId === trackedTask.knowledgeId) {
          this.selectedKnowledgeId = "";
          writeSelectedKnowledgeId("");
          this.selectedDocument = null;
          this.documentDrawerOpen = false;
        }
        await this.loadKnowledgeBases();
      } else if (
        (succeeded || cancelled || task.status === "FAILED")
        && trackedTask.knowledgeId === this.selectedKnowledgeId
      ) {
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
    } else if (task.task_type === "RAG_DOCUMENT_CARD_GENERATE") {
      this.latestRagDocumentCardTasks[indexId] = indexTask;
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

  trackChainedRagCardTask(task: TaskRecord, trackedTask: TrackedTask): void {
    const chainedId = task.metadata?.rag_document_card_task_id;
    if (typeof chainedId !== "string" || !chainedId) return;
    if (this.trackedTasks.some((item) => item.taskId === chainedId)) return;
    this.trackedTasks.push({
      taskId: chainedId,
      taskType: "RAG_DOCUMENT_CARD_GENERATE",
      successMessage: "RAG 文档理解卡片已生成，请在文档详情中检查并确认",
      knowledgeId: trackedTask.knowledgeId,
    });
    writeTrackedTasks(this.trackedTasks);
  },

  async loadKnowledgeBases(): Promise<void> {
    const incoming = await adminApi.listKnowledgeBases();
    this.knowledgeBases = incoming.map((kb) => {
      const current = this.knowledgeBases.find((item) => item.id === kb.id);
      if (current && (current.assistant_config_revision ?? 0) > (kb.assistant_config_revision ?? 0)) {
        return {
          ...kb,
          assistant_online: current.assistant_online,
          assistant_config_revision: current.assistant_config_revision,
        };
      }
      return kb;
    });
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
  applyKnowledgeAssistantConfig(config: KnowledgeAssistantConfig): void {
    const kb = this.knowledgeBases.find((item) => item.id === config.knowledge_id);
    if (kb && config.revision >= (kb.assistant_config_revision ?? 0)) {
      kb.assistant_online = config.assistant_online;
      kb.assistant_config_revision = config.revision;
    }
  },

  async updateKnowledgeBaseDescription(
    knowledge: KnowledgeBase,
    description: string,
  ): Promise<KnowledgeBase> {
    const actionKey = `update-knowledge:${knowledge.id}`;
    this.actionLoading[actionKey] = true;
    this.error = "";
    try {
      const updated = await adminApi.updateKnowledgeBase(knowledge.id, {
        description: description.trim() || null,
        expected_updated_at: knowledge.updated_at,
      });
      const index = this.knowledgeBases.findIndex((item) => item.id === updated.id);
      if (index >= 0) this.knowledgeBases[index] = updated;
      this.announce("知识库说明已保存；启用专业技术助手后，新请求将使用新的范围", "caveat");
      return updated;
    } catch (cause) {
      this.error = cause instanceof Error ? cause.message : "知识库说明保存失败";
      throw cause;
    } finally {
      delete this.actionLoading[actionKey];
    }
  },

  async loadSystemInfo(): Promise<void> {
    const generation = ++systemInfoLoadGeneration;
    try {
      const info = await adminApi.getSystemInfo();
      if (generation === systemInfoLoadGeneration) this.systemInfo = info;
    } catch {
      if (generation === systemInfoLoadGeneration) this.systemInfo = null;
    }
  },

  /**
   * Which model each type really runs with, including silent fallbacks to
   * simulated models. Failures leave the status unknown (null) rather than
   * keeping an older answer that may no longer be true.
   */
  async loadModelRuntime(): Promise<void> {
    const generation = ++modelRuntimeLoadGeneration;
    const credential = this.serviceToken;
    const isCurrent = () => generation === modelRuntimeLoadGeneration
      && this.hasServiceCredential && this.serviceToken === credential;
    if (!this.hasServiceCredential) {
      this.modelRuntime = null;
      return;
    }
    try {
      const status = await adminApi.getModelRuntime();
      if (isCurrent()) this.modelRuntime = Array.isArray(status?.models) ? status : null;
    } catch {
      if (isCurrent()) this.modelRuntime = null;
    }
  },

  async loadModels(): Promise<void> {
    const generation = ++modelListLoadGeneration;
    const credential = this.serviceToken;
    const isCurrent = () => generation === modelListLoadGeneration
      && this.hasServiceCredential && this.serviceToken === credential;
    try {
      const models = await adminApi.listModels();
      if (isCurrent()) this.models = models;
    } catch (error) {
      if (isCurrent()) throw error;
    }
  },

  /** Server query for the current document page; the card filter needs the active index. */
  /**
   * Server query for the current document page. The active index is always
   * attached so every row carries its RAG card state (rendered by the table);
   * the card filter can only apply when such an index exists.
   */
  documentListQuery(activeIndexId: string | null | undefined): DocumentListQuery {
    return {
      search: this.documentSearch.trim() || undefined,
      buildStatus: this.documentFilters.buildStatus || undefined,
      cardState: activeIndexId ? this.documentFilters.cardState || undefined : undefined,
      cardIndexId: activeIndexId ?? undefined,
      sort: this.documentFilters.sort,
      order: this.documentFilters.order,
      limit: this.documentPageSize,
      offset: (this.documentPage - 1) * this.documentPageSize,
    };
  },

  activeIndexIdFor(knowledgeId: string): string | null {
    const knowledge = this.knowledgeBases.find((item) => item.id === knowledgeId);
    return knowledge?.rag_active_index_id ?? knowledge?.active_index_id ?? null;
  },

  /** Filters or sort changed: reload the document list from the first page. */
  async reloadDocuments(): Promise<void> {
    this.documentPage = 1;
    await this.loadDocumentPage();
  },

  /** Load the current document page (page/size changes keep the filters). */
  async loadDocumentPage(): Promise<void> {
    const knowledgeId = this.selectedKnowledgeId;
    if (!knowledgeId) return;
    const generation = ++documentQueryGeneration;
    this.documentsLoading = true;
    try {
      const page = await adminApi.listDocumentsPage(
        knowledgeId,
        this.documentListQuery(this.activeIndexIdFor(knowledgeId)),
      );
      if (this.selectedKnowledgeId === knowledgeId) this.commitDocumentPage(generation, page);
    } catch (cause) {
      if (generation === documentQueryGeneration) {
        this.error = cause instanceof Error ? cause.message : "文档列表加载失败";
      }
    } finally {
      this.settleDocumentLoad(generation);
    }
  },

  /**
   * Write a fetched page if it still belongs to the latest document query.
   * A page beyond the end (rows deleted or filtered away meanwhile) moves
   * to the last valid page instead of showing an empty list without a pager.
   */
  commitDocumentPage(generation: number, page: Page<DocumentItem>): void {
    if (generation !== documentQueryGeneration) return;
    const target = clampedPage(this.documentPage, this.documentPageSize, page.items.length, page.total);
    if (target !== null) {
      this.documentPage = target;
      void this.loadDocumentPage();
      return;
    }
    this.documents = page.items;
    this.documentTotal = page.total;
  },

  /** Every document load path clears the overlay for the query that is still current. */
  settleDocumentLoad(generation: number): void {
    if (generation === documentQueryGeneration) this.documentsLoading = false;
  },

  async loadKnowledgeDetails(): Promise<void> {
    if (!this.selectedKnowledgeId) {
      this.documents = [];
      this.documentTotal = 0;
      this.skippedDocuments = [];
      this.skippedDocumentTotal = 0;
      this.indexes = [];
      this.releases = [];
      this.releaseDetails = {};
      this.profileFeedback = [];
      this.latestIndexTasks = {};
      this.latestProfileTasks = {};
      this.latestDocumentProfileTasks = {};
      this.latestRagDocumentCardTasks = {};
      this.knowledgeProfileSummary = null;
      this.ragDocumentCardReadiness = null;
      this.ragDocumentCard = null;
      return;
    }
    const knowledgeId = this.selectedKnowledgeId;
    const activeIndexId = this.activeIndexIdFor(knowledgeId);
    // A card filter cannot apply without an active index; clear it instead of
    // keeping a filter the request would silently ignore.
    if (!activeIndexId) this.documentFilters.cardState = "";
    const documentGeneration = ++documentQueryGeneration;
    try {
      const [documentPage, skipped, indexes, profileSummary, readiness] = await Promise.all([
        adminApi.listDocumentsPage(knowledgeId, this.documentListQuery(activeIndexId)),
        adminApi
          .listDocumentsPage(knowledgeId, { buildStatus: "SKIPPED", limit: SKIPPED_ALERT_LIMIT })
          .catch(() => null),
        adminApi.listIndexes(knowledgeId),
        adminApi.getKnowledgeProfiles(knowledgeId).catch(() => null),
        activeIndexId
          ? adminApi.getRagDocumentCardReadiness(knowledgeId, activeIndexId).catch(() => null)
          : Promise.resolve(null),
      ]);
      if (this.selectedKnowledgeId !== knowledgeId) return;
      this.commitDocumentPage(documentGeneration, documentPage);
      // Only this knowledge base's result may fill the alert; a failed query
      // leaves it empty rather than showing another knowledge base's rows.
      this.skippedDocuments = skipped?.items ?? [];
      this.skippedDocumentTotal = skipped?.total ?? 0;
      this.indexes = indexes;
      this.releases = [];
      this.profileFeedback = [];
      this.knowledgeProfileSummary = profileSummary;
      this.ragDocumentCardReadiness = readiness;
      this.releaseDetails = {};
      await this.loadIndexTasks(indexes, knowledgeId);
    } finally {
      this.settleDocumentLoad(documentGeneration);
    }
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
          tasks.find((task) => task.task_type === "RAG_DOCUMENT_CARD_GENERATE"),
        ] as const;
      }),
    );
    if (this.selectedKnowledgeId !== targetKnowledgeId) return;
    const entries = results.flatMap((result) => (result.status === "fulfilled" ? [result.value] : []));
    this.latestIndexTasks = Object.fromEntries(entries.map(([indexId, task]) => [indexId, task]));
    this.latestProfileTasks = Object.fromEntries(
      entries.map(([indexId, , profileTask]) => [indexId, profileTask]),
    );
    this.latestRagDocumentCardTasks = Object.fromEntries(
      entries.map(([indexId, , , cardTask]) => [indexId, cardTask]),
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
      this.loadModels(),
      adminApi.listPrompts().then((value) => (this.prompts = value)),
      this.loadSystemInfo(),
      this.loadModelRuntime(),
    ]);
  },

  async selectKnowledge(): Promise<void> {
    const knowledgeId = this.selectedKnowledgeId;
    writeSelectedKnowledgeId(knowledgeId);
    this.documentPage = 1;
    this.skippedDocuments = [];
    this.skippedDocumentTotal = 0;
    this.selectedDocument = null;
    this.documentProfileContext = null;
    this.ragDocumentCard = null;
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
    if (!this.error) this.announce("知识库删除任务已提交；完成后将弹出提醒");
  },

  async uploadFile(file: File, idempotencyKey: string, requestBuild: boolean): Promise<void> {
    if (!this.selectedKnowledgeId) throw new Error("未选择知识库");
    await adminApi.uploadDocument(this.selectedKnowledgeId, file, idempotencyKey, requestBuild);
  },

  async renameDocument(document: DocumentItem): Promise<void> {
    let displayName = "";
    try {
      const result = await ElMessageBox.prompt("修改文档在知识库和后续回答中显示的名称。", "重命名文档", {
        confirmButtonText: "保存",
        cancelButtonText: "取消",
        inputValue: document.display_name,
        inputPlaceholder: "请输入文档名称",
        inputValidator: (value) => {
          const normalized = value.trim();
          if (!normalized) return "文档名称不能为空";
          return normalized.length <= 500 || "文档名称不能超过 500 个字符";
        },
      });
      displayName = String(result.value ?? "").trim();
    } catch {
      return;
    }
    await this.run(async () => {
      const updated = await adminApi.renameDocument(document.id, displayName);
      this.documents = this.documents.map((item) => (item.id === updated.id ? updated : item));
      if (this.selectedDocument?.id === updated.id) this.selectedDocument = updated;
    }, "文档名称已更新；后续回答立即生效，无需重建索引");
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
    if (successMessage) this.announce(successMessage);
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
      this.ragDocumentCard = null;
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
      const [elementResult, chunkResult, profileContext, documentCard] = await Promise.all([
        indexId
          ? adminApi.listElements(document.id, indexId)
          : Promise.resolve({ index_id: "", items: [] as Array<Record<string, unknown>> }),
        indexId
          ? adminApi.listChunks(document.id, indexId)
          : Promise.resolve({ index_id: "", items: [] as Array<Record<string, unknown>> }),
        adminApi.getDocumentProfileContext(document.id),
        indexId
          ? adminApi.getRagDocumentCard(document.knowledge_id, document.id, indexId).catch(() => null)
          : Promise.resolve(null),
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
      this.ragDocumentCard = documentCard;
    });
  },

  async generateRagDocumentCards(documentId?: string, replaceConfirmed = false): Promise<void> {
    const knowledgeId = this.selectedKnowledgeId;
    const indexId = documentId
      ? this.documentInspectionIndexId || this.activeIndex?.id
      : this.activeIndex?.id;
    if (!knowledgeId || !indexId) return;
    await this.runAction(`rag-document-card-generate:${documentId ?? indexId}`, async () => {
      const submission = await adminApi.generateRagDocumentCards(knowledgeId, {
        index_id: indexId, document_id: documentId, replace_confirmed: replaceConfirmed,
      });
      this.trackTask(submission, {
        successMessage: "RAG 文档理解卡片已生成，请检查并确认", knowledgeId,
      });
      this.latestRagDocumentCardTasks[indexId] = {
        id: submission.task_id, task_type: submission.task_type, status: submission.status,
        stage: "QUEUED", progress: 0, attempt: 0, index_id: indexId,
        error_code: null, error_message: null, metadata: {},
        created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
      };
      await this.hydrateTrackedTask(submission.task_id);
    });
    if (!this.error) this.announce("RAG 文档理解生成任务已提交");
  },

  async saveRagDocumentCard(payload: RagDocumentCardPayload): Promise<void> {
    const knowledgeId = this.selectedKnowledgeId;
    const document = this.selectedDocument;
    const card = this.ragDocumentCard;
    if (!knowledgeId || !document || !card) return;
    await this.runAction(`rag-document-card-save:${document.id}`, async () => {
      this.ragDocumentCard = await adminApi.updateRagDocumentCard(
        knowledgeId, document.id, card.index_id, card.revision, payload,
      );
    });
    if (!this.error) {
      this.announce("卡片已保存，请确认后用于后续上线检查", "caveat");
      await this.loadDocumentPage();
    }
  },

  async reviewRagDocumentCard(reviewStatus: "CONFIRMED" | "REJECTED"): Promise<void> {
    const knowledgeId = this.selectedKnowledgeId;
    const document = this.selectedDocument;
    const card = this.ragDocumentCard;
    if (!knowledgeId || !document || !card) return;
    await this.runAction(`rag-document-card-review:${document.id}`, async () => {
      this.ragDocumentCard = await adminApi.reviewRagDocumentCard(
        knowledgeId, document.id, card.index_id, card.revision, reviewStatus,
      );
      if (this.activeIndex?.id === card.index_id) {
        this.ragDocumentCardReadiness = await adminApi.getRagDocumentCardReadiness(
          knowledgeId, card.index_id,
        );
      }
    });
    if (!this.error) {
      this.announce(reviewStatus === "CONFIRMED" ? "卡片已确认" : "卡片已拒绝");
      // Row card states (and a card-state filter) change with the review.
      await this.loadDocumentPage();
    }
  },

  async confirmRagDocumentCards(): Promise<void> {
    const knowledgeId = this.selectedKnowledgeId;
    const indexId = this.activeIndex?.id;
    const autoCount = this.ragDocumentCardReadiness?.auto_count ?? 0;
    if (!knowledgeId || !indexId || autoCount === 0) return;
    try {
      await ElMessageBox.confirm(
        `将一键确认当前索引 ${autoCount} 份待确认的 RAG 理解卡片。已拒绝、生成失败或缺失的卡片不受影响。`,
        "一键确认 RAG 理解",
        { type: "warning", confirmButtonText: "确认", cancelButtonText: "取消" },
      );
    } catch {
      return;
    }
    await this.runAction(`rag-document-card-confirm-batch:${indexId}`, async () => {
      const result = await adminApi.confirmRagDocumentCards(knowledgeId, indexId);
      if (this.activeIndex?.id === indexId) this.ragDocumentCardReadiness = result.readiness;
    });
    if (!this.error) {
      this.announce(`已确认 ${autoCount} 份 RAG 理解卡片`);
      await this.loadDocumentPage();
    }
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
    if (!this.error) this.announce("全部待上线 Profile 属性生成任务已提交；完成后将弹出提醒");
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
      this.announce("已请求停止任务；正在处理的文档完成后即停止");
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
      this.announce(`已上线 ${result.published_count} 项 Profile 属性`);
    });
  },

  async discardPendingKnowledgeProfiles(): Promise<void> {
    if (!this.selectedKnowledgeId) return;
    await this.runAction(`document-profile-discard:${this.selectedKnowledgeId}`, async () => {
      const result = await adminApi.discardPendingKnowledgeProfiles(this.selectedKnowledgeId);
      await this.loadKnowledgeProfiles();
      this.announce(`已取消 ${result.discarded_count} 项待上线变更`);
    });
  },

  async buildIndex(options: { confirm?: boolean } = {}): Promise<void> {
    if (!this.selectedKnowledgeId) return;
    if (options.confirm) {
      const name = this.selectedKnowledge?.name ?? "当前知识库";
      try {
        await ElMessageBox.confirm(
          `将为「${name}」提交一次 RAG 候选索引构建。变更或缺少模型指纹的文档会重新解析，`
            + "可能产生模型调用费用；构建完成后需明确设为当前索引才会生效。",
          "构建索引",
          { type: "warning", confirmButtonText: "提交构建", cancelButtonText: "取消" },
        );
      } catch {
        return;
      }
    }
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

  async retryIndexBuild(indexId: string): Promise<void> {
    /* ADR-0023：失败/取消的索引由管理员手动重建。同一文档同一错误
     * 失败 2 次后，下一次构建会自动跳过该文档并完成其余文档。 */
    let confirmed = true;
    try {
      await ElMessageBox.confirm(
        "将基于当前文档重新创建一次构建。" +
          "同一文档以同一错误连续失败 2 次后，此次构建将自动跳过该文档并完成其余文档。",
        "重建索引",
        { type: "warning", confirmButtonText: "提交重建", cancelButtonText: "取消" },
      );
    } catch {
      confirmed = false;
    }
    if (!confirmed) return;
    await this.runAction(`retry-index:${indexId}`, async () => {
      const build = await adminApi.retryIndex(indexId);
      if (build.task_id) {
        this.trackTask(
          { task_id: build.task_id, task_type: "INDEX_BUILD", status: "QUEUED", replayed: false },
          {
            successMessage: "RAG 候选索引重建已完成，请检查后设为当前索引",
            knowledgeId: this.selectedKnowledgeId,
          },
        );
      }
      await Promise.all([this.loadKnowledgeBases(), this.loadKnowledgeDetails()]);
    });
    if (!this.error) {
      this.announce("重建已提交；完成后需明确设为当前 RAG 索引", "caveat");
    }
  },

  async cancelIndexBuild(indexId: string): Promise<void> {
    /* 协作停止：后端把任务行落 CANCELLED，构建循环在文档边界轮询到
     * 后收尾——正在处理的文档会完成，其余文档停止，索引落 CANCELLED。 */
    const task = this.latestIndexTasks[indexId];
    if (!task) {
      this.error = "未找到该构建的任务记录，无法停止";
      return;
    }
    await this.runAction(`task-cancel:${task.id}`, async () => {
      const cancelled = await adminApi.cancelTask(task.id);
      this.syncPolledTask(cancelled);
      this.announce("已请求停止构建；正在处理的文档完成后即停止");
    });
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
    if (!this.error) this.announce("Profile 重建任务已提交；完成后将弹出提醒");
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
    if (!this.error) this.announce("Profile 草稿重新生成任务已提交");
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
        this.announce("草稿已批准；全部草稿完成后请执行发布前验证", "caveat");
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
        this.announce("发布前验证通过，当前 Support Release 可以发布");
      } else if (validation.review_required_count) {
        this.announce(`发布前验证完成：仍有 ${validation.review_required_count} 篇草稿待审核或批准`, "warning");
      } else if (validation.valid) {
        this.announce("发布前验证完成，但当前候选暂不可发布", "warning");
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
    if (!this.error) this.announce("当前 RAG 索引已切换");
  },

  async gcIndexes(): Promise<void> {
    if (!this.selectedKnowledgeId) return;
    try {
      await ElMessageBox.confirm(
        "将按保留策略物理删除当前知识库中可回收的旧索引（含 chunk、embedding 和图片），"
          + "并对当前知识库执行会话、Trace 与任务记录的保留期清理；其他知识库不受影响。"
          + "当前 RAG 索引和仍被会话引用的索引不会删除。该操作不可恢复。",
        "回收旧索引",
        { type: "warning", confirmButtonText: "回收", cancelButtonText: "取消" },
      );
    } catch {
      return;
    }
    await this.run(async () => {
      const result = await adminApi.gcIndexes(this.selectedKnowledgeId);
      const parts = [`已回收 ${result.deleted_count} 个旧索引`];
      if (result.purged_conversation_count) {
        parts.push(`清理 ${result.purged_conversation_count} 个超过保留期的会话`);
      }
      if (result.skipped_count) {
        parts.push(`${result.skipped_count} 个索引因新增引用被跳过`);
      }
      this.announce(parts.join("；"), result.skipped_count ? "warning" : "success");
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
      this.announce(forced && released ? `索引已删除；解绑 ${released} 个冻结会话引用` : "索引已删除");
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

  async refreshOperationalState(): Promise<void> {
    if (!this.selectedKnowledgeId) return;
    // Sequential on purpose: loadKnowledgeDetails derives the active index
    // from knowledgeBases, so it must see the freshly loaded list.
    try {
      await this.loadKnowledgeBases();
    } catch {
      // Keep going with the previous list; details can still refresh.
    }
    await this.loadKnowledgeDetails().catch(() => undefined);
  },

  /**
   * Lightweight refresh while an index is BUILDING: knowledge list, documents,
   * indexes and the tasks of building indexes only. Profiles, RAG cards and
   * the per-index task history of finished indexes are left alone until a
   * build settles, which triggers one full refresh.
   */
  async refreshBuildingState(): Promise<void> {
    const knowledgeId = this.selectedKnowledgeId;
    if (!knowledgeId) return;
    const wasBuilding = new Set(
      this.indexes.filter((index) => index.status === "BUILDING").map((index) => index.id),
    );
    await this.loadKnowledgeBases().catch(() => undefined);
    if (this.selectedKnowledgeId !== knowledgeId) return;
    const documentGeneration = ++documentQueryGeneration;
    let documentPage: Page<DocumentItem>;
    let indexes: KnowledgeIndex[];
    try {
      [documentPage, indexes] = await Promise.all([
        adminApi.listDocumentsPage(knowledgeId, this.documentListQuery(this.activeIndexIdFor(knowledgeId))),
        adminApi.listIndexes(knowledgeId),
      ]);
      if (this.selectedKnowledgeId !== knowledgeId) return;
      if (indexes.some((index) => wasBuilding.has(index.id) && index.status !== "BUILDING")) {
        await this.loadKnowledgeDetails();
        return;
      }
      this.commitDocumentPage(documentGeneration, documentPage);
    } finally {
      this.settleDocumentLoad(documentGeneration);
    }
    this.indexes = indexes;
    const building = indexes.filter((index) => index.status === "BUILDING");
    const results = await Promise.allSettled(
      building.map(async (index) => [index.id, await adminApi.listIndexTasks(index.id)] as const),
    );
    if (this.selectedKnowledgeId !== knowledgeId) return;
    for (const result of results) {
      if (result.status !== "fulfilled") continue;
      const [indexId, tasks] = result.value;
      this.latestIndexTasks[indexId] = tasks.find((task) => (task.task_type ?? "INDEX_BUILD") === "INDEX_BUILD");
      this.latestRagDocumentCardTasks[indexId] = tasks.find(
        (task) => task.task_type === "RAG_DOCUMENT_CARD_GENERATE",
      );
    }
  },

  async runModelAction(action: () => Promise<ModelActionNotice>): Promise<ModelActionResult> {
    const generation = ++modelActionGeneration;
    const credential = this.serviceToken;
    const isCurrent = () => generation === modelActionGeneration
      && this.hasServiceCredential && this.serviceToken === credential;
    let result: ModelActionResult = { ok: true };
    await this.run(async () => {
      try {
        const notice = await action();
        if (typeof notice !== "string" && notice.listStale) result = { ok: true, listStale: true };
        if (isCurrent()) {
          if (typeof notice === "string") this.announce(notice);
          else this.announce(notice.message, notice.level);
          // Keys and providers may have changed what actually runs.
          void this.loadModelRuntime();
        }
      } catch (cause) {
        const error = cause instanceof Error ? cause.message : "模型配置操作失败";
        result = { ok: false, error };
        if (isCurrent()) this.error = error;
      }
    });
    return result;
  },

  /**
   * Reload the model list after a write the server has already accepted. A refresh
   * failure must not turn that success into a failed action (the dialog would stay open
   * and a repeat save would re-trigger the rebuild confirmation), so it is reported
   * as a warning on the success notice instead.
   */
  async refreshModelsAfterWrite(notice: { message: string; level: NoticeLevel }): Promise<ModelActionNotice> {
    try {
      await this.loadModels();
      return notice;
    } catch (cause) {
      const reason = cause instanceof Error ? cause.message : "未知错误";
      return {
        message: `${notice.message}；但刷新模型列表失败（${reason}），页面可能仍显示旧配置，请手动刷新`,
        level: "warning",
        listStale: true,
      };
    }
  },

  async saveModelConfig(modelId: string, payload: ModelConfigUpdatePayload): Promise<ModelActionResult> {
    let successMessage = "模型配置已保存，下一轮对话生效";
    // Only the plain LLM/Rerank save is a simple confirmation; the others
    // carry effect boundaries the administrator has to read.
    let level: NoticeLevel = "success";
    const modelType = this.models.find((model) => model.id === modelId)?.model_type;
    if (modelType === "EMBEDDING") {
      successMessage = "Embedding 配置已保存；查询侧后续请求读取新配置，请核对索引向量与模型一致性";
      level = "caveat";
    } else if (modelType === "OCR" || modelType === "VISION") {
      successMessage = "模型配置已保存，知识库已标记待重建；仅实际重新解析的文档使用新配置，旧解析可能复用";
      level = "caveat";
    }
    return this.runModelAction(async () => {
      const result = await adminApi.updateModel(modelId, payload);
      if (payload.rebuild_all_confirmed && result.rebuilds?.length) {
        successMessage = `模型配置已保存；已对 ${result.rebuilds.length} 个知识库提交 RAG 重建，请核对构建结果、活动索引及文档理解确认状态`;
        level = "caveat";
      }
      return this.refreshModelsAfterWrite({ message: successMessage, level });
    });
  },

  async putModelApiKey(modelId: string, apiKey: string): Promise<void> {
    await this.runModelAction(async () => {
      await adminApi.putApiKey(modelId, apiKey);
      return this.refreshModelsAfterWrite({
        message: "API 密钥已加密保存（数据库密钥优先于环境变量）",
        level: "success",
      });
    });
  },

  async clearModelApiKey(modelId: string): Promise<ModelActionResult> {
    return this.runModelAction(async () => {
      await adminApi.deleteApiKey(modelId);
      return this.refreshModelsAfterWrite({
        message: "数据库密钥已清除；有环境密钥时回退，否则模型调用可能不可用",
        level: "warning",
      });
    });
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
    saveServiceCredential(token);
    this.announce("访问凭据仅保存在当前浏览器会话");
  },

  clearToken(): void {
    clearServiceCredential();
    this.serviceToken = "";
    this.credentialRejected = false;
    this.announce("本地访问凭据已清除");
  },

  rejectCredential(reason: ServiceCredentialRejectionReason = "invalid"): void {
    clearServiceCredential();
    this.serviceToken = "";
    this.credentialRejected = true;
    modelRuntimeLoadGeneration++;
    this.modelRuntime = null;
    // "missing" fires the first time any request layer finds no token
    // configured yet — an onboarding state, not a failure, so it gets a
    // neutral prompt instead of the "invalid" wording used for a real 401.
    this.error =
      reason === "missing"
        ? "尚未配置服务访问凭据，请先在设置页填写后再使用管理功能"
        : "服务凭证缺失或无效，请重新配置";
  },
});

export function useAdminStore() {
  return adminStore;
}

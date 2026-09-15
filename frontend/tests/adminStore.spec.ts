import ElementPlus from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ElMessageBox } from "element-plus";

import { adminApi } from "@/api/admin";
import { adminStore } from "@/composables/adminStore";
import AdminStudio from "@/components/AdminStudio.vue";
import type { KnowledgeBase } from "@/types/admin";

const kb = {
  id: "kb-1",
  name: "产品库",
  description: null,
  status: "ENABLED",
  assistant_online: true,
  runtime_state: "READY",
  active_index_id: "idx-1",
  building_index_id: null,
  rebuild_required: false,
  document_count: 2,
  active_chunk_count: 10,
  created_at: "2026-08-17T00:00:00Z",
  updated_at: "2026-08-17T00:00:00Z",
};

describe("Assistant list consistency", () => {
  afterEach(() => vi.restoreAllMocks());

  it("keeps newer publication state when an earlier list request finishes later", async () => {
    adminStore.knowledgeBases = [
      { ...kb, assistant_online: false, assistant_config_revision: 0 },
      { ...kb, id: "kb-2", assistant_online: false, assistant_config_revision: 0 },
    ];
    let resolve!: (rows: KnowledgeBase[]) => void;
    vi.spyOn(adminApi, "listKnowledgeBases").mockImplementation(() => new Promise((done) => { resolve = done; }));
    const loading = adminStore.loadKnowledgeBases();
    adminStore.selectedKnowledgeId = "kb-2";
    adminStore.applyKnowledgeAssistantConfig({ knowledge_id: "kb-1", assistant_online: true, suggestions: [], revision: 2 });
    adminStore.applyKnowledgeAssistantConfig({ knowledge_id: "kb-1", assistant_online: false, suggestions: [], revision: 1 });
    resolve([{ ...kb, assistant_online: false, assistant_config_revision: 0 }, { ...kb, id: "kb-2", assistant_online: false }]);
    await loading;
    expect(adminStore.knowledgeBases[0]).toMatchObject({ assistant_online: true, assistant_config_revision: 2 });
    expect(adminStore.knowledgeBases[1]?.assistant_online).toBe(false);
  });
});

const index = {
  id: "idx-1",
  kb_id: "kb-1",
  version: 3,
  status: "ACTIVE",
  embedding_model_name: "bge-m3",
  embedding_dimension: 1024,
  bm25_engine: "pg",
  document_count: 2,
  element_count: 40,
  chunk_count: 12,
  build_reason: "MANUAL",
  activate_on_success: true,
  error_code: null,
  error_message: null,
  created_at: "2026-08-17T00:00:00Z",
  finished_at: "2026-08-17T00:10:00Z",
  activated_at: null,
};

const task = {
  id: "task-1",
  status: "RUNNING",
  stage: "CHUNKING",
  progress: 40,
  attempt: 1,
  index_id: "idx-1",
  error_code: null,
  error_message: null,
  metadata: { current_document: "a.docx", current_document_position: 2, total_documents: 5 },
  created_at: "2026-08-17T00:00:00Z",
  updated_at: "2026-08-17T00:00:00Z",
};

const traceDetail = {
  trace_id: "t-1",
  mode: "PLAYGROUND" as const,
  kb_id: "kb-1",
  index_id: "idx-1",
  question: "如何配置数据库？",
  status: "SUCCESS",
  latency: { total_ms: 400 },
  error: {},
  created_at: "2026-08-17T00:00:00Z",
  retrieval_result: { hits: 3 },
  rerank_result: {},
  selected_context: [],
  prompt: "PROMPT",
  answer: "答案",
  sources: [],
  citation_result: {},
  model_usage: {},
};

function stubApi() {
  vi.spyOn(adminApi, "listKnowledgeBases").mockResolvedValue([kb]);
  vi.spyOn(adminApi, "listDocuments").mockResolvedValue([]);
  vi.spyOn(adminApi, "listIndexes").mockResolvedValue([index]);
  vi.spyOn(adminApi, "listIndexTasks").mockResolvedValue([task]);
  vi.spyOn(adminApi, "listTraces").mockResolvedValue([]);
  vi.spyOn(adminApi, "listModels").mockResolvedValue([]);
  vi.spyOn(adminApi, "listPrompts").mockResolvedValue([]);
}

async function mountStudio(view = "indexes") {
  const wrapper = mount(AdminStudio, {
    props: { view },
    global: { plugins: [ElementPlus] },
  });
  await flushPromises();
  return wrapper;
}

describe("adminStore", () => {
  beforeEach(() => {
    sessionStorage.setItem("rag_service_token", "test-service-token");
    adminStore.serviceToken = "test-service-token";
    adminStore.credentialRejected = false;
    adminStore.knowledgeBases = [];
    adminStore.indexes = [];
    adminStore.latestIndexTasks = {};
    adminStore.latestRagDocumentCardTasks = {};
    adminStore.documentInspectionIndexId = "";
    adminStore.selectedKnowledgeId = "";
    adminStore.notice = "";
    adminStore.error = "";
    adminStore.trackedTasks = [];
    adminStore.actionLoading = {};
    adminStore.knowledgeSearch = "";
    adminStore.knowledgeStatusFilter = "";
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    sessionStorage.clear();
    adminStore.serviceToken = "";
    adminStore.credentialRejected = false;
    localStorage.clear();
  });

  it("aggregates totals and filters knowledge bases", () => {
    adminStore.knowledgeBases = [
      kb,
      { ...kb, id: "kb-2", name: "手册库", runtime_state: "BUILDING" },
    ];
    expect(adminStore.totalDocuments).toBe(4);
    expect(adminStore.totalChunks).toBe(20);
    adminStore.knowledgeSearch = "产品";
    expect(adminStore.filteredKnowledgeBases).toHaveLength(1);
    adminStore.knowledgeSearch = "";
    adminStore.knowledgeStatusFilter = "BUILDING";
    expect(adminStore.filteredKnowledgeBases).toHaveLength(1);
    expect(adminStore.filteredKnowledgeBases[0]!.name).toBe("手册库");
  });

  it("restores the previously selected knowledge base after a refresh", async () => {
    const secondKnowledgeBase = { ...kb, id: "kb-2", name: "手册库" };
    localStorage.setItem("rag_admin_selected_knowledge_id", secondKnowledgeBase.id);
    vi.spyOn(adminApi, "listKnowledgeBases").mockResolvedValue([kb, secondKnowledgeBase]);

    await adminStore.loadKnowledgeBases();

    expect(adminStore.selectedKnowledgeId).toBe(secondKnowledgeBase.id);
  });

  it("creates a knowledge base and closes the drawer on success", async () => {
    stubApi();
    const created = { ...kb, id: "kb-9", name: "新库" };
    const create = vi.spyOn(adminApi, "createKnowledgeBase").mockResolvedValue(created);
    adminStore.knowledgeDrawerOpen = true;
    adminStore.newKnowledgeName = "新库";
    await adminStore.createKnowledgeBase();
    expect(create).toHaveBeenCalledWith("新库", "");
    expect(adminStore.selectedKnowledgeId).toBe("kb-9");
    expect(adminStore.knowledgeDrawerOpen).toBe(false);
    expect(adminStore.notice).toBe("知识库已创建，默认不对 Assistant 上线");
  });

  it("updates a knowledge-base description by id and sends the displayed revision", async () => {
    adminStore.knowledgeBases = [kb, { ...kb, id: "kb-2" }];
    const updated = {
      ...kb,
      description: "VAU 图像配置和故障排查",
      updated_at: "2026-09-13T01:00:00Z",
    };
    const update = vi.spyOn(adminApi, "updateKnowledgeBase").mockResolvedValue(updated);

    const result = await adminStore.updateKnowledgeBaseDescription(kb, "  VAU 图像配置和故障排查  ");

    expect(update).toHaveBeenCalledWith("kb-1", {
      description: "VAU 图像配置和故障排查",
      expected_updated_at: kb.updated_at,
    });
    expect(result).toEqual(updated);
    expect(adminStore.knowledgeBases[0]).toEqual(updated);
    expect(adminStore.knowledgeBases[1]?.id).toBe("kb-2");
  });

  it("keeps the existing list value when a description update conflicts", async () => {
    adminStore.knowledgeBases = [kb];
    vi.spyOn(adminApi, "updateKnowledgeBase").mockRejectedValue(new Error("请重新加载后再保存"));

    await expect(adminStore.updateKnowledgeBaseDescription(kb, "未保存的新说明")).rejects.toThrow(
      "请重新加载后再保存",
    );

    expect(adminStore.knowledgeBases[0]?.description).toBeNull();
    expect(adminStore.error).toBe("请重新加载后再保存");
  });

  it("requires the knowledge name and deletes the selected knowledge base", async () => {
    stubApi();
    adminStore.knowledgeBases = [kb];
    adminStore.selectedKnowledgeId = kb.id;
    vi.spyOn(ElMessageBox, "prompt").mockResolvedValue({
      value: kb.name,
      action: "confirm",
    } as never);
    const remove = vi.spyOn(adminApi, "deleteKnowledgeBase").mockResolvedValue({
      task_id: "task-delete",
      task_type: "KNOWLEDGE_BASE_DELETE",
      status: "QUEUED",
      replayed: false,
    });
    vi.mocked(adminApi.listKnowledgeBases).mockResolvedValueOnce([{ ...kb, status: "DELETING" }]);

    await adminStore.deleteKnowledgeBase(kb);

    expect(ElMessageBox.prompt).toHaveBeenCalledWith(
      expect.stringContaining("文档、索引、Profile、会话与 Trace"),
      "删除知识库",
      expect.objectContaining({ inputPlaceholder: kb.name, confirmButtonText: "永久删除" }),
    );
    expect(remove).toHaveBeenCalledWith(kb.id);
    expect(adminStore.selectedKnowledgeId).toBe(kb.id);
    expect(adminStore.trackedTasks).toEqual([
      expect.objectContaining({ taskId: "task-delete", taskType: "KNOWLEDGE_BASE_DELETE" }),
    ]);
    expect(adminStore.notice).toBe("知识库删除任务已提交；完成后将弹出提醒");
  });

  it("renames a document without requesting an index rebuild", async () => {
    const document = {
      id: "doc-1",
      knowledge_id: kb.id,
      filename: "source.docx",
      display_name: "旧名称",
      file_size: 1024,
      status: "STORED",
      created_at: "2026-09-11T00:00:00Z",
      updated_at: "2026-09-11T00:00:00Z",
    };
    const updated = {
      ...document,
      display_name: "新名称",
      updated_at: "2026-09-11T01:00:00Z",
    };
    adminStore.documents = [document];
    adminStore.selectedDocument = document;
    vi.spyOn(ElMessageBox, "prompt").mockResolvedValue({
      value: "  新名称  ",
      action: "confirm",
    } as never);
    const rename = vi.spyOn(adminApi, "renameDocument").mockResolvedValue(updated);

    await adminStore.renameDocument(document);

    expect(rename).toHaveBeenCalledWith("doc-1", "新名称");
    expect(adminStore.documents).toEqual([updated]);
    expect(adminStore.selectedDocument).toEqual(updated);
    expect(adminStore.notice).toContain("无需重建索引");
  });

  it("notifies once when a tracked background task completes", async () => {
    stubApi();
    adminStore.selectedKnowledgeId = kb.id;
    adminStore.trackTask(
      { task_id: "task-done", task_type: "PROFILE_REFRESH", status: "QUEUED", replayed: false },
      { successMessage: "Profile 重建已完成", knowledgeId: kb.id },
    );
    vi.spyOn(adminApi, "getTask").mockResolvedValue({
      id: "task-done",
      task_type: "PROFILE_REFRESH",
      status: "SUCCEEDED",
      stage: "COMPLETED",
      progress: 100,
      attempt: 1,
      kb_id: kb.id,
      index_id: "idx-1",
      document_id: null,
      error_code: null,
      error_message: null,
      metadata: {},
      created_at: "2026-08-26T00:00:00Z",
      started_at: "2026-08-26T00:00:01Z",
      finished_at: "2026-08-26T00:00:02Z",
      updated_at: "2026-08-26T00:00:02Z",
    });
    await adminStore.pollTrackedTasks();

    expect(adminStore.trackedTasks).toEqual([]);
    expect(adminStore.notice).toBe("Profile 重建已完成");
  });

  it("warns when a RAG card batch only partially succeeds", async () => {
    stubApi();
    adminStore.selectedKnowledgeId = kb.id;
    adminStore.trackTask(
      {
        task_id: "task-card-partial",
        task_type: "RAG_DOCUMENT_CARD_GENERATE",
        status: "QUEUED",
        replayed: false,
      },
      { successMessage: "RAG 文档理解已生成", knowledgeId: kb.id },
    );
    vi.spyOn(adminApi, "getTask").mockResolvedValue({
      id: "task-card-partial",
      task_type: "RAG_DOCUMENT_CARD_GENERATE",
      status: "SUCCEEDED",
      stage: "COMPLETED",
      progress: 100,
      attempt: 1,
      kb_id: kb.id,
      index_id: "idx-1",
      document_id: null,
      error_code: null,
      error_message: null,
      metadata: { generated_count: 7, failed_count: 2, selected_count: 9 },
      created_at: "2026-09-13T00:00:00Z",
      started_at: "2026-09-13T00:00:01Z",
      finished_at: "2026-09-13T00:00:05Z",
      updated_at: "2026-09-13T00:00:05Z",
    });

    await adminStore.pollTrackedTasks();

    expect(adminStore.notice).toContain("成功 7 份，失败 2 份");
    expect(adminStore.trackedTasks).toEqual([]);
  });

  it("uses the active index for a full RAG card backfill", async () => {
    adminStore.knowledgeBases = [kb];
    adminStore.indexes = [index];
    adminStore.selectedKnowledgeId = kb.id;
    adminStore.documentInspectionIndexId = "idx-candidate";
    const generate = vi.spyOn(adminApi, "generateRagDocumentCards").mockResolvedValue({
      task_id: "task-card",
      task_type: "RAG_DOCUMENT_CARD_GENERATE",
      status: "QUEUED",
      replayed: false,
    });
    vi.spyOn(adminApi, "getTask").mockRejectedValue(new Error("eventual consistency"));

    await adminStore.generateRagDocumentCards();

    expect(generate).toHaveBeenCalledWith(kb.id, {
      index_id: "idx-1",
      document_id: undefined,
      replace_confirmed: false,
    });
    expect(adminStore.latestRagDocumentCardTasks["idx-1"]?.status).toBe("QUEUED");
  });

  it("synchronizes a running Profile task into the review progress state", async () => {
    stubApi();
    adminStore.selectedKnowledgeId = kb.id;
    adminStore.trackTask(
      { task_id: "task-profile-running", task_type: "PROFILE_REFRESH", status: "QUEUED", replayed: false },
      { successMessage: "Profile 重建已完成", knowledgeId: kb.id },
    );
    vi.spyOn(adminApi, "getTask").mockResolvedValue({
      id: "task-profile-running",
      task_type: "PROFILE_REFRESH",
      status: "RUNNING",
      stage: "GENERATING",
      progress: 55,
      attempt: 1,
      kb_id: kb.id,
      index_id: "idx-1",
      document_id: null,
      error_code: null,
      error_message: null,
      metadata: { generated_count: 3, document_count: 5 },
      created_at: "2026-08-26T00:00:00Z",
      started_at: "2026-08-26T00:00:01Z",
      finished_at: null,
      updated_at: "2026-08-26T00:00:02Z",
    });

    await adminStore.pollTrackedTasks();

    expect(adminStore.trackedTasks).toHaveLength(1);
    expect(adminStore.latestProfileTasks["idx-1"]).toMatchObject({
      status: "RUNNING",
      stage: "GENERATING",
      progress: 55,
      metadata: { generated_count: 3, document_count: 5 },
    });
  });

  it("shows a submitted Profile refresh immediately even before index-task listing catches up", async () => {
    stubApi();
    adminStore.selectedKnowledgeId = kb.id;
    adminStore.indexes = [index];
    vi.spyOn(adminApi, "refreshProfileDrafts").mockResolvedValue({
      task_id: "task-profile-submitted",
      task_type: "PROFILE_REFRESH",
      status: "QUEUED",
      replayed: false,
    });
    vi.spyOn(adminApi, "getTask").mockResolvedValue({
      id: "task-profile-submitted",
      task_type: "PROFILE_REFRESH",
      status: "RUNNING",
      stage: "GENERATING",
      progress: 5,
      attempt: 1,
      kb_id: kb.id,
      index_id: index.id,
      document_id: null,
      error_code: null,
      error_message: null,
      metadata: { generated_count: 0, document_count: 2 },
      created_at: "2026-08-26T00:00:00Z",
      started_at: "2026-08-26T00:00:01Z",
      finished_at: null,
      updated_at: "2026-08-26T00:00:02Z",
    });

    await adminStore.refreshProfileDrafts(index.id);

    expect(adminStore.latestProfileTasks[index.id]).toMatchObject({
      id: "task-profile-submitted",
      status: "RUNNING",
      progress: 5,
    });
  });

  it("uploads a document for the selected knowledge base", async () => {
    stubApi();
    adminStore.selectedKnowledgeId = "kb-1";
    const upload = vi.spyOn(adminApi, "uploadDocument").mockResolvedValue(undefined);
    adminStore.uploadDrawerOpen = true;
    await adminStore.upload(new File(["x"], "a.docx"));
    expect(upload).toHaveBeenCalledWith("kb-1", expect.any(File), expect.any(String));
    expect(adminStore.uploadDrawerOpen).toBe(false);
    expect(adminStore.notice).toBe(
      "文档已上传，RAG 候选索引构建已提交；构建完成后需明确设为当前索引，Profile 请在 Profile Review 审核",
    );
  });

  it("submits document-attribute generation from the knowledge entry", async () => {
    stubApi();
    adminStore.knowledgeBases = [kb];
    vi.spyOn(ElMessageBox, "confirm").mockResolvedValue({ action: "confirm" } as never);
    const generate = vi.spyOn(adminApi, "generateKnowledgeProfiles").mockResolvedValue({
      task_id: "task-rebuild",
      task_type: "DOCUMENT_PROFILE_GENERATE",
      status: "QUEUED",
      replayed: false,
    });

    await adminStore.regenerateKnowledgeProfiles(kb);

    expect(generate).toHaveBeenCalledWith(kb.id);
    expect(adminStore.trackedTasks).toEqual([
      expect.objectContaining({
        taskId: "task-rebuild",
        taskType: "DOCUMENT_PROFILE_GENERATE",
        knowledgeId: kb.id,
      }),
    ]);
    expect(adminStore.notice).toBe("全部待上线 Profile 属性生成任务已提交；完成后将弹出提醒");
  });

  it("skips document-attribute generation when the confirmation is dismissed", async () => {
    stubApi();
    vi.spyOn(ElMessageBox, "confirm").mockRejectedValue(new Error("cancel"));
    const generate = vi.spyOn(adminApi, "generateKnowledgeProfiles");

    await adminStore.regenerateKnowledgeProfiles(kb);

    expect(generate).not.toHaveBeenCalled();
    expect(adminStore.trackedTasks).toEqual([]);
  });

  it("keeps tracking the chained profile refresh after an orchestrated build", async () => {
    stubApi();
    adminStore.selectedKnowledgeId = kb.id;
    adminStore.trackTask(
      { task_id: "task-build", task_type: "INDEX_BUILD", status: "QUEUED", replayed: false },
      { successMessage: "候选索引构建完成", knowledgeId: kb.id },
    );
    vi.spyOn(adminApi, "getTask").mockResolvedValue({
      id: "task-build",
      task_type: "INDEX_BUILD",
      status: "SUCCEEDED",
      stage: "COMPLETED",
      progress: 100,
      attempt: 1,
      kb_id: kb.id,
      index_id: "idx-1",
      document_id: null,
      error_code: null,
      error_message: null,
      metadata: { profile_task_id: "task-refresh" },
      created_at: "2026-08-26T00:00:00Z",
      started_at: "2026-08-26T00:00:01Z",
      finished_at: "2026-08-26T00:00:02Z",
      updated_at: "2026-08-26T00:00:02Z",
    });

    await adminStore.pollTrackedTasks();

    expect(adminStore.trackedTasks).toEqual([
      expect.objectContaining({
        taskId: "task-refresh",
        taskType: "PROFILE_REFRESH",
        knowledgeId: kb.id,
      }),
    ]);
  });

  it("tracks the document Profile task submitted after an automatic RAG build", async () => {
    stubApi();
    adminStore.selectedKnowledgeId = kb.id;
    adminStore.trackTask(
      { task_id: "task-build", task_type: "INDEX_BUILD", status: "QUEUED", replayed: false },
      { successMessage: "RAG 索引构建完成", knowledgeId: kb.id },
    );
    vi.spyOn(adminApi, "getTask").mockResolvedValue({
      id: "task-build",
      task_type: "INDEX_BUILD",
      status: "SUCCEEDED",
      stage: "COMPLETED",
      progress: 100,
      attempt: 1,
      kb_id: kb.id,
      index_id: "idx-1",
      document_id: null,
      error_code: null,
      error_message: null,
      metadata: {
        profile_task_id: "task-document-profile",
        profile_task_type: "DOCUMENT_PROFILE_GENERATE",
      },
      created_at: "2026-08-26T00:00:00Z",
      started_at: "2026-08-26T00:00:01Z",
      finished_at: "2026-08-26T00:00:02Z",
      updated_at: "2026-08-26T00:00:02Z",
    });

    await adminStore.pollTrackedTasks();

    expect(adminStore.trackedTasks).toEqual([
      expect.objectContaining({
        taskId: "task-document-profile",
        taskType: "DOCUMENT_PROFILE_GENERATE",
        knowledgeId: kb.id,
      }),
    ]);
  });

  it("loads document profile context in parallel and drops stale responses", async () => {
    stubApi();
    adminStore.knowledgeBases = [kb];
    adminStore.indexes = [index];
    adminStore.selectedKnowledgeId = kb.id;
    vi.spyOn(adminApi, "listElements").mockResolvedValue({
      index_id: "idx-1",
      items: [{ id: 1, content: "el" }],
    });
    vi.spyOn(adminApi, "listChunks").mockResolvedValue({
      index_id: "idx-1",
      items: [{ id: 2, content: "chunk" }],
    });
    const getProfileContext = vi
      .spyOn(adminApi, "getDocumentProfileContext")
      .mockResolvedValue({
        document_id: "doc-1",
        published_profile: null,
        candidate_draft: null,
      });

    const document = {
      id: "doc-1",
      knowledge_id: "kb-1",
      filename: "manual.docx",
      display_name: "操作手册",
      file_size: 2048,
      status: "INDEXED",
      created_at: "2026-08-17T00:00:00Z",
      updated_at: "2026-08-17T00:00:00Z",
    };
    await adminStore.inspectDocument(document);

    expect(getProfileContext).toHaveBeenCalledWith("doc-1");
    expect(adminStore.documentProfileContext).toEqual({
      document_id: "doc-1",
      published_profile: null,
      candidate_draft: null,
    });

    // A slow response for the previous document must not leak onto a newly
    // opened document while the first inspection is still in flight.
    let resolveContext!: (value: {
      document_id: string;
      published_profile: null;
      candidate_draft: null;
    }) => void;
    getProfileContext.mockReturnValue(
      new Promise((resolve) => {
        resolveContext = resolve;
      }),
    );
    adminStore.documentProfileContext = null;
    const inspection = adminStore.loadDocumentInspection();
    adminStore.selectedDocument = { ...document, id: "doc-2" };
    resolveContext({ document_id: "doc-1", published_profile: null, candidate_draft: null });
    await inspection;
    expect(adminStore.documentProfileContext).toBeNull();
  });

  it("builds, activates, gc's and deletes indexes", async () => {
    stubApi();
    adminStore.selectedKnowledgeId = "kb-1";
    const build = vi.spyOn(adminApi, "buildIndex").mockResolvedValue({
      requested: true,
      coalesced: false,
      index_id: "idx-new",
      task_id: "task-new",
      rebuild_required: false,
    });
    const activate = vi.spyOn(adminApi, "activateIndex").mockResolvedValue(index);
    vi
      .spyOn(adminApi, "gcIndexes")
      .mockResolvedValue({ deleted_index_ids: ["old"], deleted_count: 1 });
    const del = vi
      .spyOn(adminApi, "deleteIndex")
      .mockResolvedValue({ deleted_index_ids: ["x"], deleted_count: 1 });

    await adminStore.buildIndex();
    expect(build).toHaveBeenCalledWith("kb-1");
    await adminStore.activateIndex("idx-1");
    expect(activate).toHaveBeenCalledWith("idx-1");
    await adminStore.gcIndexes();
    expect(adminStore.notice).toBe("已回收 1 个旧索引");
    vi.spyOn(ElMessageBox, "confirm").mockImplementation(async () => "confirm" as never);
    await adminStore.deleteIndex("idx-old");
    expect(del).toHaveBeenCalledWith("idx-old");
  });

  it("runs a playground query and stores the resulting trace", async () => {
    stubApi();
    adminStore.selectedKnowledgeId = "kb-1";
    adminStore.question = "如何配置数据库？";
    const run = vi.spyOn(adminApi, "runPlayground").mockResolvedValue(traceDetail);
    await adminStore.askPlayground();
    expect(run).toHaveBeenCalled();
    expect(adminStore.playgroundTrace?.answer).toBe("答案");
  });

  it("loads a trace silently for the split detail view", async () => {
    stubApi();
    const get = vi.spyOn(adminApi, "getTrace").mockResolvedValue(traceDetail);
    const result = await adminStore.openTraceSilent("t-1");
    expect(get).toHaveBeenCalledWith("t-1");
    expect(result.trace_id).toBe("t-1");
    expect(adminStore.traceDrawerOpen).toBe(false);
  });

  it("manages the service token in sessionStorage", () => {
    adminStore.saveToken("  tok  ");
    expect(sessionStorage.getItem("rag_service_token")).toBe("tok");
    adminStore.clearToken();
    expect(sessionStorage.getItem("rag_service_token")).toBeNull();
    expect(adminStore.notice).toBe("本地访问凭据已清除");
  });

  it("reads task metadata defensively", () => {
    expect(adminStore.taskMetadata(task, "current_document")).toBe("a.docx");
    expect(adminStore.taskMetadata(undefined, "x")).toBeUndefined();
  });

  it("derives support publication state and formats defensive values", () => {
    const supportIndex = { ...index, id: "idx-support" };
    const draftRelease = {
      id: "release-draft",
      index_id: supportIndex.id,
      status: "DRAFT",
      draft_count: 3,
      approved_count: 1,
    } as never;
    const publishedRelease = {
      id: "release-live",
      index_id: "idx-live",
      status: "PUBLISHED",
      draft_count: 2,
      approved_count: 2,
    } as never;
    adminStore.knowledgeBases = [
      {
        ...kb,
        support_active_index_id: supportIndex.id,
        support_active_release_id: "release-live",
      },
    ];
    adminStore.selectedKnowledgeId = kb.id;
    adminStore.indexes = [supportIndex];
    adminStore.releases = [draftRelease, publishedRelease];

    expect(adminStore.supportActiveIndex?.id).toBe(supportIndex.id);
    expect(adminStore.supportActiveRelease?.id).toBe("release-live");
    expect(adminStore.pendingSupportReviewCount).toBe(2);
    expect(adminStore.releaseForIndex(supportIndex.id)?.id).toBe("release-draft");
    expect(adminStore.isActionableReleaseIndex(supportIndex.id)).toBe(true);
    expect(adminStore.isActionableReleaseIndex("missing")).toBe(false);

    adminStore.documents = [
      { display_name: "数据库手册", filename: "db.docx" },
      { display_name: "部署说明", filename: "server.docx" },
    ] as never;
    adminStore.documentSearch = "server";
    expect(adminStore.filteredDocuments).toHaveLength(1);
    adminStore.documentSearch = "数据库";
    expect(adminStore.filteredDocuments).toHaveLength(1);
    adminStore.documentSearch = "";
    expect(adminStore.filteredDocuments).toHaveLength(2);
    expect(adminStore.formatDate(null)).toBe("—");
    expect(adminStore.formatDate("2026-09-11T00:00:00Z")).not.toBe("—");
    expect(adminStore.formatJson(undefined)).toBe("(empty)");
    expect(adminStore.taskMetadata({ ...task, metadata: { count: 3 } }, "count")).toBe(3);
    expect(adminStore.taskMetadata({ ...task, metadata: { nested: {} } }, "nested")).toBeUndefined();
    adminStore.navigate("logs", "tab=traces");
    expect(adminStore.requestedViewQuery).toBe("tab=traces");
    adminStore.actionLoading.save = true;
    expect(adminStore.isActionLoading("save")).toBe(true);
    expect(adminStore.isActionLoading("missing")).toBe(false);
  });

  it("runs document Profile publication operations", async () => {
    adminStore.selectedKnowledgeId = kb.id;
    const hydrate = vi.spyOn(adminStore, "hydrateTrackedTask").mockResolvedValue(undefined);
    const loadProfiles = vi.spyOn(adminStore, "loadKnowledgeProfiles").mockResolvedValue(undefined);
    vi.spyOn(adminApi, "generateDocumentProfile").mockResolvedValue({
      task_id: "task-doc-profile",
      task_type: "DOCUMENT_PROFILE_GENERATE",
      status: "QUEUED",
      replayed: false,
    });
    vi.spyOn(adminApi, "cancelTask").mockResolvedValue({
      ...task,
      task_type: "DOCUMENT_PROFILE_GENERATE",
      kb_id: kb.id,
      status: "CANCELLED",
    } as never);
    vi.spyOn(adminApi, "updateDocumentProfile").mockResolvedValue({} as never);
    vi.spyOn(adminApi, "checkKnowledgeProfiles").mockResolvedValue({ publishable: true } as never);
    vi.spyOn(adminApi, "publishKnowledgeProfiles").mockResolvedValue({
      published_count: 2,
      summary: { published_count: 2 },
    } as never);
    vi.spyOn(adminApi, "discardPendingKnowledgeProfiles").mockResolvedValue({
      discarded_count: 3,
    });

    await adminStore.generateDocumentProfile("doc-1");
    await adminStore.cancelDocumentProfileTask("task-doc-profile");
    await adminStore.updateDocumentProfile("doc-1", {} as never);
    expect(await adminStore.checkKnowledgeProfiles()).toEqual({ publishable: true });
    await adminStore.publishKnowledgeProfiles();
    expect(adminStore.notice).toBe("已上线 2 项 Profile 属性");
    await adminStore.discardPendingKnowledgeProfiles();
    expect(adminStore.notice).toBe("已取消 3 项待上线变更");
    expect(hydrate).toHaveBeenCalledWith("task-doc-profile");
    expect(loadProfiles).toHaveBeenCalled();
  });

  it("runs release validation, publishing, rebase, and rollback operations", async () => {
    adminStore.selectedKnowledgeId = kb.id;
    adminStore.releases = [{ id: "release-1", status: "DRAFT" }] as never;
    const release = { id: "release-1", status: "DRAFT" };
    vi.spyOn(adminApi, "updateProfileDraft").mockResolvedValue({
      release_id: "release-1",
    } as never);
    vi.spyOn(adminApi, "getKnowledgeRelease").mockResolvedValue(release as never);
    vi.spyOn(adminApi, "validateKnowledgeRelease")
      .mockResolvedValueOnce({ publishable: true } as never)
      .mockResolvedValueOnce({ publishable: false, review_required_count: 2 } as never)
      .mockResolvedValueOnce({
        publishable: false,
        review_required_count: 0,
        valid: true,
      } as never);
    vi.spyOn(adminApi, "publishKnowledgeRelease").mockResolvedValue({
      ...release,
      status: "PUBLISHED",
    } as never);
    vi.spyOn(adminApi, "rebaseKnowledgeRelease").mockResolvedValue({
      release_id: "release-1",
    } as never);
    vi.spyOn(adminApi, "rollbackSupportRelease").mockResolvedValue({} as never);
    const loadKnowledgeBases = vi
      .spyOn(adminStore, "loadKnowledgeBases")
      .mockResolvedValue(undefined);
    vi.spyOn(adminStore, "loadKnowledgeDetails").mockResolvedValue(undefined);

    await adminStore.updateProfileDraft("draft-1", { status: "APPROVED" } as never);
    expect(adminStore.notice).toContain("草稿已批准");
    await adminStore.validateKnowledgeRelease("release-1");
    expect(adminStore.notice).toContain("可以发布");
    await adminStore.validateKnowledgeRelease("release-1");
    expect(adminStore.notice).toContain("2 篇草稿待审核");
    await adminStore.validateKnowledgeRelease("release-1");
    expect(adminStore.notice).toContain("暂不可发布");
    await adminStore.publishKnowledgeRelease("release-1");
    expect(adminStore.releaseDetails["release-1"]?.status).toBe("PUBLISHED");
    expect(await adminStore.rebaseKnowledgeRelease("release-1")).toEqual({
      release_id: "release-1",
    });
    await adminStore.rollbackSupportRelease("release-1");
    expect(loadKnowledgeBases).toHaveBeenCalled();
  });

  it("loads and updates model and RAG configuration", async () => {
    const ragConfig = { vector_top_k: 12 } as never;
    vi.spyOn(adminApi, "getRagConfig").mockResolvedValue(ragConfig);
    vi.spyOn(adminApi, "updateRagConfig").mockResolvedValue(ragConfig);
    vi.spyOn(adminApi, "updateModel").mockResolvedValue({ rebuilds: ["kb-1"] } as never);
    vi.spyOn(adminApi, "listModels").mockResolvedValue([]);
    vi.spyOn(adminApi, "putApiKey").mockResolvedValue({} as never);
    vi.spyOn(adminApi, "deleteApiKey").mockResolvedValue({} as never);
    vi.spyOn(adminApi, "probeModel").mockResolvedValue({ success: true } as never);

    await adminStore.loadRagConfig();
    await adminStore.saveRagConfig({ vector_top_k: 12 });
    await adminStore.saveModelConfig("model-1", { rebuild_all_confirmed: true });
    expect(adminStore.notice).toContain("1 个知识库");
    await adminStore.putModelApiKey("model-1", "test-key");
    await adminStore.clearModelApiKey("model-1");
    expect(await adminStore.probeModel("model-1")).toEqual({ success: true });
  });

  it("short-circuits selected-knowledge operations and normalizes non-Error failures", async () => {
    adminStore.selectedKnowledgeId = "";
    adminStore.knowledgeBases = [];
    adminStore.indexes = [];
    adminStore.releases = [];

    expect(adminStore.supportActiveIndex).toBeNull();
    expect(adminStore.supportActiveRelease).toBeNull();
    expect(adminStore.documentInspectionIndexes).toEqual([]);
    await adminStore.generateKnowledgeProfiles();
    await adminStore.generateDocumentProfile("doc-1");
    await adminStore.updateDocumentProfile("doc-1", {} as never);
    expect(await adminStore.checkKnowledgeProfiles()).toBeNull();
    await adminStore.publishKnowledgeProfiles();
    await adminStore.discardPendingKnowledgeProfiles();
    await adminStore.buildIndex();
    await adminStore.gcIndexes();
    await adminStore.refreshOperationalState();

    await adminStore.run(async () => {
      throw "offline";
    });
    expect(adminStore.error).toBe("操作失败");
    await adminStore.runAction("save", async () => {
      throw "offline";
    });
    expect(adminStore.error).toBe("操作失败");
  });

  it("surfaces api errors instead of throwing", async () => {
    stubApi();
    vi.spyOn(adminApi, "buildIndex").mockRejectedValue(new Error("boom"));
    adminStore.selectedKnowledgeId = "kb-1";
    await adminStore.buildIndex();
    expect(adminStore.error).toBe("boom");
    expect(adminStore.loading).toBe(false);
  });

  it("renders index task pipeline in the index build view", async () => {
    stubApi();
    const wrapper = await mountStudio("indexes");
    expect(wrapper.text()).toContain("Index v3");
    const row = wrapper.get(".el-table__row");
    await row.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Pipeline");
    expect(wrapper.text()).toContain("分块");
    expect(wrapper.text()).toContain("Task Log");
  });
});

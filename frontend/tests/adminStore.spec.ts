import ElementPlus from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ElMessageBox } from "element-plus";

import { adminApi } from "@/api/admin";
import { adminStore } from "@/composables/adminStore";
import AdminStudio from "@/components/AdminStudio.vue";

const kb = {
  id: "kb-1",
  name: "产品库",
  description: null,
  status: "ENABLED",
  runtime_state: "READY",
  active_index_id: "idx-1",
  building_index_id: null,
  rebuild_required: false,
  document_count: 2,
  active_chunk_count: 10,
  created_at: "2026-08-17T00:00:00Z",
  updated_at: "2026-08-17T00:00:00Z",
};

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
    adminStore.knowledgeBases = [];
    adminStore.indexes = [];
    adminStore.latestIndexTasks = {};
    adminStore.selectedKnowledgeId = "";
    adminStore.notice = "";
    adminStore.error = "";
    adminStore.knowledgeSearch = "";
    adminStore.knowledgeStatusFilter = "";
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    sessionStorage.clear();
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
    expect(adminStore.notice).toBe("知识库已创建");
  });

  it("uploads a document for the selected knowledge base", async () => {
    stubApi();
    adminStore.selectedKnowledgeId = "kb-1";
    const upload = vi.spyOn(adminApi, "uploadDocument").mockResolvedValue(undefined);
    adminStore.uploadDrawerOpen = true;
    await adminStore.upload(new File(["x"], "a.docx"));
    expect(upload).toHaveBeenCalledWith("kb-1", expect.any(File));
    expect(adminStore.uploadDrawerOpen).toBe(false);
    expect(adminStore.notice).toBe("文档已上传，索引构建已提交");
  });

  it("builds, activates, gc's and deletes indexes", async () => {
    stubApi();
    adminStore.selectedKnowledgeId = "kb-1";
    const build = vi.spyOn(adminApi, "buildIndex").mockResolvedValue(undefined);
    const activate = vi.spyOn(adminApi, "activateIndex").mockResolvedValue(index);
    vi
      .spyOn(adminApi, "gcIndexes")
      .mockResolvedValue({ deleted_index_ids: ["old"], deleted_count: 1 });
    const del = vi
      .spyOn(adminApi, "deleteIndex")
      .mockResolvedValue({ deleted_index_ids: ["x"], deleted_count: 1 });

    await adminStore.buildIndex();
    expect(build).toHaveBeenCalledWith("kb-1", true);
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

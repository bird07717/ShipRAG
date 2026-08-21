import ElementPlus from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { adminApi } from "@/api/admin";
import { adminStore } from "@/composables/adminStore";
import AdminStudio from "@/components/AdminStudio.vue";

const kb = {
  id: "kb-1",
  name: "产品库",
  description: "desc",
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

const doc = {
  id: "doc-1",
  knowledge_id: "kb-1",
  filename: "manual.docx",
  display_name: "操作手册",
  file_size: 2048,
  status: "INDEXED",
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
  finished_at: "2026-08-17T00:01:00Z",
  activated_at: null,
};

const traceSummary = {
  trace_id: "t-1",
  mode: "PLAYGROUND" as const,
  kb_id: "kb-1",
  index_id: "idx-1",
  question: "如何配置数据库？",
  status: "SUCCESS",
  latency: { vector_retrieval_ms: 12, llm_ms: 300, total_ms: 400 },
  error: {},
  created_at: "2026-08-17T00:00:00Z",
};

const traceDetail = {
  ...traceSummary,
  retrieval_result: { hits: 2 },
  rerank_result: {},
  selected_context: [{ document: "a.docx", section_path: ["1"], content: "ctx", score: 0.9 }],
  prompt: "PROMPT",
  answer: "答案内容",
  sources: [{ source_id: "S1", document: "a.docx", section_path: ["1", "2"], page: null }],
  citation_result: {},
};

const model = {
  id: "m-1",
  name: "main-llm",
  model_type: "LLM",
  provider: "siliconflow",
  base_url: "https://api.siliconflow.cn/v1/",
  model_name: "qwen",
  enabled: true,
  api_key_configured: true,
  parameters: { temperature: 0.2 },
};

const prompt = { id: "p-1", name: "default", version: 2, content: "SYS PROMPT", active: true };

function stubApi() {
  vi.spyOn(adminApi, "listKnowledgeBases").mockResolvedValue([kb]);
  vi.spyOn(adminApi, "listDocuments").mockResolvedValue([doc]);
  vi.spyOn(adminApi, "listIndexes").mockResolvedValue([index]);
  vi.spyOn(adminApi, "listIndexTasks").mockResolvedValue([]);
  vi.spyOn(adminApi, "listTraces").mockResolvedValue([traceSummary]);
  vi.spyOn(adminApi, "listModels").mockResolvedValue([model]);
  vi.spyOn(adminApi, "listPrompts").mockResolvedValue([prompt]);
  vi.spyOn(adminApi, "getTrace").mockResolvedValue(traceDetail);
  vi.spyOn(adminApi, "runPlayground").mockResolvedValue(traceDetail);
  vi.spyOn(adminApi, "listElements").mockResolvedValue({
    index_id: "idx-1",
    items: [{ id: 1, element_type: "paragraph", content: "hello" }],
  });
  vi.spyOn(adminApi, "listChunks").mockResolvedValue({
    index_id: "idx-1",
    items: [{ id: 2, chunk_type: "text", content: "chunk" }],
  });
  vi.spyOn(adminApi, "buildIndex").mockResolvedValue(undefined);
}

async function mountView(view: string) {
  const wrapper = mount(AdminStudio, {
    props: { view },
    global: { plugins: [ElementPlus] },
  });
  await flushPromises();
  return wrapper;
}

describe("view interactions", () => {
  beforeEach(() => {
    stubApi();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    sessionStorage.clear();
  });

  it("knowledge: filters, opens detail drawer and rebuilds", async () => {
    const wrapper = await mountView("knowledge");
    expect(wrapper.text()).toContain("产品库");

    await wrapper.get(".el-table__row").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("知识库详情");
    expect(wrapper.text()).toContain("Active Index");

    adminStore.knowledgeSearch = "不存在";
    await flushPromises();
    expect(wrapper.text()).toContain("暂无知识库");
    adminStore.knowledgeSearch = "";
  });

  it("documents: searches, opens detail drawer with elements and chunks", async () => {
    const wrapper = await mountView("documents");
    expect(wrapper.text()).toContain("操作手册");

    await wrapper.get(".el-table__row").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Elements · 1");
    expect(wrapper.text()).toContain("Chunks · 1");

    adminStore.documentSearch = "nothing";
    await flushPromises();
    expect(wrapper.text()).toContain("暂无文档");
    adminStore.documentSearch = "";
  });

  it("playground: runs a query and renders answer, sources and context", async () => {
    const wrapper = await mountView("playground");
    const runButton = wrapper
      .findAll("button")
      .find((b) => b.text().includes("运行"));
    expect(runButton).toBeDefined();
    await runButton!.trigger("click");
    await flushPromises();

    expect(adminStore.playgroundTrace?.answer).toBe("答案内容");
    expect(wrapper.text()).toContain("答案内容");
    expect(wrapper.text()).toContain("Sources · 1");
    expect(wrapper.text()).toContain("a.docx");
    expect(wrapper.text()).toContain("Score 0.90");
  });

  it("traces: selects a trace and renders the latency timeline", async () => {
    const wrapper = await mountView("traces");
    expect(wrapper.text()).toContain("如何配置数据库？");

    await wrapper.get(".trace-row").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Timeline");
    expect(wrapper.text()).toContain("LLM Generation");
    expect(wrapper.text()).toContain("答案内容");
  });

  it("configuration: switches tabs and picks a prompt", async () => {
    const wrapper = await mountView("configuration");
    expect(wrapper.text()).toContain("qwen");
    expect(wrapper.text()).toContain("siliconflow");

    const promptTab = wrapper
      .findAll(".el-tabs__item")
      .find((tab) => tab.text().includes("Prompts"));
    await promptTab!.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("default");

    await wrapper.get(".prompt-row").trigger("click");
    expect(wrapper.text()).toContain("SYS PROMPT");
  });

  it("settings: switches section and clears the credential", async () => {
    sessionStorage.setItem("rag_service_token", "tok");
    const wrapper = await mountView("settings");
    const danger = wrapper
      .findAll(".settings-nav button")
      .find((b) => b.text().includes("危险操作"));
    await danger!.trigger("click");
    await flushPromises();

    const clear = wrapper
      .findAll("button")
      .find((b) => b.text().includes("清除凭据"));
    await clear!.trigger("click");
    await flushPromises();
    expect(sessionStorage.getItem("rag_service_token")).toBeNull();
    expect(adminStore.notice).toBe("本地访问凭据已清除");
  });

  it("dashboard: opens a trace drawer from recent activity", async () => {
    const wrapper = await mountView("dashboard");
    await wrapper.get(".el-table__row").trigger("click");
    await flushPromises();
    expect(adminStore.traceDrawerOpen).toBe(true);
    expect(wrapper.text()).toContain("RAG Trace");
    expect(wrapper.text()).toContain("答案内容");
  });
});

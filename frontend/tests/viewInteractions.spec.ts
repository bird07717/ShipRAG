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
  model_usage: {},
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

const failedTraceSummary = {
  trace_id: "t-fail",
  mode: "CHAT" as const,
  kb_id: "kb-1",
  index_id: "idx-1",
  conversation_id: "conv-1",
  question: "为什么升级失败？",
  status: "FAILED",
  user_id: "user-1",
  latency: { total_ms: 900 },
  error: { code: "INVALID_EVIDENCE", message: "模型引用了当前文档之外的证据" },
  created_at: "2026-08-17T00:00:00Z",
};

const failedTraceDetail = {
  ...failedTraceSummary,
  request_id: "req-1",
  retrieval_result: {
    support_version: 1,
    failure: { code: "INVALID_EVIDENCE" },
    focus_document_id: "doc-1",
    validation_result: { valid: false, code: "INVALID_EVIDENCE" },
    workflow_after: { current_step: 3, completed_steps: [1, 2] },
  },
  rerank_result: {},
  selected_context: [],
  prompt: null,
  answer: null,
  sources: [],
  citation_result: {},
  model_usage: {},
};

const conversationMessages = [
  {
    id: "m-1",
    conversation_id: "conv-1",
    role: "USER" as const,
    content: "为什么升级失败？",
    sources: [],
    status: "COMPLETED",
    content_blocks: [],
    response_type: null,
    support_mode: null,
    created_at: "2026-08-17T00:00:00Z",
    updated_at: "2026-08-17T00:00:00Z",
  },
  {
    id: "m-2",
    conversation_id: "conv-1",
    role: "ASSISTANT" as const,
    content: "请先检查第一步",
    sources: [],
    status: "COMPLETED",
    content_blocks: [],
    response_type: "DOCUMENT_DELIVERED",
    support_mode: "STEP_GUIDANCE",
    created_at: "2026-08-17T00:00:01Z",
    updated_at: "2026-08-17T00:00:01Z",
  },
];

const requestError = {
  id: "e-1",
  request_id: "req-1",
  method: "POST",
  path: "/api/v1/support/chat/stream",
  status_code: 502,
  error_code: "UPSTREAM_UNAVAILABLE",
  message: "上游暂时不可用",
  created_at: "2026-08-17T00:00:00Z",
};

const escalationTraceSummary = {
  ...failedTraceSummary,
  trace_id: "t-esc",
  question: "设备完全无法启动",
  status: "COMPLETED",
  response_type: "ESCALATION_REQUIRED",
  error: {},
};

const escalationTraceDetail = {
  ...escalationTraceSummary,
  retrieval_result: {
    support_version: 1,
    escalation: {
      required: true,
      reason: "多次重启后仍然无法进入系统",
      summary: {
        problem_code: "VDR600_BOOT_FAILURE",
        current_step: 4,
        completed_steps: [1, 2, 3],
        observations: [],
        checks_performed: [],
        missing_information: ["电源指示灯状态"],
      },
    },
  },
  rerank_result: {},
  selected_context: [],
  prompt: null,
  answer: null,
  sources: [],
  citation_result: {},
  model_usage: {},
};

const failedTask = {
  id: "task-1",
  status: "FAILED",
  stage: "embedding",
  progress: 40,
  attempt: 2,
  index_id: "idx-1",
  error_code: "EmbeddingError",
  error_message: "上游返回 429",
  metadata: { reason: "MANUAL" },
  created_at: "2026-08-17T00:00:00Z",
  updated_at: "2026-08-17T00:00:05Z",
};

const failureSummary = {
  from: "2026-08-14T00:00:00Z",
  to: "2026-08-21T00:00:00Z",
  scope: { trace_failures: "KNOWLEDGE_BASE" as const, request_errors: "GLOBAL" as const, knowledge_id: "kb-1" },
  trace_failures: [{ error_code: "INVALID_EVIDENCE", count: 2, last_seen: "2026-08-20T10:00:00Z" }],
  request_errors: [{ path: "/api/v1/support/chat/stream", status_code: 502, count: 3, last_seen: "2026-08-20T10:00:00Z" }],
  request_errors_by_day: [{ day: "2026-08-20", count: 3 }],
};

function stubApi() {
  vi.spyOn(adminApi, "listKnowledgeBases").mockResolvedValue([kb]);
  vi.spyOn(adminApi, "listDocuments").mockResolvedValue([doc]);
  vi.spyOn(adminApi, "listIndexes").mockResolvedValue([index]);
  vi.spyOn(adminApi, "listIndexTasks").mockResolvedValue([]);
  vi.spyOn(adminApi, "listTraces").mockResolvedValue([traceSummary]);
  vi.spyOn(adminApi, "listModels").mockResolvedValue([model]);
  vi.spyOn(adminApi, "listPrompts").mockResolvedValue([prompt]);
  vi.spyOn(adminApi, "getRagConfig").mockResolvedValue({
    vector_top_k: 10,
    bm25_top_k: 10,
    fusion_top_k: 20,
    rerank_top_n: 10,
    context_max_chunks: 8,
  });
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

async function mountView(view: string, navigationQuery = "") {
  const wrapper = mount(AdminStudio, {
    props: { view, navigationQuery },
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
    // shared drawers live in the module-level store and would leak into the
    // next test's mount (triggering real fetches from the replay drawer)
    adminStore.conversationDrawerOpen = false;
    adminStore.conversationId = "";
    adminStore.traceDrawerOpen = false;
    adminStore.selectedTrace = null;
  });

  it("knowledge: filters, opens detail drawer and rebuilds", async () => {
    const wrapper = await mountView("knowledge");
    expect(wrapper.text()).toContain("产品库");

    await wrapper.get(".el-table__row").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("知识库详情");
    expect(wrapper.text()).toContain("Active Index");
    expect(wrapper.text()).toContain("删除知识库");

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
    expect(adminApi.listElements).toHaveBeenLastCalledWith("doc-1", "idx-1");
    expect(adminApi.listChunks).toHaveBeenLastCalledWith("doc-1", "idx-1");
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

  it("logs: opens full traces and renders the latency timeline", async () => {
    vi.spyOn(adminApi, "getFailureSummary").mockResolvedValue(failureSummary);
    vi.spyOn(adminApi, "listRequestErrors").mockResolvedValue({
      items: [],
      total: 0,
      limit: 20,
      offset: 0,
    });
    const wrapper = await mountView("logs");
    const tracesTab = wrapper
      .findAll(".el-tabs__item")
      .find((tab) => tab.text().includes("全量 Trace"));
    await tracesTab!.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("如何配置数据库？");

    await wrapper.get(".trace-row").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Timeline");
    expect(wrapper.text()).toContain("LLM Generation");
    expect(wrapper.text()).toContain("答案内容");
  });

  it("logs: applies the retired RAG errors deep-link state", async () => {
    vi.spyOn(adminApi, "getFailureSummary").mockResolvedValue(failureSummary);
    const listTraces = vi.mocked(adminApi.listTraces);
    const wrapper = await mountView("logs", "tab=failed-turns&engine=RAG");

    expect(wrapper.find(".el-tabs__item.is-active").text()).toContain("失败轮次");
    const engineSelect = wrapper
      .findAllComponents({ name: "ElSelect" })
      .find((select) => select.props("placeholder") === "全部引擎");
    expect(engineSelect?.props("modelValue")).toBe("RAG");
    expect(listTraces).toHaveBeenCalledWith("kb-1", {
      status: "FAILED",
      engine: "RAG",
      errorCode: undefined,
      limit: 200,
    });
  });

  it("logs: reviews request errors, failed turns and opens conversation replay", async () => {
    vi.spyOn(adminApi, "getFailureSummary").mockResolvedValue(failureSummary);
    vi.spyOn(adminApi, "listRequestErrors").mockResolvedValue({
      items: [requestError],
      total: 1,
      limit: 20,
      offset: 0,
    });
    vi.spyOn(adminApi, "getRequestError").mockResolvedValue({
      ...requestError,
      detail: { exception_type: "LlmError", stack: "Traceback (most recent call last)" },
    });
    vi.spyOn(adminApi, "listTasks").mockResolvedValue([]);
    const listTraces = vi.spyOn(adminApi, "listTraces").mockResolvedValue([failedTraceSummary]);
    vi.spyOn(adminApi, "getTrace").mockResolvedValue(failedTraceDetail);
    vi.spyOn(adminApi, "listConversationMessages").mockResolvedValue(conversationMessages);

    const wrapper = await mountView("logs");
    // overview: failure distribution from the summary endpoint
    expect(wrapper.text()).toContain("失败概览");
    expect(wrapper.text()).toContain("INVALID_EVIDENCE");

    // request errors tab: row click opens the detail drawer with the stack
    const errorRow = wrapper
      .findAll(".el-table__row")
      .find((row) => row.text().includes("上游暂时不可用"));
    expect(errorRow).toBeDefined();
    await errorRow!.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("请求错误详情");
    expect(wrapper.text()).toContain("Traceback (most recent call last)");

    // failed turns tab: failed support turn with the full error payload
    const failedTab = wrapper
      .findAll(".el-tabs__item")
      .find((tab) => tab.text().includes("失败轮次"));
    await failedTab!.trigger("click");
    await flushPromises();
    const engineSelect = wrapper
      .findAllComponents({ name: "ElSelect" })
      .find((select) => select.props("placeholder") === "全部引擎");
    await engineSelect!.setValue("SUPPORT");
    engineSelect!.vm.$emit("change", "SUPPORT");
    await flushPromises();
    expect(listTraces).toHaveBeenCalledWith("kb-1", {
      status: "FAILED",
      engine: "SUPPORT",
      errorCode: undefined,
      limit: 200,
    });
    const failedRow = wrapper
      .findAll(".el-table__row")
      .find((row) => row.text().includes("为什么升级失败"));
    expect(failedRow).toBeDefined();
    await failedRow!.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("失败轮次详情");
    expect(wrapper.text()).toContain("模型引用了当前文档之外的证据");

    // conversation replay: shared drawer renders the multi-turn timeline
    const replayButton = wrapper
      .findAll("button")
      .find((button) => button.text().includes("查看会话回放"));
    expect(replayButton).toBeDefined();
    await replayButton!.trigger("click");
    await flushPromises();
    expect(adminStore.conversationDrawerOpen).toBe(true);
    expect(wrapper.text()).toContain("会话回放");
    expect(wrapper.text()).toContain("请先检查第一步");
    expect(wrapper.text()).toContain("当前步骤 3");
  });

  it("logs: reviews failed tasks and escalation records", async () => {
    vi.spyOn(adminApi, "getFailureSummary").mockResolvedValue(failureSummary);
    vi.spyOn(adminApi, "listRequestErrors").mockResolvedValue({
      items: [],
      total: 0,
      limit: 20,
      offset: 0,
    });
    vi.spyOn(adminApi, "listTasks").mockResolvedValue([failedTask]);
    vi.spyOn(adminApi, "listTraces").mockResolvedValue([escalationTraceSummary]);
    vi.spyOn(adminApi, "getTrace").mockResolvedValue(escalationTraceDetail);

    const wrapper = await mountView("logs");

    // request-error filter reset keeps working from any tab
    const resetButton = wrapper
      .findAll("button")
      .find((button) => button.text().includes("重置"));
    await resetButton!.trigger("click");
    await flushPromises();

    // failed tasks tab: global task list with build failures
    const tasksTab = wrapper
      .findAll(".el-tabs__item")
      .find((tab) => tab.text().includes("任务失败"));
    await tasksTab!.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("EmbeddingError");
    const taskRow = wrapper
      .findAll(".el-table__row")
      .find((row) => row.text().includes("上游返回 429"));
    expect(taskRow).toBeDefined();
    await taskRow!.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("任务失败详情");
    expect(wrapper.text()).toContain("MANUAL");

    // escalations tab: read-only escalation records with the handover summary
    const escalationTab = wrapper
      .findAll(".el-tabs__item")
      .find((tab) => tab.text().includes("升级记录"));
    await escalationTab!.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("设备完全无法启动");
    const escalationRow = wrapper
      .findAll(".el-table__row")
      .find((row) => row.text().includes("设备完全无法启动"));
    await escalationRow!.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("升级详情");
    expect(wrapper.text()).toContain("VDR600_BOOT_FAILURE");
    expect(wrapper.text()).toContain("电源指示灯状态");
    expect(wrapper.text()).toContain("多次重启后仍然无法进入系统");
  });

  it("configuration: switches tabs and picks a prompt", async () => {
    const wrapper = await mountView("configuration");
    expect(wrapper.text()).toContain("qwen");
    expect(wrapper.text()).toContain("siliconflow");
    expect(wrapper.text()).toContain("RAG 检索参数");
    expect(wrapper.text()).toContain("服务凭证");

    const promptTab = wrapper
      .findAll(".el-tabs__item")
      .find((tab) => tab.text().includes("Prompts"));
    await promptTab!.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("default");

    await wrapper.get(".prompt-row").trigger("click");
    expect(wrapper.text()).toContain("SYS PROMPT");

    const ragTab = wrapper
      .findAll(".el-tabs__item")
      .find((tab) => tab.text().includes("RAG 检索参数"));
    await ragTab!.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Generic RAG 全局配置");
  });

  it("configuration: opens service credentials and clears the credential", async () => {
    sessionStorage.setItem("rag_service_token", "tok");
    const wrapper = await mountView("configuration", "tab=credentials");
    expect(wrapper.find(".el-tabs__item.is-active").text()).toContain("服务凭证");
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

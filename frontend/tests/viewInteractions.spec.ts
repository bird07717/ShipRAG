import ElementPlus from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { adminApi } from "@/api/admin";
import { adminStore } from "@/composables/adminStore";
import type {
  DocumentProfileContext,
  KnowledgeRelease,
  ProfileDraft,
  RagDocumentCard,
} from "@/types/admin";
import AdminStudio from "@/components/AdminStudio.vue";

const kb = {
  id: "kb-1",
  name: "产品库",
  description: "desc",
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

const candidateIndex = {
  ...index,
  id: "idx-2",
  version: 4,
  status: "READY",
  activate_on_success: false,
  build_reason: "DOCUMENT_CHANGED",
  activated_at: null,
};

const profileDraft: ProfileDraft = {
  id: "draft-1",
  release_id: "rel-1",
  kb_id: "kb-1",
  document_id: "doc-1",
  document_name: "操作手册",
  operation: "UPDATE",
  target_profile_id: "pp-1",
  target_problem_code: "PDC_UPGRADE",
  problem_code: "PDC_UPGRADE",
  canonical_question: "如何升级 PDC？",
  aliases: ["PDC 怎么升级"],
  keywords: ["升级"],
  product_name: "PDC",
  model_names: ["VDR600"],
  task_type: "UPGRADE",
  routing_summary: "解决 PDC 升级问题",
  symptoms: ["升级失败"],
  exclusions: [],
  profile_enrichment_version: 1,
  enabled: true,
  status: "DRAFT",
  generation_source: "LLM",
  quality_status: "PASS",
  quality_notes: [],
  quality_evidence: [],
  confidence: 0.91,
  reasoning: "文档主题明确",
  validation_result: {},
  created_at: "2026-08-17T00:00:00Z",
  updated_at: "2026-08-17T00:00:00Z",
};

const supportRelease: KnowledgeRelease = {
  id: "rel-1",
  kb_id: "kb-1",
  index_id: "idx-2",
  index_version: 4,
  index_status: "READY",
  status: "DRAFT",
  validation_result: { publishable: false, review_required_count: 1 },
  generation_error: null,
  draft_count: 1,
  approved_count: 0,
  drafts: [profileDraft],
  created_at: "2026-08-17T00:00:00Z",
  updated_at: "2026-08-17T00:00:00Z",
  published_at: null,
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
  supported_providers: ["zhipu", "siliconflow", "dashscope"],
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

const profileContext: DocumentProfileContext = {
  document_id: "doc-1",
  published_profile: {
    id: "pp-1",
    problem_code: "PDC_UPGRADE",
    canonical_question: "如何升级 PDC？",
    aliases: ["PDC 怎么升级"],
    keywords: ["升级"],
    product_name: "PDC",
    model_names: ["VDR600"],
    task_type: "UPGRADE",
    routing_summary: "解决 PDC 升级问题",
    symptoms: [],
    exclusions: [],
    enabled: true,
    profile_enrichment_version: 1,
    updated_at: "2026-08-17T00:00:00Z",
  },
  candidate_draft: {
    id: "draft-1",
    release_id: "rel-1",
    index_id: "idx-2",
    index_version: 4,
    release_status: "DRAFT",
    status: "DRAFT",
    operation: "UPDATE",
    target_profile_id: "pp-1",
    problem_code: "PDC_UPGRADE",
    canonical_question: "如何升级 PDC？",
    aliases: [],
    keywords: ["升级"],
    product_name: "PDC",
    model_names: ["VDR600"],
    task_type: "UPGRADE",
    routing_summary: "候选路由摘要",
    enabled: true,
    quality_status: "REVIEW_REQUIRED",
    quality_notes: ["需人工核对"],
    generation_source: "LLM",
    updated_at: "2026-08-17T00:00:00Z",
  },
};

const documentProfileSummary = {
  knowledge_id: "kb-1",
  online_count: 1,
  pending_count: 1,
  attention_count: 0,
  failed_count: 0,
  generating_count: 0,
  profiles: [
    {
      document_id: "doc-1",
      document_name: "操作手册",
      document_status: "STORED",
      profile_status: "PENDING_ONLINE" as const,
      active_profile: { intents: [{ problem_code: "PDC_UPGRADE", canonical_question: "如何升级 PDC？", aliases: [], keywords: [], routing_summary: "PDC 升级" }] },
      pending_profile: { intents: [{ problem_code: "PDC_UPGRADE", canonical_question: "如何升级 PDC？", aliases: ["PDC 怎么升级"], keywords: ["升级"], routing_summary: "PDC 升级" }] },
      pending_state: "PENDING_ONLINE",
      pending_error_code: null,
      pending_error_message: null,
      active_updated_at: null,
      pending_updated_at: null,
    },
  ],
};

const ragDocumentCard: RagDocumentCard = {
  id: "card-1", knowledge_id: "kb-1", index_id: "idx-1", index_version: 3,
  document_id: "doc-1", document_name: "操作手册", schema_version: 1,
  document_type: "PROCEDURE",
  card_payload: {
    document_type: "PROCEDURE", summary: "配置 VAU 网络图像的完整操作",
    components: ["VAU"], tasks: ["配置 VAU 图像"],
    applicable_questions: ["如何配置 VAU 图像"], prerequisites: ["电脑与设备网络可用"],
    key_topics: ["电脑 IP", "图像通道"], exclusions: ["线缆故障排查"],
    has_procedure: true, has_images: true,
  },
  generation_status: "READY", review_status: "AUTO", revision: 1,
  content_fingerprint: "a".repeat(64), generator_snapshot: { provider: "fake" },
  error_code: null, error_message: null,
  created_at: "2026-08-17T00:00:00Z", updated_at: "2026-08-17T00:00:00Z",
};

const ragDocumentCardList = {
  knowledge_id: "kb-1", index_id: "idx-1", index_version: 3, cards: [ragDocumentCard],
  readiness: {
    knowledge_id: "kb-1", index_id: "idx-1", expected_count: 1, ready_count: 1,
    confirmed_count: 0, auto_count: 1, rejected_count: 0, missing_count: 0,
    failed_count: 0, stale_count: 0, shadow_ready: true, ready: false,
    blockers: ["待人工确认 1 份"],
  },
};

function stubApi() {
  vi.spyOn(adminApi, "listKnowledgeBases").mockResolvedValue([kb]);
  vi.spyOn(adminApi, "listDocuments").mockResolvedValue([doc]);
  vi.spyOn(adminApi, "listIndexes").mockResolvedValue([index, candidateIndex]);
  vi.spyOn(adminApi, "listIndexTasks").mockResolvedValue([]);
  vi.spyOn(adminApi, "listKnowledgeReleases").mockResolvedValue([supportRelease]);
  vi.spyOn(adminApi, "getKnowledgeRelease").mockResolvedValue(supportRelease);
  vi.spyOn(adminApi, "updateProfileDraft").mockImplementation(async (_draftId, patch) => ({
    ...profileDraft,
    ...patch,
  }));
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
  vi.spyOn(adminApi, "listEngineRetrievalConfigs").mockResolvedValue([
    "SUPPORT_WITH_RAG_FALLBACK",
    "RAG_ONLY",
  ].map((execution_policy) => ({
    execution_policy: execution_policy as "SUPPORT_WITH_RAG_FALLBACK" | "RAG_ONLY",
    vector_top_k: 10,
    bm25_top_k: 10,
    fusion_top_k: 20,
    rerank_top_n: 10,
    context_max_chunks: 8,
  })));
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
  vi.spyOn(adminApi, "buildIndex").mockResolvedValue({
    requested: true,
    coalesced: false,
    index_id: "idx-2",
    task_id: "task-2",
    rebuild_required: false,
  });
  vi.spyOn(adminApi, "getDocumentProfileContext").mockResolvedValue(profileContext);
  vi.spyOn(adminApi, "getKnowledgeProfiles").mockResolvedValue(documentProfileSummary);
  vi.spyOn(adminApi, "listRagDocumentCards").mockResolvedValue(ragDocumentCardList);
  vi.spyOn(adminApi, "getRagDocumentCard").mockResolvedValue(ragDocumentCard);
  vi.spyOn(adminApi, "getRagDocumentCardReadiness").mockResolvedValue(ragDocumentCardList.readiness);
  vi.spyOn(adminApi, "reviewRagDocumentCard").mockResolvedValue({
    ...ragDocumentCard, review_status: "CONFIRMED", revision: 2,
  });
  vi.spyOn(adminApi, "updateRagDocumentCard").mockResolvedValue({ ...ragDocumentCard, revision: 2 });
  vi.spyOn(adminApi, "generateRagDocumentCards").mockResolvedValue({
    task_id: "task-card", task_type: "RAG_DOCUMENT_CARD_GENERATE",
    status: "QUEUED", replayed: false,
  });
  vi.spyOn(adminApi, "checkKnowledgeProfiles").mockResolvedValue({
    valid: true, publishable: true, pending_count: 1, errors: [], warnings: [],
  });
  vi.spyOn(adminApi, "generateKnowledgeProfiles").mockResolvedValue({
    task_id: "task-profile", task_type: "DOCUMENT_PROFILE_GENERATE", status: "QUEUED", replayed: false,
  });
  vi.spyOn(adminApi, "generateDocumentProfile").mockResolvedValue({
    task_id: "task-profile-doc", task_type: "DOCUMENT_PROFILE_GENERATE", status: "QUEUED", replayed: false,
  });
  vi.spyOn(adminApi, "updateDocumentProfile").mockResolvedValue({
    ...profileContext, profile_status: "PENDING_ONLINE", active_profile: null,
    pending_profile: documentProfileSummary.profiles[0]!.pending_profile,
  });
  vi.spyOn(adminApi, "publishKnowledgeProfiles").mockResolvedValue({
    published_count: 1,
    check: { valid: true, publishable: true, pending_count: 1, errors: [], warnings: [] },
    summary: { ...documentProfileSummary, pending_count: 0, online_count: 2 },
  });
  vi.spyOn(adminApi, "discardPendingKnowledgeProfiles").mockResolvedValue({ discarded_count: 1 });
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
    sessionStorage.setItem("rag_service_token", "test-service-token");
    adminStore.serviceToken = "test-service-token";
    adminStore.credentialRejected = false;
    adminStore.latestRagDocumentCardTasks = {};
    stubApi();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    sessionStorage.clear();
    adminStore.serviceToken = "";
    adminStore.credentialRejected = false;
    // shared drawers live in the module-level store and would leak into the
    // next test's mount (triggering real fetches from the replay drawer)
    adminStore.conversationDrawerOpen = false;
    adminStore.conversationId = "";
    adminStore.traceDrawerOpen = false;
    adminStore.selectedTrace = null;
    adminStore.documentDrawerOpen = false;
    adminStore.selectedDocument = null;
    adminStore.documentProfileContext = null;
    adminStore.ragDocumentCard = null;
    adminStore.ragDocumentCards = [];
    adminStore.ragDocumentCardReadiness = null;
    adminStore.latestRagDocumentCardTasks = {};
    adminStore.requestedIndexDetailId = "";
    adminStore.requestedView = "";
    adminStore.requestedViewQuery = "";
    adminStore.trackedTasks = [];
    adminStore.notice = "";
    adminStore.error = "";
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
    expect(adminApi.getDocumentProfileContext).toHaveBeenCalledWith("doc-1");
    expect(wrapper.text()).toContain("元素（Elements）· 1");
    expect(wrapper.text()).toContain("切片（Chunks）· 1");

    adminStore.documentSearch = "nothing";
    await flushPromises();
    expect(wrapper.text()).toContain("暂无文档");
    adminStore.documentSearch = "";
  });

  it("documents: displays live RAG card generation progress and counts", async () => {
    vi.mocked(adminApi.listIndexTasks).mockImplementation(async (indexId) =>
      indexId === "idx-1"
        ? [{
            id: "task-card-running",
            task_type: "RAG_DOCUMENT_CARD_GENERATE",
            status: "RUNNING",
            stage: "GENERATING_CARDS",
            progress: 46,
            attempt: 1,
            index_id: "idx-1",
            error_code: null,
            error_message: null,
            metadata: { generated_count: 8, failed_count: 2, selected_count: 42 },
            created_at: "2026-09-13T00:00:00Z",
            updated_at: "2026-09-13T00:00:03Z",
          }]
        : [],
    );

    const wrapper = await mountView("documents");
    await flushPromises();

    const progress = wrapper.get(".rag-card-task-progress");
    expect(progress.text()).toContain("正在生成");
    expect(progress.text()).toContain("46%");
    expect(progress.text()).toContain("成功 8");
    expect(progress.text()).toContain("失败 2");
    expect(progress.text()).toContain("共 42 份");
  });

  it("documents: Profile & Alias tab separates published profile from candidate draft", async () => {
    const wrapper = await mountView("documents");
    await wrapper.get(".el-table__row").trigger("click");
    await flushPromises();
    await wrapper
      .findAll(".el-tabs__item")
      .find((tab) => tab.text().includes("Profile 与别名"))!
      .trigger("click");
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain("已发布 Profile");
    expect(text).not.toContain("PDC_UPGRADE");
    expect(text).toContain("PDC 怎么升级");
    expect(text).toContain("已发布 · 影响线上技术支持（Support）");
    expect(text).toContain("候选草稿 · 未发布");
    expect(text).toContain("不影响线上 Support");
    expect(text).toContain("v4");
    expect(text).toContain("REVIEW_REQUIRED");
    expect(text).toContain("空别名（Alias；无用户改写）");
    expect(text).toContain("前往候选审核");

    await wrapper
      .findAll("button")
      .find((b) => b.text().includes("前往候选审核"))!
      .trigger("click");
    await flushPromises();
    expect(adminStore.requestedIndexDetailId).toBe("idx-2");
    expect(adminStore.requestedView).toBe("support-review");
    expect(adminStore.documentDrawerOpen).toBe(false);
  });

  it("documents: edits and confirms an index-bound RAG document card", async () => {
    const wrapper = await mountView("documents");
    await wrapper.get(".el-table__row").trigger("click");
    await flushPromises();
    expect(adminApi.getRagDocumentCard).toHaveBeenCalledWith("kb-1", "doc-1", "idx-1");
    await wrapper.findAll(".el-tabs__item")
      .find((tab) => tab.text().includes("RAG 文档理解"))!.trigger("click");
    await flushPromises();
    expect(wrapper.findAll("textarea").some((field) =>
      field.element.value.includes("配置 VAU 网络图像的完整操作"),
    )).toBe(true);
    const confirm = wrapper.findAll("button").find((button) => button.text().trim() === "确认");
    expect(confirm).toBeTruthy();
    await confirm!.trigger("click");
    await flushPromises();
    expect(adminApi.reviewRagDocumentCard).toHaveBeenCalledWith(
      "kb-1", "doc-1", "idx-1", 1, "CONFIRMED",
    );
  });

  async function pickFiles(wrapper: ReturnType<typeof mount>, files: File[]) {
    const input = wrapper.find('input[type="file"]');
    Object.defineProperty(input.element, "files", { value: files, configurable: true });
    await input.trigger("change");
    await flushPromises();
  }

  function uploadButton(wrapper: ReturnType<typeof mount>) {
    return wrapper.findAll("button").find((button) => button.text().includes("开始上传"));
  }

  it("documents: upload failure keeps the failed item in the queue", async () => {
    const wrapper = await mountView("documents");
    adminStore.uploadDrawerOpen = true;
    await flushPromises();

    await pickFiles(wrapper, [
      new File(["stub"], "manual.docx", { type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" }),
    ]);
    expect(wrapper.text()).toContain("manual.docx");

    const build = vi.spyOn(adminApi, "buildIndex");
    vi.spyOn(adminApi, "uploadDocument").mockRejectedValue(new Error("上传通道异常"));
    await uploadButton(wrapper)!.trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("上传通道异常");
    expect(wrapper.text()).toContain("已上传 0 份，失败 1 份");
    expect(wrapper.find(".upload-item--failed").exists()).toBe(true);
    expect(adminStore.uploadDrawerOpen).toBe(true);
    expect(build).not.toHaveBeenCalled();
  });

  it("documents: upload success marks the item done and triggers one build", async () => {
    const wrapper = await mountView("documents");
    adminStore.uploadDrawerOpen = true;
    await flushPromises();

    await pickFiles(wrapper, [new File(["stub"], "manual.pdf", { type: "application/pdf" })]);
    const upload = vi.spyOn(adminApi, "uploadDocument").mockResolvedValue({} as never);
    const build = vi.spyOn(adminApi, "buildIndex").mockResolvedValue({
      requested: true, coalesced: false, index_id: "idx-2", task_id: "task-2", rebuild_required: false,
    });
    await uploadButton(wrapper)!.trigger("click");
    await flushPromises();

    expect(upload).toHaveBeenCalledWith(
      "kb-1", expect.any(File), expect.stringMatching(/^[0-9a-f-]{36}$/), false,
    );
    expect(build).toHaveBeenCalledTimes(1);
    expect(wrapper.text()).toContain("已上传 1 份文档");
    expect(wrapper.find(".upload-item--done").exists()).toBe(true);
  });

  it("documents: batches multiple files, isolates failures and builds once", async () => {
    const wrapper = await mountView("documents");
    adminStore.uploadDrawerOpen = true;
    await flushPromises();

    await pickFiles(wrapper, [
      new File(["good"], "good.docx"),
      new File(["bad"], "bad.docx"),
    ]);
    const upload = vi.spyOn(adminApi, "uploadDocument").mockImplementation(
      async (_kb: string, file: File) => {
        if (file.name === "bad.docx") throw new Error("源文档哈希已存在");
        return {} as never;
      },
    );
    const build = vi.spyOn(adminApi, "buildIndex").mockResolvedValue({
      requested: true, coalesced: false, index_id: "idx-2", task_id: "task-2", rebuild_required: false,
    });
    expect(uploadButton(wrapper)!.text()).toContain("2");
    await uploadButton(wrapper)!.trigger("click");
    await flushPromises();

    expect(upload).toHaveBeenCalledTimes(2);
    expect(upload).toHaveBeenLastCalledWith("kb-1", expect.any(File), expect.any(String), false);
    expect(build).toHaveBeenCalledTimes(1);
    expect(wrapper.text()).toContain("已上传 1 份，失败 1 份");
    expect(wrapper.text()).toContain("源文档哈希已存在");
  });

  it("documents: rejects unsupported and oversized files before upload", async () => {
    const wrapper = await mountView("documents");
    adminStore.uploadDrawerOpen = true;
    await flushPromises();

    const oversized = new File(["stub"], "huge.docx");
    Object.defineProperty(oversized, "size", { value: 52_428_801 });
    const duplicate = new File(["stub"], "virus.exe");
    await pickFiles(wrapper, [
      new File(["stub"], "virus.exe"),
      oversized,
      duplicate,
      duplicate,
    ]);
    await flushPromises();

    const upload = vi.spyOn(adminApi, "uploadDocument");
    expect(wrapper.text()).toContain("virus.exe");
    expect(wrapper.text()).toContain("仅支持 .docx / .pdf 文件");
    expect(wrapper.text()).toContain("huge.docx");
    expect(wrapper.text()).toContain("文件超过大小上限");
    expect(wrapper.text()).toContain("上传队列（2）");
    expect(uploadButton(wrapper)!.attributes("disabled")).toBeDefined();
    expect(upload).not.toHaveBeenCalled();
  });

  it("support review: manages document attributes in one workspace", async () => {
    const wrapper = await mountView("support-review");
    await flushPromises();

    expect(wrapper.text()).toContain("Profile 仅保留标准问题、别名（Alias）和路由描述");
    expect(wrapper.text()).toContain("操作手册");
    expect(wrapper.text()).toContain("如何升级 PDC？");
    expect(wrapper.text()).toContain("待上线");
    expect(wrapper.text()).toContain("RAG 始终可用");
    expect(wrapper.findAll("button").some((button) => button.attributes("aria-label") === "编辑属性")).toBe(true);
  });

  it("support review: checks then publishes pending attributes", async () => {
    const { ElMessageBox } = await import("element-plus");
    vi.spyOn(ElMessageBox, "confirm").mockResolvedValue({ action: "confirm" } as never);
    const wrapper = await mountView("support-review");
    await flushPromises();

    const publish = wrapper.findAll("button").find((button) => button.text().includes("上线 Profile 变更"));
    expect(publish).toBeDefined();
    await publish!.trigger("click");
    await flushPromises();
    expect(adminApi.checkKnowledgeProfiles).toHaveBeenCalledWith("kb-1");
    expect(adminApi.publishKnowledgeProfiles).toHaveBeenCalledWith("kb-1");
  });

  it("support review: submits a visible all-document regeneration task", async () => {
    const wrapper = await mountView("support-review");
    await flushPromises();
    const regenerate = wrapper.findAll("button").find((button) => button.text().includes("重新生成全部"));
    expect(regenerate).toBeDefined();
    await regenerate!.trigger("click");
    await flushPromises();
    expect(adminApi.generateKnowledgeProfiles).toHaveBeenCalledWith("kb-1");
    expect(wrapper.text()).toContain("当前生成任务");
  });

  it("knowledge: detail drawer regenerates pending attributes behind confirmation", async () => {
    const { ElMessageBox } = await import("element-plus");
    const confirm = vi.spyOn(ElMessageBox, "confirm").mockResolvedValue({
      action: "confirm",
    } as never);
    const wrapper = await mountView("knowledge");
    await wrapper.get(".el-table__row").trigger("click");
    await flushPromises();

    const button = wrapper.findAll("button").find((b) => b.text().includes("重新生成待上线属性"));
    expect(button).toBeDefined();
    await button!.trigger("click");
    await flushPromises();

    expect(confirm).toHaveBeenCalledWith(
      expect.stringContaining("不会直接修改线上 Profile"),
      "重新生成待上线属性",
      expect.objectContaining({ confirmButtonText: "提交生成任务" }),
    );
    expect(adminApi.generateKnowledgeProfiles).toHaveBeenCalledWith("kb-1");
    expect(adminStore.trackedTasks).toEqual([
      expect.objectContaining({ taskId: "task-profile", taskType: "DOCUMENT_PROFILE_GENERATE" }),
    ]);
    expect(adminStore.notice).toBe("全部待上线 Profile 属性生成任务已提交；完成后将弹出提醒");
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
      .find((tab) => tab.text().includes("全部链路追踪"));
    await tracesTab!.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("如何配置数据库？");

    await wrapper.get(".trace-row").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("时间线（Timeline）");
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
    vi.spyOn(adminApi, "listConversationMessages").mockResolvedValue({ items: conversationMessages });

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
    expect(wrapper.text()).toContain("RAG only 检索配置");
  });

  it("configuration: offers DashScope and applies its default endpoint", async () => {
    const wrapper = await mountView("configuration");
    await wrapper.get('button[aria-label="编辑模型配置"]').trigger("click");
    await flushPromises();

    await wrapper.get(".el-select").trigger("click");
    await flushPromises();
    // Element Plus teleports dropdowns to body; earlier dialogs may leave
    // detached copies behind, so always click the last rendered match.
    const dashscopeOption = Array.from(document.body.querySelectorAll(".el-select-dropdown__item"))
      .filter((item) => item.textContent?.includes("千问"))
      .pop();
    expect(dashscopeOption).toBeDefined();
    (dashscopeOption as HTMLElement).click();
    await flushPromises();

    const inputs = wrapper.findAll("input");
    expect(
      inputs.some(
        (input) =>
          input.element.value === "https://dashscope.aliyuncs.com/compatible-mode/v1/",
      ),
    ).toBe(true);
  });

  it("configuration: non-LLM model types also offer DashScope", async () => {
    const rerankModel = {
      ...model,
      id: "m-2",
      name: "main-rerank",
      model_type: "RERANK",
      provider: "siliconflow",
      model_name: "Qwen/Qwen3-VL-Reranker-8B",
    };
    vi.mocked(adminApi.listModels).mockResolvedValue([rerankModel]);
    const wrapper = await mountView("configuration");
    await wrapper.get('button[aria-label="编辑模型配置"]').trigger("click");
    await flushPromises();

    await wrapper.get(".el-select").trigger("click");
    await flushPromises();
    const dashscopeOption = Array.from(document.body.querySelectorAll(".el-select-dropdown__item"))
      .filter((item) => item.textContent?.includes("千问"))
      .pop();
    expect(dashscopeOption).toBeDefined();
    (dashscopeOption as HTMLElement).click();
    await flushPromises();

    const inputs = wrapper.findAll("input");
    expect(
      inputs.some(
        (input) =>
          input.element.value === "https://dashscope.aliyuncs.com/compatible-mode/v1/",
      ),
    ).toBe(true);
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
    expect(wrapper.text()).toContain("RAG 链路追踪（Trace）");
    expect(wrapper.text()).toContain("答案内容");
  });
});

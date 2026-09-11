import ElementPlus from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { adminStore } from "@/composables/adminStore";
import AdminStudio from "@/components/AdminStudio.vue";
import DemoChat from "@/components/DemoChat.vue";
import App from "@/App.vue";

const kb = {
  id: "kb-1",
  name: "产品库",
  description: "d",
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
  file_size: 1024,
  status: "PARSING",
  created_at: "2026-08-17T00:00:00Z",
  updated_at: "2026-08-17T00:00:00Z",
};

const index = {
  id: "idx-1",
  kb_id: "kb-1",
  version: 3,
  status: "BUILDING",
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
  finished_at: null,
  activated_at: null,
};

const trace = {
  trace_id: "t-1",
  mode: "PLAYGROUND",
  kb_id: "kb-1",
  index_id: "idx-1",
  question: "如何配置数据库？",
  status: "SUCCESS",
  latency: { vector_retrieval_ms: 12, llm_ms: 300, total_ms: 400 },
  error: {},
  created_at: "2026-08-17T00:00:00Z",
  retrieval_result: {},
  rerank_result: {},
  selected_context: [
    { document: "a.docx", section_path: ["1", "2"], content: "chunk text", score: 0.87 },
  ],
  prompt: "PROMPT",
  answer: "答案",
  sources: [{ source_id: "S1", document: "a.docx", section_path: ["1"], page: null }],
  citation_result: {},
};

const model = {
  id: "m-1",
  name: "main-llm",
  model_type: "LLM",
  provider: "siliconflow",
  model_name: "qwen",
  enabled: true,
  api_key_configured: true,
  parameters: { temperature: 0.2 },
};

const prompt = { id: "p-1", name: "default", version: 2, content: "SYS", active: true };

describe("view smoke", () => {
  beforeEach(() => {
    sessionStorage.setItem("rag_service_token", "test-service-token");
    adminStore.serviceToken = "test-service-token";
    adminStore.credentialRejected = false;
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    sessionStorage.clear();
    adminStore.serviceToken = "";
    adminStore.credentialRejected = false;
  });

  it("mounts every admin view without errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        let body: unknown = [];
        const path = String(url);
        if (path.includes("/releases")) body = [];
        else if (path.includes("/knowledge-bases")) body = [kb];
        else if (path.includes("/metrics/overview"))
          body = {
            range: { from: "2026-08-14T00:00:00", to: "2026-08-21T00:00:00", mode: "CHAT" },
            total_traces: 10,
            completed: 9,
            failed: 1,
            cancelled: 0,
            response_type_counts: { ANSWERED: 6, UNCONFIRMED: 2, OUT_OF_SCOPE: 1, DOC_DELIVERED: 1 },
            classified_traces: 10,
            no_answer_rate: 0.3,
            doc_delivered_rate: 0.1,
            failure_rate: 0.1,
            citation_missing_rate: 0,
            rerank_degraded_rate: 0,
            unique_users: 3,
            latency: { p50_ms: 100, p95_ms: 200, p99_ms: 300, avg_ms: 120 },
            tokens: { llm_prompt: 100, llm_completion: 50, llm_total: 150, rerank_total: 10 },
          };
        else if (path.includes("/metrics/")) body = [];
        else if (path.includes("/documents")) body = [doc];
        else if (path.includes("/indexes")) body = [index];
        else if (path.includes("/tasks")) body = [];
        else if (path.includes("/traces")) body = [trace];
        else if (path.includes("/models")) body = [model];
        else if (path.includes("/prompts")) body = [prompt];
        else if (path.includes("/runtime-policies/"))
          body = {
            knowledge_id: "kb-1",
            execution_policy: "SUPPORT_WITH_RAG_FALLBACK",
            config_version: 1,
            fallback_conditions: ["NO_MATCH"],
            source: "DATABASE",
            readiness: {
              support: { ready: true, blockers: [], profile_count: 1, ready_profile_count: 1 },
              rag: { ready: true, blockers: [] },
            },
          };
        else if (path.includes("/engine-retrieval-configs"))
          body = [
            "SUPPORT_WITH_RAG_FALLBACK",
            "RAG_ONLY",
          ].map((execution_policy) => ({
            execution_policy,
            vector_top_k: 10,
            bm25_top_k: 10,
            fusion_top_k: 20,
            rerank_top_n: 10,
            context_max_chunks: 8,
          }));
        else if (path.includes("/rag-config"))
          body = {
            vector_top_k: 10,
            bm25_top_k: 10,
            fusion_top_k: 20,
            rerank_top_n: 10,
            context_max_chunks: 8,
          };
        else if (path.includes("/chat/welcome"))
          body = { knowledge_base: { id: "kb-1", name: "产品库", ready: true }, message: "hi", suggestions: ["Q1"] };
        else if (path.includes("/logs/request-errors"))
          body = { items: [], total: 0, limit: 20, offset: 0 };
        else if (path.includes("/logs/failure-summary"))
          body = {
            from: "2026-08-14T00:00:00Z",
            to: "2026-08-21T00:00:00Z",
            scope: { trace_failures: "GLOBAL", request_errors: "GLOBAL", knowledge_id: null },
            trace_failures: [],
            request_errors: [],
            request_errors_by_day: [],
          };
        else if (path.includes("/health")) body = { status: "ready", checks: {} };
        return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
      }),
    );

    for (const view of [
      "dashboard",
      "knowledge",
      "documents",
      "indexes",
      "logs",
      "playground",
      "configuration",
      "engine-routing",
      "support-review",
    ]) {
      const wrapper = mount(AdminStudio, {
        props: { view },
        global: { plugins: [ElementPlus] },
      });
      await flushPromises();
      expect(wrapper.html().length).toBeGreaterThan(100);
      wrapper.unmount();
    }
    expect(adminStore.knowledgeBases.length).toBe(1);
  });

  it("mounts App with demo chat and full ElementPlus", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        const path = String(url);
        let body: unknown = [];
        if (path.includes("/knowledge-bases")) body = [kb];
        else if (path.includes("/chat/welcome"))
          body = { knowledge_base: { id: "kb-1", name: "产品库", ready: true }, message: "你好", suggestions: ["问题一"] };
        else if (path.includes("/health"))
          body = { status: "ready", checks: {} };
        return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
      }),
    );
    const wrapper = mount(App, { global: { plugins: [ElementPlus] } });
    await flushPromises();
    expect(wrapper.text()).toContain("技术支持工作台");
    wrapper.unmount();
  });

  it("mounts DemoChat hero state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        const path = String(url);
        let body: unknown = [];
        if (path.includes("/knowledge-bases")) body = [kb];
        else if (path.includes("/chat/welcome"))
          body = { knowledge_base: { id: "kb-1", name: "产品库", ready: true }, message: "你好", suggestions: ["问题一", "问题二"] };
        return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
      }),
    );
    const wrapper = mount(DemoChat, { global: { plugins: [ElementPlus] } });
    await flushPromises();
    await flushPromises();
    expect(wrapper.text()).toContain("测试统一问答接口");
    expect(wrapper.text()).toContain("问题一");
    wrapper.unmount();
  });
});

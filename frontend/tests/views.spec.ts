import ElementPlus from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import { adminStore } from "@/composables/adminStore";
import AdminStudio from "@/components/AdminStudio.vue";
import DemoChat from "@/components/DemoChat.vue";
import App from "@/App.vue";

const kb = {
  id: "kb-1",
  name: "产品库",
  description: "d",
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
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("mounts every admin view without errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        let body: unknown = [];
        const path = String(url);
        if (path.includes("/knowledge-bases")) body = [kb];
        else if (path.includes("/documents")) body = [doc];
        else if (path.includes("/indexes")) body = [index];
        else if (path.includes("/tasks")) body = [];
        else if (path.includes("/traces")) body = [trace];
        else if (path.includes("/models")) body = [model];
        else if (path.includes("/prompts")) body = [prompt];
        else if (path.includes("/chat/welcome"))
          body = { knowledge_base: { id: "kb-1", name: "产品库", ready: true }, message: "hi", suggestions: ["Q1"] };
        else if (path.includes("/health")) body = { status: "ready", checks: {} };
        return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
      }),
    );

    for (const view of [
      "dashboard",
      "knowledge",
      "documents",
      "indexes",
      "playground",
      "traces",
      "configuration",
      "settings",
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
    expect(wrapper.text()).toContain("RAG Studio");
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
    expect(wrapper.text()).toContain("Ask your knowledge base");
    expect(wrapper.text()).toContain("问题一");
    wrapper.unmount();
  });
});

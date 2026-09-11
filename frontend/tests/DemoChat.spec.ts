import ElementPlus from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import { adminApi } from "@/api/admin";
import DemoChat from "@/components/DemoChat.vue";
import { adminStore } from "@/composables/adminStore";

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
  document_count: 1,
  active_chunk_count: 5,
  created_at: "2026-08-17T00:00:00Z",
  updated_at: "2026-08-17T00:00:00Z",
};
const secondKb = {
  ...kb,
  id: "kb-2",
  name: "第二产品库",
  active_index_id: "idx-2",
};

function streamResponse(frames: string[]): Response {
  const encoder = new TextEncoder();
  let index = 0;
  return {
    ok: true,
    body: {
      getReader: () => ({
        read: () =>
          index < frames.length
            ? Promise.resolve({ done: false, value: encoder.encode(frames[index++]) })
            : Promise.resolve({ done: true, value: undefined }),
      }),
    },
  } as unknown as Response;
}

const doneFrame = {
  conversation_id: "c-1",
  message_id: "m-1",
  answer: "第一段",
  response_type: "ANSWERED",
  answer_mode: "PRODUCT_KNOWLEDGE",
  execution_engine: "SUPPORT",
  disclaimer: null,
  content: [{ type: "text", text: "第一段" }],
  references: [
    {
      document_id: "doc-1",
      title: "manual.docx",
      section_paths: [["1", "2"]],
      source_ids: ["S1"],
      download_url: "/x",
    },
  ],
  usage: {},
  latency_ms: 1200,
};

async function mountChat(fetchMock: ReturnType<typeof vi.fn>) {
  vi.stubGlobal("fetch", fetchMock);
  const wrapper = mount(DemoChat, { global: { plugins: [ElementPlus] } });
  await flushPromises();
  await flushPromises();
  return wrapper;
}

const welcomeBody = {
  knowledge_base: { id: "kb-1", name: "产品库", ready: true },
  message: "你好，我是文档助手",
  suggestions: ["如何配置数据库？"],
};

describe("DemoChat", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    sessionStorage.clear();
    adminStore.knowledgeBases = [];
    adminStore.selectedKnowledgeId = "";
  });

  it("shows centered hero with welcome message and suggestions", async () => {
    const wrapper = await mountChat(
      vi.fn().mockImplementation((url: string) => {
        const path = String(url);
        let body: unknown = [];
        if (path.includes("/knowledge-bases")) body = [kb];
        else if (path.includes("/chat/welcome")) body = welcomeBody;
        return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
      }),
    );

    expect(wrapper.text()).toContain("测试统一问答接口");
    expect(wrapper.text()).toContain("你好，我是文档助手");
    expect(wrapper.text()).toContain("如何配置数据库？");
    expect(wrapper.findAll(".el-select")).toHaveLength(1);
    expect(wrapper.text()).not.toContain("SUPPORT_ONLY");
    expect(wrapper.text()).not.toContain("RAG_ONLY");
  });

  it("sends a question, renders streamed answer, sources and latency", async () => {
    const wrapper = await mountChat(
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        const path = String(url);
        if (path.includes("/knowledge-bases")) {
          return Promise.resolve({ ok: true, json: () => Promise.resolve([kb]) });
        }
        if (path.includes("/chat/welcome")) {
          return Promise.resolve({ ok: true, json: () => Promise.resolve(welcomeBody) });
        }
        if (path.includes("/chat/stream")) {
          expect(init?.method).toBe("POST");
          return Promise.resolve(
            streamResponse([
              'event: trace\ndata: {"trace_id":"t-1","conversation_id":"c-1"}\n\n',
              'event: message\ndata: {"delta":"第一"}\n\n',
              `event: done\ndata: ${JSON.stringify(doneFrame)}\n\n`,
            ]),
          );
        }
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }),
    );

    await wrapper.get("textarea").setValue("如何配置数据库？");
    await wrapper.get("textarea").trigger("keydown", { key: "Enter" });
    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("如何配置数据库？");
    expect(wrapper.text()).toContain("Support 助手");
    expect(wrapper.text()).toContain("第一段");
    expect(wrapper.text()).toContain("Sources · 1");
    expect(wrapper.text()).toContain("manual.docx");
    expect(wrapper.text()).toContain("1.2s");
    expect(wrapper.text()).not.toContain("Ask your knowledge base");
  });

  it("clicking a suggested question sends it directly", async () => {
    const wrapper = await mountChat(
      vi.fn().mockImplementation((url: string) => {
        const path = String(url);
        if (path.includes("/knowledge-bases")) {
          return Promise.resolve({ ok: true, json: () => Promise.resolve([kb]) });
        }
        if (path.includes("/chat/welcome")) {
          return Promise.resolve({ ok: true, json: () => Promise.resolve(welcomeBody) });
        }
        if (path.includes("/chat/stream")) {
          return Promise.resolve(
            streamResponse([`event: done\ndata: ${JSON.stringify(doneFrame)}\n\n`]),
          );
        }
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }),
    );

    await wrapper.get(".suggestion-chip").trigger("click");
    await flushPromises();
    await flushPromises();
    expect(wrapper.text()).toContain("如何配置数据库？");
    expect(wrapper.text()).toContain("第一段");
  });

  it("shows the actual engine and automatic fallback reason", async () => {
    const fallbackDone = {
      ...doneFrame,
      answer: "由 RAG 返回的答案",
      content: [],
      execution_engine: "RAG",
      fallback: { from: "SUPPORT", reason: "NO_MATCH", support_trace_id: "support-t-1" },
    };
    const wrapper = await mountChat(
      vi.fn().mockImplementation((url: string) => {
        const path = String(url);
        if (path.includes("/knowledge-bases")) {
          return Promise.resolve({ ok: true, json: () => Promise.resolve([kb]) });
        }
        if (path.includes("/chat/welcome")) {
          return Promise.resolve({ ok: true, json: () => Promise.resolve(welcomeBody) });
        }
        if (path.includes("/chat/stream")) {
          return Promise.resolve(
            streamResponse([
              'event: status\ndata: {"stage":"gateway_routing","engine":"SUPPORT"}\n\n',
              'event: fallback\ndata: {"from":"SUPPORT","to":"RAG","reason":"NO_MATCH"}\n\n',
              `event: done\ndata: ${JSON.stringify(fallbackDone)}\n\n`,
            ]),
          );
        }
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }),
    );

    await wrapper.get("textarea").setValue("未知问题");
    await wrapper.get("textarea").trigger("keydown", { key: "Enter" });
    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("RAG Engine");
    expect(wrapper.text()).toContain("由 RAG 返回的答案");
    expect(wrapper.text()).toContain("自动降级：SUPPORT → RAG");
    expect(wrapper.text()).toContain("Support 未定位到可靠文档，本轮由 RAG 兜底（NO_MATCH）");
  });

  it("starts a new conversation and sends the newly selected knowledge base", async () => {
    const chatBodies: Array<Record<string, unknown>> = [];
    const wrapper = await mountChat(
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        const path = String(url);
        if (path.includes("/knowledge-bases")) {
          return Promise.resolve({ ok: true, json: () => Promise.resolve([kb, secondKb]) });
        }
        if (path.includes("/chat/welcome")) {
          const selected = path.includes("kb-2") ? secondKb : kb;
          return Promise.resolve({
            ok: true,
            json: () =>
              Promise.resolve({
                knowledge_base: { id: selected.id, name: selected.name, ready: true },
                message: `${selected.name}欢迎词`,
                suggestions: [],
              }),
          });
        }
        if (path.includes("/chat/stream")) {
          chatBodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
          return Promise.resolve(
            streamResponse([
              `event: done\ndata: ${JSON.stringify({
                ...doneFrame,
                conversation_id: "kb-2-conversation",
                execution_engine: "SUPPORT",
              })}\n\n`,
            ]),
          );
        }
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }),
    );

    wrapper.findComponent({ name: "ElSelect" }).vm.$emit("update:modelValue", "kb-2");
    await flushPromises();
    await wrapper.get("textarea").setValue("第二知识库的问题");
    await wrapper.get("textarea").trigger("keydown", { key: "Enter" });
    await flushPromises();
    await flushPromises();

    expect(chatBodies).toEqual([
      {
        knowledge_id: "kb-2",
        conversation_id: null,
        question: "第二知识库的问题",
      },
    ]);
    expect(wrapper.text()).toContain("SUPPORT Engine");
  });

  it("clears the conversation with the new-chat action", async () => {
    vi.spyOn(adminApi, "listKnowledgeBases").mockResolvedValue([kb]);
    const wrapper = await mountChat(
      vi.fn().mockImplementation((url: string) => {
        if (String(url).includes("/chat/welcome")) {
          return Promise.resolve({ ok: true, json: () => Promise.resolve(welcomeBody) });
        }
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }),
    );

    const buttons = wrapper.findAll("button").filter((b) => b.text() === "新会话");
    expect(buttons).toHaveLength(1);
    await buttons[0]!.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("测试统一问答接口");
  });
});

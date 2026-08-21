import ElementPlus from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import { adminApi } from "@/api/admin";
import DemoChat from "@/components/DemoChat.vue";

const kb = {
  id: "kb-1",
  name: "产品库",
  description: null,
  status: "ENABLED",
  runtime_state: "READY",
  active_index_id: "idx-1",
  building_index_id: null,
  rebuild_required: false,
  document_count: 1,
  active_chunk_count: 5,
  created_at: "2026-08-17T00:00:00Z",
  updated_at: "2026-08-17T00:00:00Z",
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

    expect(wrapper.text()).toContain("Ask your knowledge base");
    expect(wrapper.text()).toContain("你好，我是文档助手");
    expect(wrapper.text()).toContain("如何配置数据库？");
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
    expect(wrapper.text()).toContain("RAG Assistant");
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
    expect(wrapper.text()).toContain("Ask your knowledge base");
  });
});

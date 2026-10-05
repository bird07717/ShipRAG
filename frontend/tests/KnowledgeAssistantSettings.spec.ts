import ElementPlus, { ElMessageBox } from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { adminApi, ApiRequestError } from "@/api/admin";
import * as chat from "@/api/chat";
import KnowledgeAssistantSettings from "@/components/KnowledgeAssistantSettings.vue";
import type { KnowledgeAssistantCheck } from "@/types/admin";
import { adminStore } from "@/composables/adminStore";

const questions = ["如何检查设备电源", "如何查看系统状态", "如何执行设备自检"];
const configuration = (id = "kb-a"): KnowledgeAssistantCheck => ({
  knowledge_id: id, assistant_online: false, suggestions: [...questions], revision: 0,
  ready: true, blockers: [],
});
const wrappers: ReturnType<typeof mount>[] = [];
function editor(id = "kb-a") {
  const wrapper = mount(KnowledgeAssistantSettings, {
    props: { knowledgeId: id, knowledgeName: id }, global: { plugins: [ElementPlus] },
  });
  wrappers.push(wrapper);
  return wrapper;
}
function button(wrapper: ReturnType<typeof editor>, label: string) {
  const found = wrapper.findAll("button").find((item) => item.text() === label);
  if (!found) throw new Error(`Missing button: ${label}`);
  return found;
}

describe("knowledge Assistant settings", () => {
  it("shows coded runtime blockers and navigates with the bound knowledge base", async () => {
    vi.spyOn(adminStore, "selectKnowledge").mockResolvedValue();
    const navigate = vi.spyOn(adminStore, "navigate").mockImplementation(() => {});
    vi.mocked(adminApi.getKnowledgeAssistant).mockResolvedValue({ ...configuration("kb-a"), ready: false, blockers: ["文案可以变化"], checks: [{ code: "UNDERSTANDING", title: "文档理解已全部确认", status: "BLOCKED", messages: ["文案可以变化"], action: { view: "documents", query: "", label: "检查文档理解" } }] });
    adminStore.selectedKnowledgeId = "kb-b";
    const wrapper = editor(); await flushPromises();
    expect(wrapper.find('[aria-label="知识库上线检查清单"]').exists()).toBe(true);
    expect(wrapper.text()).toContain("文档理解已全部确认");
    expect(wrapper.text()).toContain("不是新增上线门禁");
    await button(wrapper, "检查文档理解").trigger("click"); await flushPromises();
    expect(adminStore.selectedKnowledgeId).toBe("kb-a");
    expect(navigate).toHaveBeenCalledWith("documents", "");
  });
  beforeEach(() => {
    adminStore.serviceToken = "test-service-token";
    adminStore.credentialRejected = false;
    vi.stubGlobal("URL", Object.assign(URL, { revokeObjectURL: vi.fn() }));
    vi.spyOn(adminApi, "getKnowledgeAssistant").mockImplementation(async (id) => configuration(id));
    vi.spyOn(ElMessageBox, "confirm").mockResolvedValue({ action: "confirm" } as never);
  });
  afterEach(() => {
    wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    adminStore.serviceToken = "";
    adminStore.credentialRejected = false;
    adminStore.selectedKnowledgeId = "";
  });

  it.each(["unmounted", "credential-rejected", "credential-replaced", "knowledge-changed"])(
    "does not navigate a pending checklist action after %s",
    async (reason) => {
      let finish!: () => void;
      vi.spyOn(adminStore, "selectKnowledge").mockImplementation(() => new Promise<void>((resolve) => { finish = resolve; }));
      const navigate = vi.spyOn(adminStore, "navigate").mockImplementation(() => {});
      vi.mocked(adminApi.getKnowledgeAssistant).mockResolvedValue({
        ...configuration(), checks: [{ code: "UNDERSTANDING", title: "文档理解", status: "BLOCKED", messages: [], action: { view: "documents", query: "", label: "检查文档理解" } }],
      });
      adminStore.selectedKnowledgeId = "kb-b";
      const wrapper = editor();
      await flushPromises();
      await button(wrapper, "检查文档理解").trigger("click");
      expect(adminStore.selectedKnowledgeId).toBe("kb-a");
      if (reason === "unmounted") wrapper.unmount();
      if (reason === "credential-rejected") adminStore.credentialRejected = true;
      if (reason === "credential-replaced") adminStore.serviceToken = "replacement-test-token";
      if (reason === "knowledge-changed") adminStore.selectedKnowledgeId = "kb-c";
      finish();
      await flushPromises();
      expect(navigate).not.toHaveBeenCalled();
    },
  );

  it("saves to the bound KB and publishes the saved revision", async () => {
    const changed = ["如何重启设备系统", ...questions.slice(1)];
    const save = vi.spyOn(adminApi, "saveKnowledgeAssistant").mockResolvedValue({ ...configuration(), suggestions: changed, revision: 1 });
    const publish = vi.spyOn(adminApi, "setKnowledgeAssistantPublication").mockResolvedValue({ ...configuration(), suggestions: changed, revision: 2, assistant_online: true });
    const wrapper = editor();
    await flushPromises();
    await wrapper.findAll("input")[0]!.setValue(changed[0]);
    expect(button(wrapper, "确认上线").attributes("disabled")).toBeDefined();
    await button(wrapper, "保存问题").trigger("click");
    await flushPromises();
    expect(save).toHaveBeenCalledWith("kb-a", 0, changed);
    await button(wrapper, "确认上线").trigger("click");
    await flushPromises();
    expect(publish).toHaveBeenCalledWith("kb-a", 1, true);
    expect(wrapper.text()).toContain("知识库已对外上线");
    expect(wrapper.emitted("changed")?.at(-1)?.[0]).toMatchObject({ knowledge_id: "kb-a", assistant_online: true });
    expect(wrapper.text()).toContain("RAG 文档理解须全部人工确认");
  });

  it("blocks incomplete and duplicate questions but permits offline without runtime readiness", async () => {
    vi.mocked(adminApi.getKnowledgeAssistant).mockResolvedValue({ ...configuration(), assistant_online: true, ready: false, blockers: ["模型不可用"] });
    const publish = vi.spyOn(adminApi, "setKnowledgeAssistantPublication").mockResolvedValue({ ...configuration(), revision: 1 });
    const wrapper = editor();
    await flushPromises();
    await wrapper.findAll("input")[0]!.setValue("");
    expect(button(wrapper, "保存问题").attributes("disabled")).toBeDefined();
    await wrapper.findAll("input")[0]!.setValue(questions[1] + "？");
    expect(wrapper.text()).toContain("推荐问题不能重复");
    await button(wrapper, "下线").trigger("click");
    await flushPromises();
    expect(publish).toHaveBeenCalledWith("kb-a", 0, false);
    expect(wrapper.findAll("input")[0]!.element.value).toBe(questions[1] + "？");
  });

  it("shows conflict without claiming success or losing edits", async () => {
    vi.spyOn(adminApi, "saveKnowledgeAssistant").mockRejectedValue(new ApiRequestError("配置已被修改，请重新加载后再操作", 409));
    const wrapper = editor();
    await flushPromises();
    await wrapper.findAll("input")[0]!.setValue("如何重启设备系统");
    await button(wrapper, "保存问题").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("配置已被修改");
    expect(wrapper.findAll("input")[0]!.element.value).toBe("如何重启设备系统");
    expect(wrapper.text()).not.toContain("已保存");
  });

  it("ignores a previous KB load after its editor is closed", async () => {
    let resolveOld!: (value: KnowledgeAssistantCheck) => void;
    vi.mocked(adminApi.getKnowledgeAssistant).mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }));
    const old = editor();
    old.unmount();
    const current = editor("kb-b");
    await flushPromises();
    resolveOld({ ...configuration(), suggestions: ["旧知识库问题"] });
    await flushPromises();
    expect(old.emitted("changed")).toBeUndefined();
    expect(current.emitted("changed")?.[0]?.[0]).toMatchObject({ knowledge_id: "kb-b" });
    expect(current.findAll("input")[0]!.element.value).toBe(questions[0]);
  });

  it("starts each trial without old conversation context and aborts when closed", async () => {
    let callbacks!: chat.ChatStreamCallbacks;
    let signal!: AbortSignal;
    let finish!: () => void;
    const stream = vi.spyOn(chat, "streamChat").mockImplementation(async (_id, _question, _conversation, cb, _options, abort) => {
      callbacks = cb;
      signal = abort!;
      await new Promise<void>((resolve) => { finish = resolve; });
    });
    const wrapper = editor("kb-b");
    await flushPromises();
    await button(wrapper, "试问").trigger("click");
    expect(stream.mock.calls[0]?.slice(0, 3)).toEqual(["kb-b", questions[0], undefined]);
    callbacks.onDelta("设备检查结果");
    await flushPromises();
    expect(wrapper.text()).toContain("设备检查结果");
    wrapper.unmount();
    expect(signal.aborted).toBe(true);
    callbacks.onDelta("过期内容");
    finish();
    await flushPromises();
  });

  it("renders image-bearing answers as content blocks without duplicating text", async () => {
    let callbacks!: chat.ChatStreamCallbacks;
    let finish!: () => void;
    vi.spyOn(chat, "streamChat").mockImplementation(async (_id, _question, _conversation, cb) => {
      callbacks = cb;
      await new Promise<void>((resolve) => { finish = resolve; });
    });
    const image = vi.spyOn(chat, "fetchImageBlobUrl").mockResolvedValue("blob:image-url");
    const wrapper = editor("kb-b");
    await flushPromises();
    await button(wrapper, "试问").trigger("click");
    callbacks.onDone({
      conversation_id: "c1", message_id: "m1", trace_id: "t1",
      answer: "第一步断电第二步开盖",
      response_type: "ANSWERED",
      content: [
        { type: "text", text: "第一步断电" },
        { type: "image", image_asset_id: "asset-1", caption: "内部结构" },
        { type: "text", text: "第二步开盖" },
      ],
      references: [], usage: {}, latency_ms: 10,
    } as chat.ChatDoneEvent);
    finish();
    await flushPromises();

    expect(image).toHaveBeenCalledWith("asset-1", expect.any(AbortSignal));
    expect(wrapper.find("img").attributes("src")).toBe("blob:image-url");
    expect(wrapper.text()).toContain("内部结构");
    // 文本片段只渲染一次：块渲染生效时不再叠加完整 answer。
    expect(wrapper.text().match(/第一步断电/g)).toHaveLength(1);
    expect(wrapper.text().match(/第二步开盖/g)).toHaveLength(1);
  });

  it("does not publish after cancelling confirmation", async () => {
    vi.mocked(ElMessageBox.confirm).mockRejectedValue("cancel");
    const publish = vi.spyOn(adminApi, "setKnowledgeAssistantPublication");
    const wrapper = editor();
    await flushPromises();
    await button(wrapper, "确认上线").trigger("click");
    await flushPromises();
    expect(publish).not.toHaveBeenCalled();
  });

  it("explains unavailable trials and refreshes readiness without discarding unsaved edits", async () => {
    vi.mocked(adminApi.getKnowledgeAssistant)
      .mockResolvedValueOnce({ ...configuration(), ready: false, blockers: ["知识库没有 Active Index"] })
      .mockResolvedValueOnce({ ...configuration(), revision: 8, ready: true });
    const save = vi.spyOn(adminApi, "saveKnowledgeAssistant").mockResolvedValue({ ...configuration(), revision: 1 });
    const stream = vi.spyOn(chat, "streamChat").mockResolvedValue();
    const wrapper = editor();
    await flushPromises();
    expect(button(wrapper, "试问").attributes("disabled")).toBeDefined();
    expect(wrapper.find('span[title*="暂不能试问"]').attributes("title")).toContain("知识库没有 Active Index");
    await wrapper.findAll("input")[0]!.setValue("如何重启设备系统");
    await button(wrapper, "检查就绪").trigger("click");
    await flushPromises();
    expect(ElMessageBox.confirm).not.toHaveBeenCalled();
    expect(wrapper.findAll("input")[0]!.element.value).toBe("如何重启设备系统");
    expect(button(wrapper, "试问").attributes("disabled")).toBeUndefined();
    await button(wrapper, "试问").trigger("click");
    await flushPromises();
    expect(stream.mock.calls[0]?.slice(0, 3)).toEqual(["kb-a", "如何重启设备系统", undefined]);
    await button(wrapper, "保存问题").trigger("click");
    await flushPromises();
    expect(save).toHaveBeenCalledWith("kb-a", 0, ["如何重启设备系统", ...questions.slice(1)]);
  });

  it("explains empty trial questions without requiring online publication", async () => {
    vi.mocked(adminApi.getKnowledgeAssistant).mockResolvedValue({ ...configuration(), suggestions: [] });
    const wrapper = editor();
    await flushPromises();
    expect(wrapper.find('span[title="请先输入要试问的问题"]').exists()).toBe(true);
    await wrapper.findAll("input")[0]!.setValue("如何重启设备系统");
    expect(button(wrapper, "试问").attributes("disabled")).toBeUndefined();
    expect(button(wrapper, "确认上线").attributes("disabled")).toBeDefined();
  });

  it("updates the correct list entry even if the editor closes during a save", async () => {
    let resolve!: (value: KnowledgeAssistantCheck) => void;
    vi.spyOn(adminApi, "saveKnowledgeAssistant").mockImplementation(() => new Promise((done) => { resolve = done; }));
    const apply = vi.spyOn(adminStore, "applyKnowledgeAssistantConfig");
    const wrapper = editor("kb-b");
    await flushPromises();
    await wrapper.findAll("input")[0]!.setValue("如何重启设备系统");
    await button(wrapper, "保存问题").trigger("click");
    wrapper.unmount();
    resolve({ ...configuration("kb-b"), revision: 1 });
    await flushPromises();
    expect(apply).toHaveBeenCalledWith(expect.objectContaining({ knowledge_id: "kb-b", revision: 1 }));
  });
});

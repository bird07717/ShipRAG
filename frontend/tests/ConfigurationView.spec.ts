import ElementPlus, { ElMessageBox } from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { adminStore, type ModelActionResult } from "@/composables/adminStore";
import ConfigurationView from "@/views/ConfigurationView.vue";
import type { ModelConfig, ModelProbeResult } from "@/types/admin";

const modelA: ModelConfig = {
  id: "model-a", name: "模型 A", model_type: "LLM", provider: "dashscope",
  model_name: "saved-model-a", base_url: "https://example.invalid/v1/", enabled: true,
  api_key_configured: true, api_key_source: "DATABASE", supported_providers: ["dashscope", "zhipu"],
  parameters: { temperature: 0.8, max_tokens: 9000, thinking: { type: "enabled" }, retained: "unchanged" },
};
const modelB: ModelConfig = { ...modelA, id: "model-b", name: "模型 B", model_type: "VISION", model_name: "saved-model-b", parameters: {} };
const probe: ModelProbeResult = { ok: true, model_type: "LLM", provider: "dashscope", model_name: modelA.model_name, latency_ms: 1, detail: "连通正常" };
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
const wrappers: ReturnType<typeof mount>[] = [];
function editor(models = [modelA, modelB]) {
  adminStore.models = models;
  const wrapper = mount(ConfigurationView, { global: { plugins: [ElementPlus] } });
  wrappers.push(wrapper);
  return wrapper;
}
async function open(wrapper: ReturnType<typeof editor>, position = 0) {
  await flushPromises();
  await wrapper.findAll('button[aria-label="编辑模型配置"]')[position]!.trigger("click");
  await flushPromises();
}
function button(wrapper: ReturnType<typeof editor>, text: string) {
  return wrapper.findAllComponents({ name: "ElButton" }).find((item) => text === "清除数据库密钥" ? item.text().startsWith(text) : item.text() === text)!;
}
function dialog(wrapper: ReturnType<typeof editor>) { return wrapper.findComponent({ name: "ElDialog" }); }

describe("model configuration editor", () => {
  beforeEach(() => {
    adminStore.serviceToken = "test-service-token";
    adminStore.credentialRejected = false;
    adminStore.error = "";
    adminStore.notice = "";
    adminStore.loading = false;
    adminStore.prompts = [];
    vi.spyOn(adminStore, "saveModelConfig").mockResolvedValue({ ok: true });
    vi.spyOn(adminStore, "clearModelApiKey").mockResolvedValue({ ok: true });
    vi.spyOn(adminStore, "probeModel").mockResolvedValue(probe);
    vi.spyOn(ElMessageBox, "confirm").mockResolvedValue({ action: "confirm" } as never);
  });
  afterEach(() => {
    wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
    vi.restoreAllMocks();
    adminStore.models = [];
    adminStore.serviceToken = "";
    adminStore.credentialRejected = false;
    adminStore.error = "";
    adminStore.notice = "";
  });

  it("explains V2 parameters read-only and preserves the saved JSON when saving identity fields", async () => {
    const wrapper = editor(); await open(wrapper);
    expect(wrapper.text()).toContain("V2 生成参数（只读说明）");
    expect(wrapper.text()).toContain("M3_RAG_ANSWER_THINKING_ENABLED");
    expect(wrapper.findAllComponents({ name: "ElInputNumber" })).toHaveLength(0);
    expect(wrapper.findAllComponents({ name: "ElSwitch" })).toHaveLength(1);
    await wrapper.get('input[aria-label="模型名称"]').setValue("new-model-a");
    await button(wrapper, "保存").trigger("click"); await flushPromises();
    expect(adminStore.saveModelConfig).toHaveBeenCalledWith("model-a", {
      provider: "dashscope", model_name: "new-model-a", base_url: modelA.base_url, enabled: true,
    });
    expect(modelA.parameters).toEqual({ temperature: 0.8, max_tokens: 9000, thinking: { type: "enabled" }, retained: "unchanged" });
    expect(dialog(wrapper).props("modelValue")).toBe(false);
  });

  it("explicitly tests saved configuration, not the edited draft, and clears the badge on draft edits", async () => {
    const wrapper = editor(); await open(wrapper);
    expect(wrapper.text()).toContain("不测试编辑中的草稿");
    await wrapper.get('input[aria-label="模型名称"]').setValue("draft-model");
    await wrapper.get('input[aria-label="模型 API 密钥"]').setValue("test-draft-key");
    await button(wrapper, "测试已保存配置").trigger("click"); await flushPromises();
    expect(adminStore.probeModel).toHaveBeenCalledWith("model-a");
    expect(adminStore.saveModelConfig).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("已保存配置连通正常");
    await wrapper.get('input[aria-label="模型名称"]').setValue("another-draft");
    expect(wrapper.text()).not.toContain("已保存配置连通正常");
  });

  it.each(["success", "failure"])("ignores an old probe's %s and preserves a newer probe's loading state", async (outcome) => {
    const older = deferred<ModelProbeResult>(); const newer = deferred<ModelProbeResult>();
    vi.mocked(adminStore.probeModel).mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise);
    const wrapper = editor(); await open(wrapper);
    await button(wrapper, "测试已保存配置").trigger("click");
    await button(wrapper, "取消").trigger("click"); await open(wrapper, 1);
    await button(wrapper, "测试已保存配置").trigger("click");
    if (outcome === "success") older.resolve(probe);
    else older.reject(new Error("旧模型探测失败"));
    await flushPromises();
    expect(button(wrapper, "测试已保存配置").props("loading")).toBe(true);
    expect(wrapper.text()).not.toContain("已保存配置连通正常");
    expect(wrapper.text()).not.toContain("旧模型探测失败");
    newer.resolve({ ...probe, model_name: modelB.model_name }); await flushPromises();
    expect(button(wrapper, "测试已保存配置").props("loading")).toBe(false);
    expect(wrapper.text()).toContain("已保存配置连通正常");
  });

  it("ignores an in-flight probe after editing its draft", async () => {
    const pending = deferred<ModelProbeResult>(); vi.mocked(adminStore.probeModel).mockReturnValue(pending.promise);
    const wrapper = editor(); await open(wrapper);
    await button(wrapper, "测试已保存配置").trigger("click");
    await wrapper.get('input[aria-label="请求地址"]').setValue("https://another.invalid/v1/");
    pending.resolve(probe); await flushPromises();
    expect(wrapper.text()).not.toContain("已保存配置连通正常");
    expect(button(wrapper, "测试已保存配置").props("loading")).toBe(false);
  });

  it.each([0, 1])("does not restore an old key-clear target after reopening model %i", async (position) => {
    const pending = deferred<ModelActionResult>(); vi.mocked(adminStore.clearModelApiKey).mockReturnValue(pending.promise);
    const wrapper = editor(); await open(wrapper);
    await button(wrapper, "清除数据库密钥").trigger("click");
    expect(button(wrapper, "保存").props("disabled")).toBe(true);
    await button(wrapper, "取消").trigger("click"); await open(wrapper, position);
    await wrapper.get('input[aria-label="模型名称"]').setValue("new-draft");
    pending.resolve({ ok: true }); await flushPromises();
    await button(wrapper, "保存").trigger("click"); await flushPromises();
    expect(adminStore.saveModelConfig).toHaveBeenCalledWith(position ? "model-b" : "model-a", expect.objectContaining({ model_name: "new-draft" }));
  });

  it("refreshes the saved key state without discarding identity edits", async () => {
    vi.mocked(adminStore.clearModelApiKey).mockImplementation(async () => {
      adminStore.models = [{ ...modelA, api_key_source: "ENV" }, modelB];
      return { ok: true };
    });
    const wrapper = editor(); await open(wrapper);
    await wrapper.get('input[aria-label="模型名称"]').setValue("identity-draft");
    await button(wrapper, "清除数据库密钥").trigger("click"); await flushPromises();
    expect(wrapper.get('input[aria-label="模型名称"]').element).toHaveProperty("value", "identity-draft");
    expect(wrapper.text()).toContain("环境变量密钥");
    expect(wrapper.findAll("button").some((item) => item.text().includes("清除数据库密钥"))).toBe(false);
    await button(wrapper, "保存").trigger("click"); await flushPromises();
    expect(adminStore.saveModelConfig).toHaveBeenCalledWith("model-a", expect.objectContaining({ model_name: "identity-draft" }));
  });

  it.each([0, 1])("does not close a reopened model %i editor when an older save completes", async (position) => {
    const pending = deferred<ModelActionResult>(); vi.mocked(adminStore.saveModelConfig).mockReturnValue(pending.promise);
    const wrapper = editor(); await open(wrapper);
    await button(wrapper, "保存").trigger("click");
    await button(wrapper, "取消").trigger("click"); await open(wrapper, position);
    pending.resolve({ ok: true }); await flushPromises();
    expect(dialog(wrapper).props("modelValue")).toBe(true);
    expect(wrapper.get('input[aria-label="模型名称"]').element).toHaveProperty("value", position ? modelB.model_name : modelA.model_name);
  });

  it.each(["closed", "reopened", "credential-replaced"])("does not submit a rebuild after confirmation if the editor was %s", async (reason) => {
    const pending = deferred<never>(); vi.mocked(ElMessageBox.confirm).mockReturnValue(pending.promise);
    const wrapper = editor([{ ...modelA, model_type: "EMBEDDING" }, modelB]); await open(wrapper);
    await wrapper.get('input[aria-label="模型名称"]').setValue("new-embedding");
    await button(wrapper, "保存").trigger("click");
    if (reason === "credential-replaced") adminStore.serviceToken = "replacement-test-token";
    else {
      await button(wrapper, "取消").trigger("click");
      if (reason === "reopened") await open(wrapper);
    }
    pending.resolve({ action: "confirm" } as never); await flushPromises();
    expect(adminStore.saveModelConfig).not.toHaveBeenCalled();
  });

  it("requires embedding confirmation and explains interruption, index checks and new-session acceptance", async () => {
    const wrapper = editor([{ ...modelA, model_type: "EMBEDDING" }]); await open(wrapper);
    expect(wrapper.text()).toContain("查询向量生成");
    expect(wrapper.text()).toContain("实际构建模型的供应商、名称、地址和维度");
    expect(wrapper.text()).toContain("查询与卡片生成会校验身份");
    expect(wrapper.text()).toContain("不匹配时停止，不改用另一模型检索旧向量");
    expect(wrapper.text()).toContain("检查活动索引、文档理解生成/确认及上线就绪");
    expect(wrapper.text()).toContain("旧会话不会自动迁移");
    await wrapper.get('input[aria-label="模型名称"]').setValue("new-embedding");
    await button(wrapper, "保存").trigger("click"); await flushPromises();
    expect(ElMessageBox.confirm).toHaveBeenCalledWith(expect.stringContaining("不能保证无中断切换"), expect.any(String), expect.any(Object));
    expect(adminStore.saveModelConfig).toHaveBeenCalledWith("model-a", expect.objectContaining({ rebuild_all_confirmed: true }));
  });

  it("keeps the embedding editor open after cancelling rebuild confirmation", async () => {
    vi.mocked(ElMessageBox.confirm).mockRejectedValue(new Error("cancel"));
    const wrapper = editor([{ ...modelA, model_type: "EMBEDDING" }]); await open(wrapper);
    await wrapper.get('input[aria-label="模型名称"]').setValue("new-embedding");
    await button(wrapper, "保存").trigger("click"); await flushPromises();
    expect(adminStore.saveModelConfig).not.toHaveBeenCalled();
    expect(dialog(wrapper).props("modelValue")).toBe(true);
    expect(button(wrapper, "保存").props("loading")).toBe(false);
  });

  it.each(["OCR", "VISION"])("explains the %s build snapshot, reuse and environment fallback", async (type) => {
    const wrapper = editor([{ ...modelA, model_type: type }]); await open(wrapper);
    expect(wrapper.text()).toContain("重建会比较实际构建模型指纹");
    expect(wrapper.text()).toContain("身份变化时不复用旧解析和图片描述");
    expect(wrapper.text()).toContain("身份未变且文件及解析版本一致时才可复用");
    expect(wrapper.text()).toContain("没有指纹的旧文档首次重建会重新解析");
    expect(wrapper.text()).toContain("不替换正在运行的任务快照");
    expect(wrapper.text()).toContain("并不代表关闭识别功能");
    expect(wrapper.text()).not.toContain("V2 生成参数（只读说明）");
    await button(wrapper, "保存").trigger("click"); await flushPromises();
    expect(adminStore.saveModelConfig).toHaveBeenCalledWith("model-a", expect.not.objectContaining({ parameters: expect.anything() }));
  });
});

import ElementPlus from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { adminApi } from "@/api/admin";
import { adminStore } from "@/composables/adminStore";
import ConfigurationView from "@/views/ConfigurationView.vue";
import type { ModelConfig } from "@/types/admin";

const modelA: ModelConfig = {
  id: "model-a", name: "模型 A", model_type: "LLM", provider: "dashscope",
  model_name: "saved-a", base_url: "https://example.invalid/v1/", enabled: true,
  api_key_configured: true, api_key_source: "DATABASE", parameters: {},
};
const modelB: ModelConfig = { ...modelA, id: "model-b", name: "模型 B", model_name: "saved-b" };
const latestModels = [modelA, { ...modelB, model_name: "new-b" }];
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
let wrapper: ReturnType<typeof mount> | undefined;
function editor() {
  wrapper = mount(ConfigurationView, { global: { plugins: [ElementPlus] } });
  return wrapper;
}
async function open(position = 0) {
  await flushPromises();
  await wrapper!.findAll('button[aria-label="编辑模型配置"]')[position]!.trigger("click");
  await flushPromises();
}
function button(text: string) {
  return wrapper!.findAllComponents({ name: "ElButton" }).find((item) => item.text().startsWith(text))!;
}
async function startOlder(operation: "save" | "clear", pending: ReturnType<typeof deferred<ModelConfig>>) {
  if (operation === "save") vi.mocked(adminApi.updateModel).mockReturnValueOnce(pending.promise);
  else vi.mocked(adminApi.deleteApiKey).mockReturnValueOnce(pending.promise);
  editor(); await open();
  await button(operation === "save" ? "保存" : "清除数据库密钥").trigger("click");
  await button("取消").trigger("click"); await open(1);
}

describe("model editor with real Store operations and mocked HTTP APIs", () => {
  beforeEach(() => {
    adminStore.models = [modelA, modelB];
    adminStore.serviceToken = "test-service-token";
    adminStore.credentialRejected = false;
    adminStore.error = "";
    adminStore.notice = "";
    adminStore.activeRequestCount = 0;
    adminStore.loading = false;
    adminStore.selectedKnowledgeId = "";
    adminStore.prompts = [];
    vi.spyOn(adminApi, "updateModel").mockResolvedValue(modelB);
    vi.spyOn(adminApi, "deleteApiKey").mockResolvedValue(modelA);
    vi.spyOn(adminApi, "putApiKey").mockResolvedValue(modelA);
    vi.spyOn(adminApi, "listModels").mockResolvedValue(latestModels);
    vi.spyOn(adminApi, "listKnowledgeBases").mockResolvedValue([]);
    vi.spyOn(adminApi, "listTraces").mockResolvedValue([]);
    vi.spyOn(adminApi, "listPrompts").mockResolvedValue([]);
    vi.spyOn(adminApi, "getSystemInfo").mockResolvedValue({} as never);
  });
  afterEach(() => {
    wrapper?.unmount(); wrapper = undefined;
    vi.restoreAllMocks();
    adminStore.models = [];
    adminStore.serviceToken = "";
    adminStore.credentialRejected = false;
    adminStore.error = "";
    adminStore.notice = "";
  });

  it.each(["save", "clear"] as const)("ignores an older %s failure while the newer save is pending", async (operation) => {
    const older = deferred<ModelConfig>(); const newer = deferred<ModelConfig>();
    await startOlder(operation, older);
    vi.mocked(adminApi.updateModel).mockReturnValueOnce(newer.promise);
    await button("保存").trigger("click");
    older.reject(new Error("旧操作失败")); await flushPromises();
    expect(adminStore.error).toBe("");
    expect(button("保存").props("loading")).toBe(true);
    expect(adminStore.loading).toBe(true);
    newer.resolve(modelB); await flushPromises();
    expect(wrapper!.findComponent({ name: "ElDialog" }).props("modelValue")).toBe(false);
    expect(adminStore.notice).toContain("已保存");
    expect(adminStore.activeRequestCount).toBe(0);
  });

  it.each(["save", "clear"] as const)("ignores an older %s failure after the newer save succeeds", async (operation) => {
    const older = deferred<ModelConfig>(); await startOlder(operation, older);
    await button("保存").trigger("click"); await flushPromises();
    const notice = adminStore.notice;
    older.reject(new Error("旧操作失败")); await flushPromises();
    expect(adminStore.error).toBe("");
    expect(adminStore.notice).toBe(notice);
  });

  it.each(["save", "clear"] as const)("keeps the current save error when an older %s succeeds", async (operation) => {
    const older = deferred<ModelConfig>(); await startOlder(operation, older);
    vi.mocked(adminApi.updateModel).mockRejectedValueOnce(new Error("当前保存失败"));
    await button("保存").trigger("click"); await flushPromises();
    expect(wrapper!.findComponent({ name: "ElDialog" }).props("modelValue")).toBe(true);
    expect(wrapper!.text()).toContain("当前保存失败");
    older.resolve(modelA); await flushPromises();
    expect(adminStore.error).toBe("当前保存失败");
    expect(adminStore.notice).toBe("");
    expect(wrapper!.text()).toContain("当前保存失败");
  });

  it.each(["save", "clear"] as const)("shows a real %s API failure inside the current editor", async (operation) => {
    const api = operation === "save" ? adminApi.updateModel : adminApi.deleteApiKey;
    vi.mocked(api).mockRejectedValueOnce(new Error("模型操作失败"));
    editor(); await open();
    await button(operation === "save" ? "保存" : "清除数据库密钥").trigger("click"); await flushPromises();
    expect(wrapper!.text()).toContain("模型操作失败");
    expect(wrapper!.findComponent({ name: "ElDialog" }).props("modelValue")).toBe(true);
    expect(button("保存").props("loading")).toBe(false);
    expect(button("保存").props("disabled")).toBe(false);
  });

  it.each(["initial", "save", "clear", "put"])("does not apply an older %s list after a newer model save", async (origin) => {
    const older = deferred<ModelConfig[]>();
    vi.mocked(adminApi.listModels).mockReturnValueOnce(older.promise);
    const first = origin === "initial" ? adminStore.refreshAll()
      : origin === "save" ? adminStore.saveModelConfig(modelA.id, { enabled: true })
      : origin === "clear" ? adminStore.clearModelApiKey(modelA.id)
      : adminStore.putModelApiKey(modelA.id, "test-model-key");
    await flushPromises();
    await adminStore.saveModelConfig(modelB.id, { model_name: "new-b" });
    expect(adminStore.models[1]?.model_name).toBe("new-b");
    older.resolve([modelA, modelB]); await first;
    expect(adminStore.models[1]?.model_name).toBe("new-b");
  });

  it("ignores a superseded list failure instead of reporting an old operation error", async () => {
    const older = deferred<ModelConfig[]>();
    vi.mocked(adminApi.listModels).mockReturnValueOnce(older.promise);
    const first = adminStore.clearModelApiKey(modelA.id); await flushPromises();
    await adminStore.saveModelConfig(modelB.id, { enabled: true });
    older.reject(new Error("旧列表失败")); await first;
    expect(adminStore.error).toBe("");
    expect(adminStore.models[1]?.model_name).toBe("new-b");
  });

  it("reports the latest list refresh failure without applying an older successful list", async () => {
    const older = deferred<ModelConfig[]>();
    vi.mocked(adminApi.listModels).mockReturnValueOnce(older.promise).mockRejectedValueOnce(new Error("最新列表失败"));
    const first = adminStore.clearModelApiKey(modelA.id); await flushPromises();
    await adminStore.saveModelConfig(modelB.id, { enabled: true });
    older.resolve(latestModels); await first;
    expect(adminStore.error).toBe("最新列表失败");
    expect(adminStore.models[1]?.model_name).toBe("saved-b");
    expect(adminStore.notice).toBe("");
  });

  it("ignores list and operation status after the credential changes", async () => {
    const pending = deferred<ModelConfig[]>(); vi.mocked(adminApi.listModels).mockReturnValue(pending.promise);
    const saving = adminStore.saveModelConfig(modelA.id, { enabled: true }); await flushPromises();
    adminStore.serviceToken = "replacement-test-token";
    pending.resolve(latestModels); await saving;
    expect(adminStore.models[1]?.model_name).toBe("saved-b");
    expect(adminStore.notice).toBe("");
  });
});

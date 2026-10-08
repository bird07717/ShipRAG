import ElementPlus from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { adminApi } from "@/api/admin";
import ModelRuntimeBanner from "@/components/ModelRuntimeBanner.vue";
import { adminStore } from "@/composables/adminStore";
import type { ModelRuntimeItem, ModelRuntimeStatus } from "@/types/admin";
import { environmentBadge, modelRuntimeIssues } from "@/utils/modelRuntime";

function item(overrides: Partial<ModelRuntimeItem>): ModelRuntimeItem {
  return {
    model_type: "LLM",
    source: "DATABASE",
    provider: "dashscope",
    model_name: "qwen",
    stored_key_configured: true,
    environment_key_configured: false,
    kind: "REAL",
    reason: "CONFIGURED",
    ...overrides,
  };
}

function status(models: ModelRuntimeItem[], simulatedIndexes = 0): ModelRuntimeStatus {
  return {
    environment: "development",
    models,
    simulated_active_index_count: simulatedIndexes,
    simulated: simulatedIndexes > 0 || models.some((model) => model.kind === "FAKE"),
  };
}

describe("model runtime issues", () => {
  it("reports simulated and unavailable models but not real or disabled ones", () => {
    const issues = modelRuntimeIssues(status([
      item({}),
      item({ model_type: "EMBEDDING", kind: "FAKE", reason: "AUTO_NO_KEY", provider: "siliconflow" }),
      item({ model_type: "RERANK", kind: "NOT_CONFIGURED", reason: "NO_API_KEY" }),
      item({ model_type: "OCR", kind: "DISABLED", reason: "MODE_DISABLED" }),
      item({ model_type: "VISION", kind: "REAL", reason: "EXPLICIT_PROVIDER", stored_key_configured: false }),
    ]));
    expect(issues.map((issue) => [issue.item.model_type, issue.severity])).toEqual([
      ["EMBEDDING", "simulated"],
      ["RERANK", "unavailable"],
      ["VISION", "unavailable"],
    ]);
    expect(issues[0]!.detail).toContain("自动化测试环境");
    expect(issues[1]!.detail).toContain("不会自动改用模拟模型");
    expect(issues[0]!.detail).toContain("siliconflow");
    expect(modelRuntimeIssues(null)).toEqual([]);
  });

  it("explains when an unsupported database provider fell back to the environment", () => {
    const [issue] = modelRuntimeIssues(status([
      item({ model_type: "OCR", source: "ENV_FALLBACK", provider: "siliconflow", kind: "FAKE", reason: "AUTO_NO_KEY" }),
    ]));
    expect(issue!.detail).toContain("已改用环境默认提供方");
  });

  it("labels known environments and passes unknown ones through", () => {
    expect(environmentBadge("production")).toEqual({ label: "生产环境", tone: "production" });
    expect(environmentBadge("development")?.label).toBe("开发环境");
    expect(environmentBadge("qa")).toEqual({ label: "qa", tone: "test" });
    expect(environmentBadge(undefined)).toBeNull();
  });
});

describe("ModelRuntimeBanner", () => {
  beforeEach(() => {
    adminStore.serviceToken = "test-token";
    adminStore.credentialRejected = false;
  });
  afterEach(() => {
    adminStore.modelRuntime = null;
    adminStore.serviceToken = "";
    vi.restoreAllMocks();
  });

  it("stays hidden while everything runs real models or the status is unknown", () => {
    adminStore.modelRuntime = null;
    const unknown = mount(ModelRuntimeBanner, { global: { plugins: [ElementPlus] } });
    expect(unknown.find(".system-banner").exists()).toBe(false);
    adminStore.modelRuntime = status([item({}), item({ model_type: "EMBEDDING" })]);
    const healthy = mount(ModelRuntimeBanner, { global: { plugins: [ElementPlus] } });
    expect(healthy.find(".system-banner").exists()).toBe(false);
  });

  it("warns in red about simulated models and indexes, with details and a link", async () => {
    adminStore.modelRuntime = status([
      item({ model_type: "EMBEDDING", kind: "FAKE", reason: "AUTO_NO_KEY" }),
      item({ model_type: "RERANK", kind: "NOT_CONFIGURED", reason: "NO_API_KEY" }),
    ], 2);
    const navigate = vi.spyOn(adminStore, "navigate").mockImplementation(() => {});
    const wrapper = mount(ModelRuntimeBanner, { global: { plugins: [ElementPlus] } });
    const banner = wrapper.get(".system-banner");
    expect(banner.classes()).toContain("tone-danger");
    expect(banner.text()).toContain("模拟实现");
    expect(banner.text()).toContain("模拟：EMBEDDING");
    expect(banner.text()).toContain("不可用：RERANK");
    expect(banner.text()).toContain("2 个当前生效的索引由模拟向量构建");

    await wrapper.findAll("button").find((button) => button.text() === "查看原因")!.trigger("click");
    expect(wrapper.text()).toContain("deterministic-shake256");
    await wrapper.findAll("button").find((button) => button.text() === "前往模型配置")!.trigger("click");
    expect(navigate).toHaveBeenCalledWith("configuration", "tab=models");
  });

  it("uses an amber banner when models are only unavailable", () => {
    adminStore.modelRuntime = status([item({ kind: "NOT_CONFIGURED", reason: "MODEL_NOT_ENABLED", provider: null, model_name: null })]);
    const wrapper = mount(ModelRuntimeBanner, { global: { plugins: [ElementPlus] } });
    expect(wrapper.get(".system-banner").classes()).toContain("tone-warning");
    expect(wrapper.text()).toContain("部分模型当前不可用");
  });

  it("hides the banner without a credential", () => {
    adminStore.modelRuntime = status([item({ kind: "FAKE", reason: "FAKE_MODE" })]);
    adminStore.serviceToken = "";
    const wrapper = mount(ModelRuntimeBanner, { global: { plugins: [ElementPlus] } });
    expect(wrapper.find(".system-banner").exists()).toBe(false);
  });
});

describe("adminStore.loadModelRuntime", () => {
  beforeEach(() => {
    adminStore.serviceToken = "test-token";
    adminStore.credentialRejected = false;
  });
  afterEach(() => {
    adminStore.modelRuntime = null;
    adminStore.serviceToken = "";
    adminStore.credentialRejected = false;
    vi.restoreAllMocks();
  });

  it("stores the latest status and clears it on failure instead of keeping a stale answer", async () => {
    const api = vi.spyOn(adminApi, "getModelRuntime").mockResolvedValue(status([item({})]));
    await adminStore.loadModelRuntime();
    expect(adminStore.modelRuntime?.models).toHaveLength(1);
    api.mockRejectedValue(new Error("down"));
    await adminStore.loadModelRuntime();
    expect(adminStore.modelRuntime).toBeNull();
  });

  it("ignores a late response from an older request or a replaced credential", async () => {
    let resolveOld!: (value: ModelRuntimeStatus) => void;
    vi.spyOn(adminApi, "getModelRuntime")
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }))
      .mockResolvedValueOnce(status([item({ model_type: "RERANK" })]));
    const older = adminStore.loadModelRuntime();
    await adminStore.loadModelRuntime();
    resolveOld(status([item({ model_type: "EMBEDDING", kind: "FAKE" })]));
    await older;
    expect(adminStore.modelRuntime?.models[0]?.model_type).toBe("RERANK");

    let resolveRejected!: (value: ModelRuntimeStatus) => void;
    vi.mocked(adminApi.getModelRuntime).mockImplementationOnce(() => new Promise((resolve) => { resolveRejected = resolve; }));
    const pending = adminStore.loadModelRuntime();
    adminStore.rejectCredential("invalid");
    expect(adminStore.modelRuntime).toBeNull();
    resolveRejected(status([item({ kind: "FAKE" })]));
    await pending;
    expect(adminStore.modelRuntime).toBeNull();
    await flushPromises();
  });

  it("does not request without a credential", async () => {
    adminStore.serviceToken = "";
    const api = vi.spyOn(adminApi, "getModelRuntime");
    await adminStore.loadModelRuntime();
    expect(api).not.toHaveBeenCalled();
    expect(adminStore.modelRuntime).toBeNull();
  });
});

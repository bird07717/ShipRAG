import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "@/App.vue";
import { adminStore } from "@/composables/adminStore";

const globalStubs = {
  AdminStudio: { template: "<div>admin-studio</div>" },
  DemoChat: { template: "<div>demo-chat</div>" },
  HealthPanel: { template: "<div>health-panel</div>" },
  EmptyState: {
    props: ["title", "description"],
    template: "<div>{{ title }} {{ description }}<slot /><slot name='action' /></div>",
  },
  PageHeader: {
    props: ["title", "description"],
    template: "<header>{{ title }} {{ description }}<slot /><slot name='actions' /></header>",
  },
  ElButton: { template: "<button><slot /></button>" },
  ElIcon: true,
  ElTag: { template: "<span><slot /></span>" },
};

describe("App", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    adminStore.serviceToken = "";
    adminStore.credentialRejected = false;
    adminStore.knowledgeBases = [];
    adminStore.requestedView = "";
    adminStore.requestedViewQuery = "";
    window.history.replaceState(null, "", "#/dashboard");
  });

  it("renders the Support Studio navigation", async () => {
    window.history.replaceState(null, "", "#/dashboard");
    const wrapper = mount(App, {
      global: {
        stubs: {
          AdminStudio: { template: "<div>admin-studio</div>" },
          HealthPanel: { template: "<div>health-panel</div>" },
          ElButton: { template: "<button><slot /></button>" },
          ElIcon: true,
          ElTag: { template: "<span><slot /></span>" },
        },
      },
    });

    expect(wrapper.text()).toContain("技术支持工作台");
    expect(wrapper.text()).not.toContain("发布管理");
    expect(wrapper.text()).toContain("问答测试");
    expect(wrapper.text()).toContain("配置审核");
    expect(wrapper.text()).toContain("引擎路由");
    expect(wrapper.text()).toContain("日志与链路追踪");
    expect(wrapper.text()).toContain("模型、凭证与系统配置");
    expect(wrapper.text()).not.toContain("Support Configuration");
    expect(wrapper.text()).not.toContain("RAG Configuration");
    expect(wrapper.text()).not.toContain("Support Errors");
    expect(wrapper.text()).not.toContain("RAG Errors");
    expect(wrapper.text()).toContain("知识库");
    expect(wrapper.text()).toContain("RAG V2 知识发布");
    expect(wrapper.text()).toContain("RAG 技术构建生成候选索引，经管理员明确切换");
    expect(wrapper.text()).toContain("admin-studio");
    expect(wrapper.text()).toContain("health-panel");
    expect(wrapper.findAll("nav button")).toHaveLength(10);
    expect(wrapper.find(".management-context").exists()).toBe(false);

    const playground = wrapper.findAll("nav button").find((item) => item.text().includes("调试台"));
    await playground?.trigger("click");
    expect(wrapper.text()).toContain("RAG V2 检索、上下文与答案调试");
    expect(window.location.hash).toBe("#/playground");
    wrapper.unmount();
    window.history.replaceState(null, "", "#/dashboard");
  });

  it.each([
    ["support-configuration", "indexes", "技术构建、快照与回收诊断"],
    ["support-profiles", "support-review", "Support Profile 草稿审核与发布（已冻结，仅历史兼容）"],
    ["support-errors", "logs?tab=failed-turns&engine=SUPPORT", "错误、失败任务与完整链路排查"],
    ["rag-errors", "logs?tab=failed-turns&engine=RAG", "错误、失败任务与完整链路排查"],
    ["traces", "logs?tab=traces", "错误、失败任务与完整链路排查"],
    ["rag-configuration", "configuration?tab=rag", "模型、Prompt、RAG 参数与服务凭证（Service Token）"],
    ["settings", "configuration?tab=credentials", "模型、Prompt、RAG 参数与服务凭证（Service Token）"],
  ])("redirects retired #%s hash to #%s", (legacyView, canonicalLocation, description) => {
    window.history.replaceState(null, "", `#/${legacyView}`);
    const wrapper = mount(App, {
      global: {
        stubs: {
          AdminStudio: { template: "<div>admin-studio</div>" },
          HealthPanel: { template: "<div>health-panel</div>" },
          ElButton: { template: "<button><slot /></button>" },
          ElIcon: true,
          ElTag: { template: "<span><slot /></span>" },
        },
      },
    });

    expect(window.location.hash).toBe(`#/${canonicalLocation}`);
    expect(wrapper.text()).toContain(description);
    wrapper.unmount();
    window.history.replaceState(null, "", "#/dashboard");
  });

  it("gates standalone chat until a service credential is configured", async () => {
    adminStore.serviceToken = "";
    window.history.replaceState(null, "", "#/support-chat");
    const wrapper = mount(App, { global: { stubs: globalStubs } });

    expect(wrapper.text()).toContain("需要配置服务凭证");
    expect(wrapper.text()).not.toContain("demo-chat");

    const configure = wrapper
      .findAll("button")
      .find((button) => button.text().includes("配置服务凭证"));
    await configure?.trigger("click");
    expect(window.location.hash).toBe("#/configuration?tab=credentials");
    wrapper.unmount();
  });

  it("loads chat data once when a valid credential opens standalone chat", async () => {
    adminStore.serviceToken = "session-token";
    adminStore.credentialRejected = false;
    adminStore.knowledgeBases = [];
    const refreshAll = vi.spyOn(adminStore, "refreshAll").mockResolvedValue(undefined);
    const run = vi.spyOn(adminStore, "run").mockImplementation(async (action) => action());
    window.history.replaceState(null, "", "#/support-chat");

    const wrapper = mount(App, { global: { stubs: globalStubs } });
    await Promise.resolve();

    expect(wrapper.text()).toContain("demo-chat");
    expect(run).toHaveBeenCalledOnce();
    expect(refreshAll).toHaveBeenCalledOnce();

    adminStore.requestedView = "missing-view";
    await wrapper.vm.$nextTick();
    expect(window.location.hash).toBe("#/support-chat");

    adminStore.requestedViewQuery = "tab=rag";
    adminStore.requestedView = "configuration";
    await wrapper.vm.$nextTick();
    expect(window.location.hash).toBe("#/configuration?tab=rag");
    expect(adminStore.requestedView).toBe("");
    wrapper.unmount();
  });

  it("normalizes an unknown hash and responds to hash changes", async () => {
    window.history.replaceState(null, "", "#/unknown?ignored=true");
    const wrapper = mount(App, { global: { stubs: globalStubs } });

    expect(window.location.hash).toBe("#/dashboard");
    window.history.pushState(null, "", "#/documents?source=menu");
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    await wrapper.vm.$nextTick();

    expect(wrapper.text()).toContain("文档上传、内容与切片检查");
    wrapper.unmount();
  });
});

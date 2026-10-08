import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "@/App.vue";
import { SERVICE_CREDENTIAL_REJECTED_EVENT } from "@/api/serviceCredential";
import { adminStore } from "@/composables/adminStore";

const globalStubs = {
  AdminStudio: { template: "<div>admin-studio</div>" },
  // Polls the task list on mount; covered by its own tests.
  TaskCenter: true,
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
  ElTooltip: { inheritAttrs: false, template: "<slot />" },
  ElDrawer: true,
};

describe("App", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    adminStore.serviceToken = "";
    adminStore.credentialRejected = false;
    adminStore.knowledgeBases = [];
    adminStore.requestedView = "";
    adminStore.requestedViewQuery = "";
    sessionStorage.clear();
    window.history.replaceState(null, "", "#/dashboard");
  });

  it("renders the Support Studio navigation", async () => {
    window.history.replaceState(null, "", "#/dashboard");
    const wrapper = mount(App, {
      global: {
        stubs: {
          AdminStudio: { template: "<div>admin-studio</div>" },
          TaskCenter: true,
          HealthPanel: { template: "<div>health-panel</div>" },
          ElButton: { template: "<button><slot /></button>" },
          ElIcon: true,
          ElTag: { template: "<span><slot /></span>" },
          ElTooltip: { inheritAttrs: false, template: "<slot />" },
          ElDrawer: true,
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
    expect(wrapper.text()).toContain("运行版本");
    expect(wrapper.text()).not.toContain("RAG V2 知识发布");
    expect(wrapper.text()).toContain("admin-studio");
    expect(wrapper.text()).toContain("health-panel");
    expect(wrapper.findAll("nav button")).toHaveLength(11);
    expect(wrapper.text()).toContain("会话浏览");
    expect(wrapper.find(".management-context").exists()).toBe(false);

    const playground = wrapper.findAll("nav button").find((item) => item.text().includes("调试台"));
    await playground?.trigger("click");
    expect(wrapper.text()).toContain("RAG V2 检索、上下文与答案调试");
    expect(window.location.hash).toBe("#/playground");
    wrapper.unmount();
    window.history.replaceState(null, "", "#/dashboard");
  });

  it("collapses the sidebar on demand, remembers it, and folds the legacy group", async () => {
    localStorage.removeItem("shipdemo.sidebarCollapsed");
    const wrapper = mount(App, { global: { stubs: globalStubs } });

    expect(wrapper.find(".shell").classes()).not.toContain("is-collapsed");
    const legacy = wrapper.get(".nav-group.is-collapsible");
    expect(legacy.get(".nav-items").attributes("style")).toContain("display: none");
    expect(legacy.text()).toContain("配置审核"); // still in the DOM for search/tests
    await legacy.get(".nav-group-toggle").trigger("click");
    expect(legacy.get(".nav-items").attributes("style") ?? "").not.toContain("display: none");

    const active = wrapper.findAll("nav button").find((item) => item.attributes("aria-current") === "page");
    expect(active?.text()).toContain("运行概览");

    await wrapper.get(".sidebar-toggle").trigger("click");
    expect(wrapper.find(".shell").classes()).toContain("is-collapsed");
    expect(localStorage.getItem("shipdemo.sidebarCollapsed")).toBe("1");
    expect(wrapper.get(".sidebar-toggle").attributes("aria-label")).toBe("展开侧栏");
    expect(wrapper.findAll("nav button")[0]!.attributes("aria-label")).toBe("运行概览");
    wrapper.unmount();

    const remounted = mount(App, { global: { stubs: globalStubs } });
    expect(remounted.find(".shell").classes()).toContain("is-collapsed");
    await remounted.get(".sidebar-toggle").trigger("click");
    expect(localStorage.getItem("shipdemo.sidebarCollapsed")).toBe("0");
    remounted.unmount();
    localStorage.removeItem("shipdemo.sidebarCollapsed");
  });

  function mockViewport(initialWidth: number) {
    let width = initialWidth;
    const listeners = new Set<() => void>();
    vi.stubGlobal("matchMedia", (query: string) => {
      const max = Number(/max-width: (\d+)px/.exec(query)?.[1] ?? Infinity);
      return {
        get matches() { return width <= max; },
        addEventListener: (_type: string, listener: () => void) => listeners.add(listener),
        removeEventListener: (_type: string, listener: () => void) => listeners.delete(listener),
      };
    });
    return {
      listeners,
      resize(next: number) {
        width = next;
        listeners.forEach((listener) => listener());
      },
    };
  }
  const drawerStub = {
    props: ["modelValue"],
    emits: ["update:modelValue"],
    template: "<div v-if='modelValue' class='drawer-stub'><slot /></div>",
  };

  it("forces the icon-only sidebar on narrow screens from the first render", () => {
    mockViewport(900);
    localStorage.removeItem("shipdemo.sidebarCollapsed");
    const wrapper = mount(App, { global: { stubs: globalStubs } });
    expect(wrapper.find(".shell").classes()).toContain("is-collapsed");
    // The manual toggle only applies above 1024px.
    expect(wrapper.find(".sidebar-toggle").exists()).toBe(false);
    expect(wrapper.findAll("nav button")[0]!.attributes("aria-label")).toBe("运行概览");
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it("navigates through the mobile drawer and follows viewport changes", async () => {
    const viewport = mockViewport(600);
    const wrapper = mount(App, { global: { stubs: { ...globalStubs, ElDrawer: drawerStub } } });
    expect(wrapper.findAll(".drawer-stub")).toHaveLength(0);
    expect(wrapper.get(".mobile-topbar").text()).toContain("运行概览");

    await wrapper.get(".mobile-menu-button").trigger("click");
    const drawer = wrapper.get(".drawer-stub");
    await drawer.findAll("nav button").find((item) => item.text().includes("文档管理"))!.trigger("click");
    expect(window.location.hash).toBe("#/documents");
    expect(wrapper.find(".drawer-stub").exists()).toBe(false); // closes after navigating

    await wrapper.get(".mobile-menu-button").trigger("click");
    expect(wrapper.find(".drawer-stub").exists()).toBe(true);
    viewport.resize(1300); // leaving mobile closes the drawer and expands the sidebar
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".drawer-stub").exists()).toBe(false);
    expect(wrapper.find(".shell").classes()).not.toContain("is-collapsed");
    expect(wrapper.find(".sidebar-toggle").exists()).toBe(true);

    wrapper.unmount();
    expect(viewport.listeners.size).toBe(0);
    vi.unstubAllGlobals();
  });

  it("keeps working when storage is unavailable", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    const wrapper = mount(App, { global: { stubs: globalStubs } });
    expect(wrapper.find(".shell").classes()).not.toContain("is-collapsed");
    await wrapper.get(".sidebar-toggle").trigger("click");
    expect(wrapper.find(".shell").classes()).toContain("is-collapsed");
    wrapper.unmount();
  });

  it("shows the environment reported by the backend, never a guess", async () => {
    adminStore.serviceToken = "session-token";
    adminStore.credentialRejected = false;
    adminStore.systemInfo = { name: "x", environment: "production", version: "1.0.0" };
    const wrapper = mount(App, { global: { stubs: globalStubs } });
    expect(wrapper.find(".sidebar .env-badge").text()).toBe("生产环境");
    expect(wrapper.find(".sidebar .env-badge").classes()).toContain("env-production");
    expect(wrapper.find(".mobile-topbar .env-badge").text()).toBe("生产环境");

    adminStore.serviceToken = "";
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".env-badge").exists()).toBe(false);
    wrapper.unmount();
    adminStore.systemInfo = null;
  });

  it("opens the legacy group automatically when its view is active", () => {
    window.history.replaceState(null, "", "#/support-review");
    const wrapper = mount(App, { global: { stubs: globalStubs } });
    expect(wrapper.get(".nav-group.is-collapsible .nav-items").attributes("style") ?? "").not.toContain("display: none");
    wrapper.unmount();
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
          TaskCenter: true,
          HealthPanel: { template: "<div>health-panel</div>" },
          ElButton: { template: "<button><slot /></button>" },
          ElIcon: true,
          ElTag: { template: "<span><slot /></span>" },
          ElTooltip: { inheritAttrs: false, template: "<slot />" },
          ElDrawer: true,
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

  it("synchronizes a rejected credential while standalone chat is mounted", async () => {
    adminStore.saveToken("invalid-session-token");
    window.history.replaceState(null, "", "#/support-chat");
    const wrapper = mount(App, { global: { stubs: globalStubs } });

    window.dispatchEvent(new Event(SERVICE_CREDENTIAL_REJECTED_EVENT));
    await wrapper.vm.$nextTick();

    expect(sessionStorage.getItem("rag_service_token")).toBeNull();
    expect(adminStore.serviceToken).toBe("");
    expect(adminStore.credentialRejected).toBe(true);
    expect(wrapper.text()).toContain("需要配置服务凭证");
    expect(wrapper.text()).not.toContain("demo-chat");
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

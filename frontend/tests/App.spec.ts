import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import App from "@/App.vue";

describe("App", () => {
  it("renders the Support Studio navigation", async () => {
    window.history.replaceState(null, "", "#/dashboard");
    const wrapper = mount(App, {
      global: {
        stubs: {
          AdminStudio: { template: "<div>admin-studio</div>" },
          HealthPanel: { template: "<div>health-panel</div>" },
          ElIcon: true,
          ElTag: { template: "<span><slot /></span>" },
        },
      },
    });

    expect(wrapper.text()).toContain("Support Studio");
    expect(wrapper.text()).toContain("发布管理");
    expect(wrapper.text()).toContain("DemoChat 问答测试");
    expect(wrapper.text()).toContain("Engine Routing");
    expect(wrapper.text()).toContain("Logs & Traces");
    expect(wrapper.text()).toContain("模型与系统配置");
    expect(wrapper.text()).not.toContain("Support Configuration");
    expect(wrapper.text()).not.toContain("RAG Configuration");
    expect(wrapper.text()).not.toContain("Support Errors");
    expect(wrapper.text()).not.toContain("RAG Errors");
    expect(wrapper.text()).toContain("Knowledge Base");
    expect(wrapper.text()).toContain("构建在后台");
    expect(wrapper.text()).toContain("DUAL ENGINE");
    expect(wrapper.text()).toContain("admin-studio");
    expect(wrapper.text()).toContain("health-panel");
    expect(wrapper.findAll("nav button")).toHaveLength(10);

    const playground = wrapper.findAll("nav button").find((item) => item.text().includes("RAG Playground"));
    await playground?.trigger("click");
    expect(wrapper.text()).toContain("检索、Prompt 与答案调试");
    expect(window.location.hash).toBe("#/playground");
    wrapper.unmount();
    window.history.replaceState(null, "", "#/dashboard");
  });

  it.each([
    ["support-configuration", "support-profiles", "候选 Profile 审核与原子发布"],
    ["support-errors", "logs", "错误、失败任务与完整链路排查"],
    ["rag-errors", "logs", "错误、失败任务与完整链路排查"],
    ["traces", "logs", "错误、失败任务与完整链路排查"],
    ["rag-configuration", "configuration", "模型、Prompt、RAG 参数与访问设置"],
    ["settings", "configuration", "模型、Prompt、RAG 参数与访问设置"],
  ])("redirects retired #%s hash to #%s", (legacyView, canonicalView, description) => {
    window.history.replaceState(null, "", `#/${legacyView}`);
    const wrapper = mount(App, {
      global: {
        stubs: {
          AdminStudio: { template: "<div>admin-studio</div>" },
          HealthPanel: { template: "<div>health-panel</div>" },
          ElIcon: true,
          ElTag: { template: "<span><slot /></span>" },
        },
      },
    });

    expect(window.location.hash).toBe(`#/${canonicalView}`);
    expect(wrapper.text()).toContain(description);
    wrapper.unmount();
    window.history.replaceState(null, "", "#/dashboard");
  });
});

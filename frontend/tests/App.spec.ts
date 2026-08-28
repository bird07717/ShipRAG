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

    expect(wrapper.text()).toContain("技术支持工作台");
    expect(wrapper.text()).not.toContain("发布管理");
    expect(wrapper.text()).toContain("问答测试（DemoChat）");
    expect(wrapper.text()).toContain("Profile 审核（Profile Review）");
    expect(wrapper.text()).toContain("引擎路由（Engine Routing）");
    expect(wrapper.text()).toContain("日志与链路追踪（Logs & Traces）");
    expect(wrapper.text()).toContain("模型、凭证与系统配置");
    expect(wrapper.text()).not.toContain("Support Configuration");
    expect(wrapper.text()).not.toContain("RAG Configuration");
    expect(wrapper.text()).not.toContain("Support Errors");
    expect(wrapper.text()).not.toContain("RAG Errors");
    expect(wrapper.text()).toContain("知识库（Knowledge Base）");
    expect(wrapper.text()).toContain("双链路知识发布");
    expect(wrapper.text()).toContain("RAG 在技术构建成功后自动切换");
    expect(wrapper.text()).toContain("双引擎（DUAL ENGINE）");
    expect(wrapper.text()).toContain("admin-studio");
    expect(wrapper.text()).toContain("health-panel");
    expect(wrapper.findAll("nav button")).toHaveLength(10);
    expect(wrapper.find(".management-context").exists()).toBe(false);

    const playground = wrapper.findAll("nav button").find((item) => item.text().includes("RAG 调试台"));
    await playground?.trigger("click");
    expect(wrapper.text()).toContain("检索、Prompt 与答案调试");
    expect(window.location.hash).toBe("#/playground");
    wrapper.unmount();
    window.history.replaceState(null, "", "#/dashboard");
  });

  it.each([
    ["support-configuration", "indexes", "技术构建、快照与回收诊断"],
    ["support-profiles", "support-review", "审核自动生成的 Support Profile 草稿"],
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
});

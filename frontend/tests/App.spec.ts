import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import App from "@/App.vue";

describe("App", () => {
  it("renders the Support Studio navigation", async () => {
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
    expect(wrapper.text()).toContain("问题文档配置");
    expect(wrapper.text()).toContain("Support 问答测试");
    expect(wrapper.text()).toContain("Support Configuration");
    expect(wrapper.text()).toContain("RAG Configuration");
    expect(wrapper.text()).toContain("Support Errors");
    expect(wrapper.text()).toContain("RAG Errors");
    expect(wrapper.text()).toContain("Knowledge Base");
    expect(wrapper.text()).toContain("构建在后台");
    expect(wrapper.text()).toContain("SUPPORT");
    expect(wrapper.text()).toContain("admin-studio");
    expect(wrapper.text()).toContain("health-panel");

    const playground = wrapper.findAll("nav button").find((item) => item.text().includes("RAG Playground"));
    await playground?.trigger("click");
    expect(wrapper.text()).toContain("检索、Prompt 与答案调试");
  });
});

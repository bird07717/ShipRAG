import { config, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import RuntimeVersion from "@/components/RuntimeVersion.vue";
import { adminStore } from "@/composables/adminStore";
const frontend = import.meta.env.APP_BUILD_INFO;
beforeEach(() => { adminStore.serviceToken = "test-token"; adminStore.credentialRejected = false; });
afterEach(() => { adminStore.systemInfo = null; adminStore.serviceToken = ""; });
// The compact (collapsed sidebar) branch wraps the button in a tooltip.
const tooltipStub = { inheritAttrs: false, props: ["content"], template: "<div class='tooltip-stub' :data-content='content'><slot /></div>" };
beforeEach(() => { config.global.stubs = { ...config.global.stubs, ElTooltip: tooltipStub }; });
describe("actual runtime version", () => {
  it("summarizes versions in a tooltip when the sidebar is collapsed", () => {
    adminStore.systemInfo = { name: "test", environment: "test", version: "1.0.0", build: { commit: "c".repeat(40), built_at: null, dirty: false } };
    const wrapper = mount(RuntimeVersion, { props: { compact: true } });
    expect(wrapper.find("details").exists()).toBe(false);
    const summary = wrapper.get(".tooltip-stub").attributes("data-content")!;
    expect(summary).toContain("运行版本");
    expect(summary).toContain("后端 cccccccc");
    expect(wrapper.get("button").attributes("aria-label")).toBe(summary);
    expect(wrapper.find(".dot").exists()).toBe(Boolean(frontend.commit));
    wrapper.unmount();
  });
  it("does not label untagged servers as current", () => {
    adminStore.systemInfo = { name: "test", environment: "test", version: "1.0.0" };
    const wrapper = mount(RuntimeVersion);
    expect(wrapper.text()).toContain("未标注");
    expect(wrapper.text()).toContain("不能确认是否为最新版");
    wrapper.unmount();
  });
  it("shows immutable backend build identity and mismatch warning", () => {
    adminStore.systemInfo = { name: "test", environment: "test", version: "1.0.0", build: { commit: "b".repeat(40), built_at: "2026-10-03T00:00:00Z", dirty: false } };
    const wrapper = mount(RuntimeVersion);
    expect(wrapper.text()).toContain("bbbbbbbb");
    expect(wrapper.text()).toContain("2026-10-03");
    if (frontend.commit) expect(wrapper.text()).toContain("前后端不一致");
    wrapper.unmount();
  });
  it.each([false, true])("explains matching commit validation (dirty=%s)", (dirty) => {
    adminStore.systemInfo = { name: "test", environment: "test", version: "1.0.0", build: { commit: frontend.commit, built_at: null, dirty } };
    const wrapper = mount(RuntimeVersion);
    expect(wrapper.text()).not.toContain("前后端不一致");
    expect(wrapper.text()).toContain(frontend.commit ? frontend.dirty || dirty ? "不保证代码完全一致" : "不代表已通过生产验收" : "不能确认是否为最新版");
    wrapper.unmount();
  });
});

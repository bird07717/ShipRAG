import { mount } from "@vue/test-utils";
import ElementPlus from "element-plus";
import { describe, expect, it } from "vitest";

import ChatComposer from "@/components/ui/ChatComposer.vue";

function mountComposer(props: Record<string, unknown> = {}) {
  return mount(ChatComposer, {
    props: { modelValue: "", ...props },
    global: { plugins: [ElementPlus] },
  });
}

describe("ChatComposer", () => {
  it("emits send on Enter and inserts nothing on Shift+Enter", async () => {
    const wrapper = mountComposer({ modelValue: "hello" });
    const textarea = wrapper.get("textarea");

    await textarea.trigger("keydown", { key: "Enter", shiftKey: true });
    expect(wrapper.emitted("send")).toBeUndefined();

    await textarea.trigger("keydown", { key: "Enter" });
    expect(wrapper.emitted("send")).toHaveLength(1);

    await textarea.trigger("keydown", { key: "Enter", ctrlKey: true });
    expect(wrapper.emitted("send")).toHaveLength(2);
  });

  it("emits update:modelValue while typing", async () => {
    const wrapper = mountComposer();
    await wrapper.get("textarea").setValue("问题");
    expect(wrapper.emitted("update:modelValue")?.at(-1)).toEqual(["问题"]);
  });

  it("keeps send disabled without input and shows stop while loading", async () => {
    const wrapper = mountComposer({ modelValue: "" });
    const send = wrapper.get(".send-button");
    expect((send.element as HTMLButtonElement).disabled).toBe(true);
    await send.trigger("click");
    expect(wrapper.emitted("send")).toBeUndefined();

    await wrapper.setProps({ modelValue: "hi", loading: true });
    expect(wrapper.text()).toContain("停止生成");
    const stop = wrapper.get(".composer-actions button");
    await stop.trigger("click");
    expect(wrapper.emitted("stop")).toHaveLength(1);
    expect(wrapper.emitted("send")).toBeUndefined();
  });
});

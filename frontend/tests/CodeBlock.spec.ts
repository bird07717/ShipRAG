import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import CodeBlock from "@/components/ui/CodeBlock.vue";

describe("CodeBlock", () => {
  it("renders a safe empty value when an optional payload field is absent", () => {
    const wrapper = mount(CodeBlock, {
      props: { code: null },
      global: { stubs: { "el-button": true } },
    });

    expect(wrapper.get("pre").text()).toBe("");
  });
});

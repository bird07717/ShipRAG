import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import HealthPanel from "@/components/HealthPanel.vue";

const readyPayload = {
  status: "ready",
  checks: {
    postgres: { status: "ok", latency_ms: 2.1, version: "17.6" },
    redis: { status: "ok", latency_ms: 1.2, version: "8.8.1" },
    minio: { status: "ok", latency_ms: 3.4 },
  },
};

describe("HealthPanel", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders dependency readiness returned by the backend", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, status: 200, json: () => readyPayload }),
    );

    const wrapper = mount(HealthPanel, {
      global: {
        stubs: {
          ElCard: { template: "<section><slot name='header' /><slot /></section>" },
          ElButton: { template: "<button @click='$emit(\"click\")'><slot /></button>" },
          ElTag: { template: "<span><slot /></span>" },
          ElAlert: true,
          ElSkeleton: true,
        },
      },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("服务就绪");
    expect(wrapper.text()).toContain("PostgreSQL");
    expect(wrapper.text()).toContain("v17.6");
  });

  it("shows a safe message when the backend cannot be reached", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network unavailable")));

    const wrapper = mount(HealthPanel, {
      global: {
        stubs: {
          ElCard: { template: "<section><slot name='header' /><slot /></section>" },
          ElButton: { template: "<button><slot /></button>" },
          ElTag: true,
          ElAlert: { props: ["title"], template: "<p>{{ title }}</p>" },
          ElSkeleton: true,
        },
      },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("network unavailable");
  });

  it("renders a not-ready dependency detail", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 503,
        json: () => ({
          status: "not_ready",
          checks: {
            postgres: { status: "error", latency_ms: 10, detail: "dependency unavailable" },
            redis: { status: "ok", latency_ms: 1 },
            minio: { status: "ok", latency_ms: 2 },
          },
        }),
      }),
    );

    const wrapper = mount(HealthPanel, {
      global: {
        stubs: {
          ElCard: { template: "<section><slot name='header' /><slot /></section>" },
          ElButton: { template: "<button><slot /></button>" },
          ElTag: { template: "<span><slot /></span>" },
          ElAlert: true,
          ElSkeleton: true,
        },
      },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("依赖未就绪");
    expect(wrapper.text()).toContain("dependency unavailable");
  });
});

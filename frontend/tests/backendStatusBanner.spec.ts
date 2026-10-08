import ElementPlus from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { backendConnectivity, markBackendUnreachable } from "@/api/connectivity";
import BackendStatusBanner from "@/components/BackendStatusBanner.vue";

function jsonResponse(status: number): Response {
  return new Response("{}", { status, headers: { "content-type": "application/json" } });
}

describe("BackendStatusBanner", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    backendConnectivity.status = "online";
    backendConnectivity.offlineSince = null;
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    backendConnectivity.status = "unknown";
  });

  it("stays hidden while the backend is reachable and probes nothing", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(BackendStatusBanner, { global: { plugins: [ElementPlus] } });
    await vi.advanceTimersByTimeAsync(60_000);
    expect(wrapper.find(".system-banner").exists()).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("retries with backoff while offline and announces the reconnection", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("", { status: 500 }));
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(BackendStatusBanner, { global: { plugins: [ElementPlus] } });

    markBackendUnreachable("Failed to fetch");
    await flushPromises();
    expect(wrapper.text()).toContain("无法连接后端服务");
    expect(wrapper.text()).toContain("3 秒后自动重试");

    await vi.advanceTimersByTimeAsync(3_000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0]![0])).toMatch(/\/health\/live$/);
    expect(wrapper.text()).toContain("5 秒后自动重试"); // backoff grows

    fetchMock.mockResolvedValue(jsonResponse(200));
    await vi.advanceTimersByTimeAsync(5_000);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(backendConnectivity.status).toBe("online");
    expect(wrapper.emitted("reconnected")).toHaveLength(1);
    expect(wrapper.text()).toContain("已重新连接后端服务");

    await vi.advanceTimersByTimeAsync(4_000);
    expect(wrapper.find(".system-banner").exists()).toBe(false);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(fetchMock).toHaveBeenCalledTimes(2); // loop stopped once online
    wrapper.unmount();
  });

  it("retries immediately on demand and stops probing after unmount", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("down"));
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(BackendStatusBanner, { global: { plugins: [ElementPlus] } });
    markBackendUnreachable("down");
    await flushPromises();

    await wrapper.findAll("button").find((button) => button.text().includes("立即重试"))!.trigger("click");
    await flushPromises();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    wrapper.unmount();
    await vi.advanceTimersByTimeAsync(120_000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("stops retrying when a page request reaches the backend first", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("down"));
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(BackendStatusBanner, { global: { plugins: [ElementPlus] } });
    markBackendUnreachable("down");
    await flushPromises();

    backendConnectivity.status = "online"; // e.g. a normal API call succeeded
    await flushPromises();
    await vi.advanceTimersByTimeAsync(120_000);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(wrapper.emitted("reconnected")).toHaveLength(1);
    wrapper.unmount();
  });
});

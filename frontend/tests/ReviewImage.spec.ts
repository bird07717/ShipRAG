import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ReviewImage from "@/components/ReviewImage.vue";
import { fetchImageBlobUrl } from "@/api/chat";
import { ApiRequestError } from "@/api/serviceCredential";
vi.mock("@/api/chat", () => ({ fetchImageBlobUrl: vi.fn() }));
const wrappers: ReturnType<typeof mount>[] = [];
function image() {
  const wrapper = mount(ReviewImage, { props: { assetId: "asset-a", caption: "接线图" }, global: { stubs: { ElImageViewer: { props: ["urlList"], template: '<div class="viewer">{{ urlList }}<button @click="$emit(\'close\')">关闭预览</button></div>' } } } });
  wrappers.push(wrapper); return wrapper;
}
beforeEach(() => {
  vi.mocked(fetchImageBlobUrl).mockReset().mockResolvedValue("blob:image-a");
  vi.stubGlobal("URL", Object.assign(URL, { revokeObjectURL: vi.fn() }));
});
afterEach(() => { wrappers.splice(0).forEach((wrapper) => wrapper.unmount()); vi.unstubAllGlobals(); });
describe("shared authenticated image viewer", () => {
  it("opens an accessible preview using the same blob without another request", async () => {
    const wrapper = image(); await flushPromises();
    expect(wrapper.get("img").attributes("alt")).toBe("接线图");
    expect(fetchImageBlobUrl).toHaveBeenCalledWith("asset-a", expect.any(AbortSignal));
    await wrapper.get(".image-button").trigger("click");
    expect(wrapper.get(".viewer").text()).toContain("blob:image-a");
    expect(fetchImageBlobUrl).toHaveBeenCalledTimes(1);
    await wrapper.get(".viewer button").trigger("click");
    expect(wrapper.find(".viewer").exists()).toBe(false);
    wrapper.unmount(); expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:image-a");
  });
  it.each([401, 403, 404, 410])("does not repeatedly retry an HTTP %s rejection", async (status) => {
    vi.mocked(fetchImageBlobUrl).mockRejectedValue(new ApiRequestError("failed", status));
    const wrapper = image(); await flushPromises();
    expect(wrapper.find(".retry-button").exists()).toBe(false);
    expect(wrapper.text()).not.toContain("图片加载中");
    expect(wrapper.text()).toContain(status === 401 ? "凭证失效" : status === 403 ? "无权查看" : "保留期限");
  });
  it("retries transient failures and decoding failures", async () => {
    vi.mocked(fetchImageBlobUrl).mockRejectedValueOnce(new Error("offline"));
    const wrapper = image(); await flushPromises();
    await wrapper.get(".retry-button").trigger("click"); await flushPromises();
    expect(wrapper.get("img").attributes("src")).toBe("blob:image-a");
    await wrapper.get("img").trigger("error");
    expect(wrapper.text()).toContain("无法显示");
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:image-a");
  });
  it("ignores and releases stale loads when the asset changes", async () => {
    let resolve!: (value: string) => void;
    vi.mocked(fetchImageBlobUrl).mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
    const wrapper = image();
    const signal = vi.mocked(fetchImageBlobUrl).mock.calls[0]?.[1];
    await wrapper.setProps({ assetId: "asset-b" }); await flushPromises();
    expect(signal?.aborted).toBe(true);
    resolve("blob:old"); await flushPromises();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:old");
    expect(wrapper.get("img").attributes("src")).toBe("blob:image-a");
  });
  it("loads only when visible and disconnects its observer", async () => {
    let callback!: IntersectionObserverCallback;
    const disconnect = vi.fn();
    vi.stubGlobal("IntersectionObserver", class {
      constructor(cb: IntersectionObserverCallback) { callback = cb; }
      observe = vi.fn(); disconnect = disconnect;
    });
    const wrapper = image();
    expect(fetchImageBlobUrl).not.toHaveBeenCalled();
    callback([{ isIntersecting: true }] as IntersectionObserverEntry[], {} as IntersectionObserver);
    callback([{ isIntersecting: true }] as IntersectionObserverEntry[], {} as IntersectionObserver);
    await flushPromises();
    expect(fetchImageBlobUrl).toHaveBeenCalledTimes(1);
    wrapper.unmount(); expect(disconnect).toHaveBeenCalled();
  });
});

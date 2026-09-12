import ElementPlus from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import type { VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { adminApi } from "@/api/admin";
import { reviewsApi } from "@/api/reviews";
import { fetchImageBlobUrl } from "@/api/chat";
import AnswerReviewDetail from "@/components/AnswerReviewDetail.vue";
import AnswerContent from "@/components/AnswerContent.vue";
import DocumentPreview from "@/components/DocumentPreview.vue";
import ReviewImage from "@/components/ReviewImage.vue";
import AnswerReviewsView from "@/views/AnswerReviewsView.vue";
import type { AnswerDetail, AnswerRecord } from "@/types/reviews";
import type { DocumentItem } from "@/types/admin";

vi.mock("@/api/chat", () => ({ fetchImageBlobUrl: vi.fn() }));
function sample(kb = "kb-a"): AnswerDetail {
  return {
    snapshot: { knowledge_id: kb, request_id: "request-a", created_at: "2026-09-11", status: "COMPLETED", question: "如何检查设备", external_user_id: "user-1", conversation_id: "conv-a", trace_id: "trace-a", response: { answer: "**《原标题》**", response_type: "DOC_DELIVERED", content: [{ type: "text", text: "原始第一步" }] }, error: {}, history: [], history_complete: true, diagnostics: { index_id: "index-old" } },
    review: null, retests: [],
  };
}
function saved(): AnswerDetail {
  const result = sample();
  result.review = { id: "review-a", knowledge_id: "kb-a", request_id: "request-a", revision: 2, category: "WRONG_DOCUMENT", status: "OPEN", expected_document_id: "doc-a", expected_document_title: "原标题", expectation: "交付检查步骤", notes: "", snapshot: result.snapshot };
  return result;
}
const wrappers: Array<{ unmount: () => void }> = [];
function editor() {
  const wrapper = mount(AnswerReviewDetail, { props: { knowledgeId: "kb-a", requestId: "request-a" }, global: { plugins: [ElementPlus] } });
  wrappers.push(wrapper); return wrapper;
}
function button(wrapper: VueWrapper, label: string) {
  const found = wrapper.findAll("button").find((item) => item.text() === label);
  if (!found) throw new Error(`Missing ${label}`);
  return found;
}
beforeEach(() => {
  vi.spyOn(reviewsApi, "detail").mockResolvedValue(sample());
  vi.spyOn(adminApi, "listDocuments").mockResolvedValue([{ id: "doc-a", display_name: "当前标题" } as DocumentItem]);
  vi.mocked(fetchImageBlobUrl).mockResolvedValue("blob:image");
  vi.stubGlobal("URL", Object.assign(URL, { revokeObjectURL: vi.fn(), createObjectURL: vi.fn(() => "blob:test") }));
});
afterEach(() => { wrappers.splice(0).forEach((item) => item.unmount()); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("answer review workflow", () => {
  it("shows the historical body and saves to the bound KB with its revision", async () => {
    const save = vi.spyOn(reviewsApi, "save").mockResolvedValue(saved());
    const wrapper = editor(); await flushPromises();
    expect(wrapper.text()).toContain("原始第一步");
    expect(button(wrapper, "按当前配置复测").attributes("disabled")).toBeDefined();
    await wrapper.get('textarea[aria-label="预期表现"]').setValue("应该回答检查步骤");
    await button(wrapper, "保存标记").trigger("click"); await flushPromises();
    expect(save).toHaveBeenCalledWith("kb-a", "request-a", expect.objectContaining({ revision: 0, expectation: "应该回答检查步骤" }));
    expect(wrapper.emitted("changed")).toHaveLength(1);
    expect(button(wrapper, "按当前配置复测").attributes("disabled")).toBeUndefined();
  });

  it("preserves an unsaved draft on conflict and forbids retesting it", async () => {
    vi.mocked(reviewsApi.detail).mockResolvedValue(saved());
    vi.spyOn(reviewsApi, "save").mockRejectedValue(new Error("标记已被修改，请重新加载"));
    const wrapper = editor(); await flushPromises();
    await wrapper.get('textarea[aria-label="处理备注"]').setValue("我的修改");
    expect(button(wrapper, "按当前配置复测").attributes("disabled")).toBeDefined();
    await button(wrapper, "保存标记").trigger("click"); await flushPromises();
    expect(wrapper.text()).toContain("标记已被修改");
    expect((wrapper.get('textarea[aria-label="处理备注"]').element as HTMLTextAreaElement).value).toBe("我的修改");
  });

  it("retests the saved revision and keeps the original result visible", async () => {
    vi.mocked(reviewsApi.detail).mockResolvedValue(saved());
    const retest = vi.spyOn(reviewsApi, "retest").mockResolvedValue({ id: "run-a", review_id: "review-a", review_revision: 2, status: "COMPLETED", expectation: { expected_document_id: "doc-a", expected_document_title: "原标题", expectation: "交付检查步骤" }, turns: [{ question: "如何检查设备", response: { answer: "新回答", references: [{ document_id: "doc-a", title: "当前标题" }] }, diagnostics: { index_id: "new-index" } }], error: null, created_at: "today" });
    const wrapper = editor(); await flushPromises();
    await button(wrapper, "按当前配置复测").trigger("click"); await flushPromises();
    expect(retest).toHaveBeenCalledWith("kb-a", "review-a", 2, expect.any(String));
    expect(wrapper.text()).toContain("原始第一步");
    expect(wrapper.text()).toContain("新回答");
    expect(wrapper.text()).toContain("正文仍需人工核验");
  });

  it("renders candidate titles from the historical routing snapshot", async () => {
    const result = sample();
    result.snapshot.diagnostics = { index_id: "index-old", engine: "RAG", retrieval_result: { doc_routing: { decision: "CLARIFY", doc_scores: [{ document_id: "doc-a", title: "当时的候选标题" }] } }, rerank_result: { candidates: [{ document_id: "doc-a", chunk_id: "chunk-a", rerank_score: 0.72 }] } };
    vi.mocked(reviewsApi.detail).mockResolvedValue(result);
    const wrapper = editor(); await flushPromises();
    expect(wrapper.text()).toContain("当时的候选标题");
    expect(wrapper.text()).toContain("请用户澄清");
    expect(wrapper.text()).toContain("0.72");
    expect(wrapper.text()).toContain("chunk-a");
  });

  it("does not update or emit a late save after the detail is closed", async () => {
    let resolve!: (value: AnswerDetail) => void;
    vi.spyOn(reviewsApi, "save").mockReturnValue(new Promise((done) => { resolve = done; }));
    const wrapper = editor(); await flushPromises();
    await button(wrapper, "保存标记").trigger("click");
    wrapper.unmount(); resolve(saved()); await flushPromises();
    expect(wrapper.emitted("changed")).toBeUndefined();
  });

  it("inspects only same-KB references against the original index", async () => {
    const result = sample();
    result.snapshot.response.references = [{ document_id: "doc-a", title: "原标题" }, { document_id: "other", title: "其他库文档" }];
    vi.mocked(reviewsApi.detail).mockResolvedValue(result);
    const inspect = vi.spyOn(adminApi, "listElements").mockResolvedValue({ index_id: "index-old", items: [] });
    const wrapper = editor(); await flushPromises();
    await button(wrapper, "原标题").trigger("click"); await flushPromises();
    expect(inspect).toHaveBeenCalledWith("doc-a", "index-old");
    await button(wrapper, "其他库文档").trigger("click"); await flushPromises();
    expect(inspect).toHaveBeenCalledTimes(1);
    expect(wrapper.text()).toContain("文档不在当前知识库或已删除");
  });

  it("disables reliable replay when the original context is incomplete", async () => {
    const result = saved(); result.snapshot.history_complete = false;
    vi.mocked(reviewsApi.detail).mockResolvedValue(result);
    const wrapper = editor(); await flushPromises();
    expect(button(wrapper, "按当前配置复测").attributes("disabled")).toBeDefined();
    expect(wrapper.text()).toContain("无法可靠复测");
  });

  it("ignores a late list response after changing knowledge bases", async () => {
    let resolve!: (value: { items: AnswerRecord[]; total: number }) => void;
    vi.spyOn(reviewsApi, "list").mockImplementation((kb) => kb === "kb-a" ? new Promise((done) => { resolve = done; }) : Promise.resolve({ items: [{ request_id: "b", question: "B库问题" } as AnswerRecord], total: 1 }));
    const wrapper = mount(AnswerReviewsView, { props: { knowledgeId: "kb-a" }, global: { plugins: [ElementPlus] } }); wrappers.push(wrapper);
    await wrapper.setProps({ knowledgeId: "kb-b" }); await flushPromises();
    resolve({ items: [{ request_id: "a", question: "A库问题" } as AnswerRecord], total: 1 }); await flushPromises();
    expect(wrapper.text()).toContain("B库问题");
    expect(wrapper.text()).not.toContain("A库问题");
  });

  it("opens the answer detail drawer with a splitter-compatible size", async () => {
    vi.spyOn(reviewsApi, "list").mockResolvedValue({
      items: [{ request_id: "request-a", question: "如何检查设备" } as AnswerRecord],
      total: 1,
    });
    const wrapper = mount(AnswerReviewsView, {
      props: { knowledgeId: "kb-a" },
      global: { plugins: [ElementPlus] },
    });
    wrappers.push(wrapper);
    await flushPromises();

    const drawer = wrapper.findComponent({ name: "ElDrawer" });
    expect(drawer.props("modelValue")).toBe(false);
    expect(drawer.props("size")).toMatch(/^\d+(?:\.\d+)?(?:px|%)$/);

    await button(wrapper, "查看").trigger("click");
    await flushPromises();

    expect(drawer.props("modelValue")).toBe(true);
    expect(wrapper.find('[role="dialog"][aria-label="Assistant 回答与优化"]').exists()).toBe(true);
    expect(reviewsApi.detail).toHaveBeenCalledWith("kb-a", "request-a", expect.any(AbortSignal));
  });
});

describe("historical answer and document rendering", () => {
  it("renders title, blocks and tables without executing source markup", () => {
    const wrapper = mount(AnswerContent, { props: { payload: { answer: "**《标题》**", response_type: "DOC_DELIVERED", content: [{ type: "text", text: '<script>alert(1)</script>\n正文' }, { type: "table", text: '|列|\n|---|\n|值|' }] } }, global: { plugins: [ElementPlus] } }); wrappers.push(wrapper);
    expect(wrapper.find("strong").text()).toBe("《标题》");
    expect(wrapper.find("script").exists()).toBe(false);
    expect(wrapper.find("table").text()).toContain("值");
  });
  it("shows original first line and parsed order in the inspection preview", () => {
    const wrapper = mount(DocumentPreview, { props: { elements: [{ id: "b", sequence_no: 2, element_type: "TEXT", content: "第一步" }, { id: "a", sequence_no: 1, element_type: "TEXT", content: "原标题" }] } }); wrappers.push(wrapper);
    const text = wrapper.findAll(".original-text").map((item) => item.text());
    expect(text).toEqual(["原标题", "第一步"]);
  });
  it("releases an image URL if loading finishes after unmount", async () => {
    let resolve!: (value: string) => void;
    vi.mocked(fetchImageBlobUrl).mockReturnValue(new Promise((done) => { resolve = done; }));
    const wrapper = mount(ReviewImage, { props: { assetId: "asset-a" } });
    wrapper.unmount(); resolve("blob:late"); await flushPromises();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:late");
  });
});

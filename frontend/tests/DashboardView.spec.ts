import ElementPlus from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { adminApi } from "@/api/admin";
import { adminStore } from "@/composables/adminStore";
import DashboardView from "@/views/DashboardView.vue";
import type { MetricsOverview, MetricsTimeseriesPoint, TraceSummary } from "@/types/admin";

const overview: MetricsOverview = {
  range: { from: "2026-08-14T00:00:00Z", to: "2026-08-21T00:00:00Z", mode: "CHAT" },
  total_traces: 120,
  completed: 110,
  failed: 10,
  cancelled: 0,
  response_type_counts: { ANSWERED: 60, UNCONFIRMED: 40, OUT_OF_SCOPE: 5, DOC_DELIVERED: 5 },
  answer_mode_counts: { PRODUCT_KNOWLEDGE: 30, PRODUCT_EXPLAINED: 20, PRODUCT_GENERAL: 10 },
  answer_basis_traces: 60,
  product_knowledge_rate: 0.5,
  product_explained_rate: 0.3333,
  product_general_rate: 0.1667,
  classified_traces: 110,
  in_scope_traces: 105,
  direct_answer_rate: 0.619,
  needs_information_rate: 0.381,
  out_of_scope_rate: 0.0455,
  doc_delivered_rate: 0.0455,
  failure_rate: 0.0833,
  citation_missing_rate: 0.02,
  rerank_degraded_rate: 0.01,
  unique_users: 12,
  latency: { p50_ms: 900, p95_ms: 2500, p99_ms: 4000, avg_ms: 1100, samples: 20 },
  tokens: { llm_prompt: 5000, llm_completion: 1000, llm_total: 6000, rerank_total: 300 },
};

const dailyPoint: MetricsTimeseriesPoint = {
  bucket: "2026-08-20T00:00:00Z",
  queries: 20,
  failed: 1,
  answered: 12,
  unconfirmed: 5,
  out_of_scope: 1,
  doc_delivered: 2,
  direct_answer_rate: 0.7368,
  needs_information_rate: 0.2632,
  out_of_scope_rate: 0.05,
  p95_ms: 2200,
  llm_tokens: 800,
  rerank_tokens: 40,
};

const trace: TraceSummary = {
  trace_id: "t-1",
  mode: "CHAT",
  kb_id: "kb-1",
  index_id: "idx-1",
  question: "如何配置？",
  status: "COMPLETED",
  user_id: "mobile-user-1",
  response_type: "UNCONFIRMED",
  answer_mode: "UNCONFIRMED",
  latency: { total_ms: 700 },
  error: {},
  created_at: "2026-08-20T10:00:00Z",
};

const wrappers: ReturnType<typeof mount>[] = [];
function mountDashboard() {
  const wrapper = mount(DashboardView, { global: { plugins: [ElementPlus] } });
  wrappers.push(wrapper);
  return wrapper;
}

// Trend windows are aligned to "now"; pin it so bucket fills are deterministic.
const NOW = new Date("2026-08-21T12:30:00Z");

describe("DashboardView metrics", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
    adminStore.serviceToken = "test-service-token";
    adminStore.credentialRejected = false;
    vi.spyOn(adminApi, "getAssistantMetrics").mockResolvedValue({ days: 7, sources: [], idempotency: { assistant_protected: 0, assistant_replays: 0, internal_protected: 0 }, retention: { active: 0, expired: 0, retention_days: 7 }, top_users: [], turn_error_codes: [], request_error_codes: [], running: {} } as never);
    vi.spyOn(adminApi, "getKnowledgeAssistant").mockImplementation(async (id) => ({ knowledge_id: id, assistant_online: false, suggestions: [], revision: 0, ready: false, blockers: ["需要确认文档"] }));
  });
  afterEach(() => {
    wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
    vi.restoreAllMocks();
    vi.useRealTimers();
    adminStore.selectedKnowledgeId = "";
    adminStore.serviceToken = "";
    adminStore.credentialRejected = false;
    adminStore.systemInfo = null;
  });

  it("renders overview cards, trend chart and drilldown from metrics", async () => {
    vi.spyOn(adminApi, "getMetricsOverview").mockResolvedValue(overview);
    vi.spyOn(adminApi, "getMetricsTimeseries").mockResolvedValue([dailyPoint]);
    vi.spyOn(adminApi, "listUnconfirmedTraces").mockResolvedValue([trace]);
    adminStore.selectedKnowledgeId = "kb-1";

    const wrapper = mountDashboard();
    await flushPromises();

    expect(adminApi.getMetricsOverview).toHaveBeenCalledWith(
      expect.objectContaining({
        knowledgeId: "kb-1",
        engine: "RAG",
        source: "ASSISTANT_API",
        from: expect.any(String),
        to: expect.any(String),
      }),
    );
    expect(adminApi.listUnconfirmedTraces).toHaveBeenCalledWith(
      "kb-1",
      expect.objectContaining({
        engine: "RAG",
        source: "ASSISTANT_API",
        excludeLowInformation: true,
      }),
    );
    expect(wrapper.text()).toContain("120"); // total queries
    expect(wrapper.text()).toContain("61.9%"); // direct-answer rate
    expect(wrapper.text()).toContain("近 7 天暂无外部调用"); // API has no Assistant source rows
    expect(wrapper.text()).not.toContain("调用数据未加载");
    expect(wrapper.text()).toContain("38.1%"); // needs-information rate
    expect(wrapper.text()).toContain("4.5%"); // out-of-scope rate
    expect(wrapper.text()).toContain("回答依据分布");
    expect(wrapper.text()).toContain("文档知识");
    expect(wrapper.text()).toContain("50.0%");
    expect(wrapper.text()).toContain("文档 + 通用解释");
    expect(wrapper.text()).toContain("33.3%");
    expect(wrapper.text()).toContain("通用工程扩展");
    expect(wrapper.text()).toContain("16.7%");
    expect(wrapper.text()).toContain("2,500 ms"); // p95
    expect(wrapper.find("svg.chart-svg").exists()).toBe(true);
    expect(wrapper.text()).toContain("如何配置？"); // drilldown question
    expect(wrapper.text()).toContain("mobile-user-1");
    expect(wrapper.findAll(".metric-card")).toHaveLength(4);
    expect(wrapper.find("details.advanced-details:not(.review-details)").attributes("open")).toBeUndefined();
    expect(wrapper.text()).toContain("直接回答率不是答案正确率");
  });

  it("collapses manual review by default and preserves its actions after expansion", async () => {
    vi.spyOn(adminApi, "getMetricsOverview").mockResolvedValue(overview);
    vi.spyOn(adminApi, "getMetricsTimeseries").mockResolvedValue([]);
    vi.spyOn(adminApi, "listUnconfirmedTraces").mockResolvedValue([trace]);
    const openTrace = vi.spyOn(adminStore, "openTrace").mockResolvedValue();
    const navigate = vi.spyOn(adminStore, "navigate").mockImplementation(() => {});
    const wrapper = mountDashboard();
    await flushPromises();

    const review = wrapper.get("details.review-details");
    expect((review.element as HTMLDetailsElement).open).toBe(false);
    expect(review.get("summary").text()).toContain("值得人工复核的问题");
    // jsdom does not implement the browser's native summary click toggle.
    (review.element as HTMLDetailsElement).open = true;
    await review.trigger("toggle");
    expect(review.attributes("open")).toBeDefined();
    await review.get(".el-table__row").trigger("click");
    expect(openTrace).toHaveBeenCalledWith(trace.trace_id);
    await review.findAll("button").find((button) => button.text().includes("进入回答复核"))!.trigger("click");
    expect(navigate).toHaveBeenCalledWith("logs", "tab=assistant-answers");
  });

  it("opens a bucket drilldown dialog with that bucket's traces on bar click", async () => {
    vi.spyOn(adminApi, "getMetricsOverview").mockResolvedValue(overview);
    vi.spyOn(adminApi, "getMetricsTimeseries").mockResolvedValue([dailyPoint]);
    vi.spyOn(adminApi, "listUnconfirmedTraces").mockResolvedValue([]);
    const listTraces = vi
      .spyOn(adminApi, "listTraces")
      .mockResolvedValue([trace]);

    const wrapper = mountDashboard();
    await flushPromises();

    const bar = wrapper.find('svg.chart-svg rect.bar-hit[data-bucket="2026-08-20T00:00:00Z"]');
    expect(bar.exists()).toBe(true);
    await bar.trigger("click");
    await flushPromises();

    // daily bucket 2026-08-20T00:00:00Z -> range [bucket, +1d)
    expect(listTraces).toHaveBeenCalledWith(undefined, {
      from: "2026-08-20T00:00:00Z",
      to: "2026-08-21T00:00:00.000Z",
      engine: "RAG",
      source: "ASSISTANT_API",
      limit: 100,
    });
    expect(wrapper.text()).toContain("如何配置？"); // dialog lists the trace
  });

  it("opens failed turns with the displayed overview's source and exact time window", async () => {
    vi.spyOn(adminApi, "getMetricsOverview").mockResolvedValue(overview);
    vi.spyOn(adminApi, "getMetricsTimeseries").mockResolvedValue([]);
    vi.spyOn(adminApi, "listUnconfirmedTraces").mockResolvedValue([]);
    const navigate = vi.spyOn(adminStore, "navigate").mockImplementation(() => {});
    const wrapper = mountDashboard();
    await flushPromises();

    await wrapper.findAll("button").find((item) => item.text().includes("技术请求执行失败"))!.trigger("click");
    const [view, query] = navigate.mock.calls[0]!;
    expect(view).toBe("logs");
    expect(Object.fromEntries(new URLSearchParams(query))).toEqual({
      tab: "failed-turns", engine: "RAG", source: "ASSISTANT_API",
      from: overview.range.from, to: overview.range.to,
    });
  });

  it("refreshes backend build identity manually, without polling system info", async () => {
    vi.useRealTimers(); // replace the Date-only fake from beforeEach
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    vi.spyOn(adminApi, "getMetricsOverview").mockResolvedValue(overview);
    vi.spyOn(adminApi, "getMetricsTimeseries").mockResolvedValue([]);
    vi.spyOn(adminApi, "listUnconfirmedTraces").mockResolvedValue([]);
    vi.spyOn(adminStore, "refreshOperationalState").mockResolvedValue();
    const systemInfo = { build: { commit: "b".repeat(40), built_at: "2026-10-05T00:00:00Z", dirty: false } };
    const getSystemInfo = vi.spyOn(adminApi, "getSystemInfo").mockResolvedValue(systemInfo as never);
    adminStore.systemInfo = { build: { commit: "a".repeat(40) } } as never;
    const wrapper = mountDashboard();
    await flushPromises();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(getSystemInfo).not.toHaveBeenCalled();

    await wrapper.findAll("button").find((item) => item.text() === "刷新状态")!.trigger("click");
    await flushPromises();
    expect(getSystemInfo).toHaveBeenCalledTimes(1);
    expect(adminStore.systemInfo).toEqual(systemInfo);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(getSystemInfo).toHaveBeenCalledTimes(1);
  });

  it("requests 24 whole hourly buckets for the 24-hour range and labels them by hour", async () => {
    vi.spyOn(adminApi, "getMetricsOverview").mockResolvedValue(overview);
    const timeseries = vi.spyOn(adminApi, "getMetricsTimeseries").mockResolvedValue([dailyPoint]);
    vi.spyOn(adminApi, "listUnconfirmedTraces").mockResolvedValue([]);

    const wrapper = mountDashboard();
    await flushPromises();
    // 7 whole server days (today included), starting at a bucket boundary.
    expect(timeseries).toHaveBeenLastCalledWith(expect.objectContaining({
      granularity: "day", from: "2026-08-15T00:00:00.000Z", to: NOW.toISOString(), engine: "RAG", source: "ASSISTANT_API",
    }));
    expect(wrapper.findAll("rect.bar-hit")).toHaveLength(7);
    expect(wrapper.find(".trend-summary").text()).toContain("不含今天");
    const overviewCalls = vi.mocked(adminApi.getMetricsOverview).mock.calls.length;

    timeseries.mockResolvedValue([
      { ...dailyPoint, bucket: "2026-08-21T05:00:00Z" },
      { ...dailyPoint, bucket: "2026-08-21T06:00:00Z" },
    ]);
    await wrapper.get('input[value="24h"]').setValue(true);
    await flushPromises();

    expect(timeseries).toHaveBeenLastCalledWith(expect.objectContaining({
      granularity: "hour", from: "2026-08-20T13:00:00.000Z", to: NOW.toISOString(),
    }));
    // Changing the trend range only reloads the trend, not the 7-day overview.
    expect(adminApi.getMetricsOverview).toHaveBeenCalledTimes(overviewCalls);
    expect(wrapper.findAll("rect.bar-hit")).toHaveLength(24);
    expect(wrapper.find('rect.bar-hit[data-bucket="2026-08-21T05:00:00Z"]').exists()).toBe(true);
    expect(wrapper.findAll("text.axis-label.bottom")[0]?.text()).toBe("08-20 13:00");
    expect(wrapper.text()).toContain("每小时平均");
    // 24 hours is always hourly; the day/hour toggle is not offered.
    expect(wrapper.find('input[value="hour"]').exists()).toBe(false);
  });

  it("offers hourly buckets for a 7-day range and day buckets only beyond 7 days", async () => {
    vi.spyOn(adminApi, "getMetricsOverview").mockResolvedValue(overview);
    const timeseries = vi.spyOn(adminApi, "getMetricsTimeseries").mockResolvedValue([dailyPoint]);
    vi.spyOn(adminApi, "listUnconfirmedTraces").mockResolvedValue([]);
    const wrapper = mountDashboard();
    await flushPromises();

    await wrapper.get('input[value="hour"]').setValue(true);
    await flushPromises();
    expect(timeseries).toHaveBeenLastCalledWith(expect.objectContaining({ granularity: "hour", from: "2026-08-14T13:00:00.000Z" }));

    timeseries.mockResolvedValue([dailyPoint, { ...dailyPoint, bucket: "2026-08-17T00:00:00Z", queries: 4 }]);
    await wrapper.get('input[value="30d"]').setValue(true);
    await flushPromises();
    expect(timeseries).toHaveBeenLastCalledWith(expect.objectContaining({ granularity: "day", from: "2026-07-23T00:00:00.000Z" }));
    expect(wrapper.find('input[value="hour"]').exists()).toBe(false);
    // Exactly 30 day buckets; quiet days are filled with zero.
    expect(wrapper.findAll("rect.bar-hit")).toHaveLength(30);
    for (const bucket of ["2026-08-18T00:00:00Z", "2026-08-19T00:00:00Z"]) {
      expect(wrapper.find(`rect.bar-hit[data-bucket="${bucket}"]`).exists()).toBe(true);
    }
    const summary = wrapper.find(".trend-summary").text();
    expect(summary).toContain("24"); // 20 + 4 requests in range
    // Direct answers (12+2)*2=28 of in-scope (12+2+5)*2=38 → 73.7%, a real ratio.
    expect(summary).toContain("73.7%");
    expect(summary).not.toMatch(/\d{4,}\.\d%/);
    expect(wrapper.text()).toContain("按 UTC+00:00 分桶");
  });

  it("realigns the window once to a non-UTC backend bucket offset", async () => {
    vi.spyOn(adminApi, "getMetricsOverview").mockResolvedValue(overview);
    vi.spyOn(adminApi, "listUnconfirmedTraces").mockResolvedValue([]);
    const timeseries = vi.spyOn(adminApi, "getMetricsTimeseries")
      .mockResolvedValue([{ ...dailyPoint, bucket: "2026-08-20T00:00:00+08:00" }]);
    const wrapper = mountDashboard();
    await flushPromises();

    // First request assumes UTC; the +08:00 buckets trigger one realigned request
    // starting at the server's midnight six days before today (08-21 20:00 +08:00).
    expect(timeseries).toHaveBeenCalledTimes(2);
    expect(timeseries.mock.calls[1]![0]).toMatchObject({ from: "2026-08-14T16:00:00.000Z" });
    expect(wrapper.findAll("rect.bar-hit")).toHaveLength(7);
    expect(wrapper.text()).toContain("按 UTC+08:00 分桶");
    expect(wrapper.text()).not.toContain("部分质量指标暂时无法加载");

    await wrapper.get('input[value="14d"]').setValue(true);
    await flushPromises();
    expect(timeseries).toHaveBeenCalledTimes(3); // offset already known: no extra request
    expect(timeseries.mock.calls[2]![0]).toMatchObject({ from: "2026-08-07T16:00:00.000Z" });
  });

  it("maps picked calendar dates to whole server days and rejects ranges beyond the limit", async () => {
    vi.spyOn(adminApi, "getMetricsOverview").mockResolvedValue(overview);
    const timeseries = vi.spyOn(adminApi, "getMetricsTimeseries").mockResolvedValue([dailyPoint]);
    vi.spyOn(adminApi, "listUnconfirmedTraces").mockResolvedValue([]);
    const wrapper = mountDashboard();
    await flushPromises();

    await wrapper.get('input[value="custom"]').setValue(true);
    await flushPromises();
    // The date picker forwards its events from the inner common picker.
    const picker = wrapper.findComponent({ name: "ElDatePicker" }).findComponent({ name: "Picker" });
    expect(picker.exists()).toBe(true);
    const pick = async (range: [Date, Date]) => {
      picker.vm.$emit("update:modelValue", range);
      picker.vm.$emit("change", range);
      await flushPromises();
    };

    await pick([new Date(2026, 7, 10), new Date(2026, 7, 12)]);
    expect(timeseries).toHaveBeenLastCalledWith(expect.objectContaining({
      granularity: "day", from: "2026-08-10T00:00:00.000Z", to: "2026-08-13T00:00:00.000Z",
    }));
    expect(wrapper.findAll("rect.bar-hit")).toHaveLength(11); // 08-10..08-20: returned 08-20 bucket is kept
    const calls = timeseries.mock.calls.length;

    await pick([new Date(2026, 3, 1), new Date(2026, 7, 20)]);
    expect(timeseries).toHaveBeenCalledTimes(calls);
    expect(wrapper.text()).toContain("时间范围不能超过 92 天");
    // The picker is restored to the range the chart still shows.
    const restored = wrapper.findComponent({ name: "ElDatePicker" }).props("modelValue") as Date[];
    expect(restored.map((date) => date.getDate())).toEqual([10, 12]);
  });

  it("ignores a late trend response after the range changed", async () => {
    vi.spyOn(adminApi, "getMetricsOverview").mockResolvedValue(overview);
    vi.spyOn(adminApi, "listUnconfirmedTraces").mockResolvedValue([]);
    let resolveOld!: (value: MetricsTimeseriesPoint[]) => void;
    const timeseries = vi.spyOn(adminApi, "getMetricsTimeseries")
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }))
      .mockResolvedValue([{ ...dailyPoint, bucket: "2026-08-21T00:00:00Z", queries: 7 }]);
    const wrapper = mountDashboard();
    await flushPromises();

    await wrapper.get('input[value="14d"]').setValue(true);
    await flushPromises();
    resolveOld([{ ...dailyPoint, queries: 99 }]);
    await flushPromises();

    expect(timeseries).toHaveBeenCalledTimes(2);
    expect(wrapper.find('rect.bar-hit[data-bucket="2026-08-21T00:00:00Z"]').exists()).toBe(true);
    expect(wrapper.find(".trend-summary").text()).not.toContain("99");
    expect(wrapper.text()).not.toContain("部分质量指标暂时无法加载");
  });

  it("shows a trend empty state, and a trend failure only once inside the trend panel", async () => {
    vi.spyOn(adminApi, "getMetricsOverview").mockResolvedValue(overview);
    const timeseries = vi.spyOn(adminApi, "getMetricsTimeseries").mockResolvedValue([]);
    vi.spyOn(adminApi, "listUnconfirmedTraces").mockResolvedValue([]);
    const wrapper = mountDashboard();
    await flushPromises();
    expect(wrapper.text()).toContain("该时段没有外部请求");

    timeseries.mockRejectedValue(new Error("boom"));
    await wrapper.get('input[value="90d"]').setValue(true);
    await flushPromises();
    expect(wrapper.text()).toContain("趋势数据暂时无法加载");
  });

  it("does not duplicate a trend failure into the page-level warning on refresh", async () => {
    vi.spyOn(adminApi, "getMetricsOverview").mockResolvedValue(overview);
    vi.spyOn(adminApi, "getMetricsTimeseries").mockRejectedValue(new Error("boom"));
    vi.spyOn(adminApi, "listUnconfirmedTraces").mockResolvedValue([]);
    const wrapper = mountDashboard();
    await flushPromises();

    expect(wrapper.text()).toContain("趋势数据暂时无法加载");
    expect(wrapper.text()).not.toContain("部分质量指标暂时无法加载");
    expect(wrapper.findAll(".el-alert")).toHaveLength(1);
    // The refresh is still not complete while the trend is missing.
    expect(wrapper.text()).toContain("上次完整刷新 尚未加载");
  });

  it("lists V2 stage degradation as to-dos only above the threshold and explains it in diagnostics", async () => {
    const quality = {
      query_interpreter_total: 40,
      query_interpreter_degraded: 4,
      query_interpreter_degraded_rate: 0.1,
      document_rerank_attempted: 30,
      document_rerank_degraded: 2,
      document_rerank_degraded_rate: 0.0667,
      document_rerank_unconfigured: 3,
    };
    vi.spyOn(adminApi, "getMetricsOverview").mockResolvedValue({ ...overview, failed: 0, v2_quality: quality });
    vi.spyOn(adminApi, "getMetricsTimeseries").mockResolvedValue([]);
    vi.spyOn(adminApi, "listUnconfirmedTraces").mockResolvedValue([]);
    const wrapper = mountDashboard();
    await flushPromises();

    const rows = wrapper.findAll(".attention-row").map((row) => row.text());
    expect(rows.some((text) => text.includes("检索未执行文档重排") && text.includes("3 次"))).toBe(true);
    expect(rows.some((text) => text.includes("查询理解失败后使用兜底") && text.includes("10.0%"))).toBe(true);
    // 2 degraded reranks is below the minimum count, even at 6.7%.
    expect(rows.some((text) => text.includes("文档重排调用失败后降级"))).toBe(false);

    const diagnostics = wrapper.get("details.advanced-details:not(.review-details)");
    expect(diagnostics.attributes("open")).toBeUndefined();
    await wrapper.findAll(".attention-row").find((row) => row.text().includes("查询理解"))!.trigger("click");
    expect(diagnostics.attributes("open")).toBeDefined();
    const section = wrapper.get(".quality-section").text();
    expect(section).toContain("10.0%");
    expect(section).toContain("4 / 40");
    expect(section).toContain("2 / 30");
    // Legacy always-zero fields are not shown anywhere.
    expect(wrapper.text()).not.toContain("引用缺失");
  });

  it("says when the backend does not provide stage quality instead of showing zeros", async () => {
    const legacy = { ...overview };
    delete legacy.v2_quality;
    vi.spyOn(adminApi, "getMetricsOverview").mockResolvedValue(legacy);
    vi.spyOn(adminApi, "getMetricsTimeseries").mockResolvedValue([]);
    vi.spyOn(adminApi, "listUnconfirmedTraces").mockResolvedValue([]);
    const wrapper = mountDashboard();
    await flushPromises();
    expect(wrapper.get(".quality-section").text()).toContain("当前后端版本未提供阶段质量统计");
    expect(wrapper.findAll(".attention-row").some((row) => row.text().includes("重排"))).toBe(false);
  });

  it("degrades to a current, implementation-neutral warning when metrics are unavailable", async () => {
    vi.spyOn(adminApi, "getMetricsOverview").mockRejectedValue(new Error("404"));
    vi.spyOn(adminApi, "getMetricsTimeseries").mockResolvedValue([]);
    vi.spyOn(adminApi, "listUnconfirmedTraces").mockResolvedValue([]);

    const wrapper = mountDashboard();
    await flushPromises();

    expect(wrapper.find(".el-alert").exists()).toBe(true);
    expect(wrapper.text()).toContain("部分质量指标暂时无法加载");
    expect(wrapper.text()).not.toContain("迁移 0014");
    expect(wrapper.find("svg.trend-chart").exists()).toBe(false);
  });

  it("ignores malformed overview payloads instead of crashing", async () => {
    // e.g. a gateway returning a JSON error array
    vi.spyOn(adminApi, "getMetricsOverview").mockResolvedValue([] as never);
    vi.spyOn(adminApi, "getMetricsTimeseries").mockResolvedValue([dailyPoint]);
    vi.spyOn(adminApi, "listUnconfirmedTraces").mockResolvedValue([]);

    const wrapper = mountDashboard();
    await flushPromises();

    expect(wrapper.text()).toContain("部分质量指标暂时无法加载");
    expect(wrapper.text()).not.toContain("迁移 0014");
  });

  it("stops the 60s auto-refresh once the service credential is rejected, instead of spamming 401s forever", async () => {
    vi.useRealTimers(); // replace the Date-only fake from beforeEach
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    const overviewSpy = vi.spyOn(adminApi, "getMetricsOverview").mockResolvedValue(overview);
    vi.spyOn(adminApi, "getMetricsTimeseries").mockResolvedValue([]);
    vi.spyOn(adminApi, "listUnconfirmedTraces").mockResolvedValue([]);
    adminStore.serviceToken = "test-service-token";

    mountDashboard();
    await flushPromises();
    expect(overviewSpy).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(60_000);
    expect(overviewSpy).toHaveBeenCalledTimes(2); // credential still valid: auto-refresh keeps polling

    adminStore.credentialRejected = true;
    await vi.advanceTimersByTimeAsync(60_000);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(overviewSpy).toHaveBeenCalledTimes(2); // rejected: no further ticks call the API
  });

  it("marks a malformed refresh as partial even if previous overview data exists", async () => {
    vi.useRealTimers(); // replace the Date-only fake from beforeEach
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    const api = vi.spyOn(adminApi, "getMetricsOverview").mockResolvedValueOnce(overview).mockResolvedValue([] as never);
    vi.spyOn(adminApi, "getMetricsTimeseries").mockResolvedValue([]);
    vi.spyOn(adminApi, "listUnconfirmedTraces").mockResolvedValue([]);
    const wrapper = mountDashboard(); await flushPromises();
    expect(wrapper.find(".el-alert").exists()).toBe(false);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(api).toHaveBeenCalledTimes(2);
    expect(wrapper.text()).toContain("部分质量指标暂时无法加载");
    expect(wrapper.text()).toContain("61.9%");
  });

  it("does not load or poll any metrics without a credential", async () => {
    vi.useRealTimers(); // replace the Date-only fake from beforeEach
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    adminStore.serviceToken = "";
    const api = vi.spyOn(adminApi, "getMetricsOverview").mockResolvedValue(overview);
    mountDashboard(); await flushPromises();
    await vi.advanceTimersByTimeAsync(120_000);
    expect(api).not.toHaveBeenCalled();
    expect(adminApi.getKnowledgeAssistant).not.toHaveBeenCalled();
    expect(adminApi.getAssistantMetrics).not.toHaveBeenCalled();
  });

  it("uses dashes for zero-sample percentages and latency", async () => {
    vi.spyOn(adminApi, "getMetricsOverview").mockResolvedValue({ ...overview, total_traces: 0, failed: 0, in_scope_traces: 0, classified_traces: 0, answer_basis_traces: 0, latency: { ...overview.latency, samples: 0 } });
    vi.spyOn(adminApi, "getMetricsTimeseries").mockResolvedValue([]);
    vi.spyOn(adminApi, "listUnconfirmedTraces").mockResolvedValue([]);
    const wrapper = mountDashboard(); await flushPromises();
    const values = wrapper.findAll(".metric-card .value");
    expect(values.map((item) => item.text())).toEqual(["0", "—", "—", "—"]);
    expect(wrapper.text()).not.toContain("0.0%");
  });

  it("does not overwrite a new knowledge base with late metrics or readiness", async () => {
    let resolveOld!: (value: MetricsOverview) => void;
    vi.spyOn(adminApi, "getMetricsOverview").mockImplementation(async (params) => params?.knowledgeId === "kb-a" ? new Promise((resolve) => { resolveOld = resolve; }) : { ...overview, total_traces: 999 });
    vi.spyOn(adminApi, "getMetricsTimeseries").mockResolvedValue([]);
    vi.spyOn(adminApi, "listUnconfirmedTraces").mockResolvedValue([]);
    adminStore.selectedKnowledgeId = "kb-a";
    const wrapper = mountDashboard(); await flushPromises();
    adminStore.selectedKnowledgeId = "kb-b"; await flushPromises();
    resolveOld(overview); await flushPromises();
    expect(wrapper.text()).toContain("请求 999 次");
    expect(wrapper.text()).not.toContain("请求 120 次");
    expect(wrapper.text()).toContain("尚未对外上线");
  });

  it("opens the selected knowledge base's checklist and distinguishes online from ready", async () => {
    vi.spyOn(adminApi, "getMetricsOverview").mockResolvedValue(overview);
    vi.spyOn(adminApi, "getMetricsTimeseries").mockResolvedValue([]);
    vi.spyOn(adminApi, "listUnconfirmedTraces").mockResolvedValue([]);
    vi.mocked(adminApi.getKnowledgeAssistant).mockResolvedValue({ knowledge_id: "kb-a", assistant_online: true, ready: false, blockers: ["卡片未确认"], revision: 0, suggestions: [] });
    adminStore.selectedKnowledgeId = "kb-a";
    const navigate = vi.spyOn(adminStore, "navigate").mockImplementation(() => {});
    const wrapper = mountDashboard(); await flushPromises();
    expect(wrapper.text()).toContain("已上线 · 暂不可用");
    await wrapper.findAll("button").find((item) => item.text() === "打开上线检查清单")!.trigger("click");
    expect(navigate).toHaveBeenCalledWith("knowledge", "id=kb-a");
  });
});

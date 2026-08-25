import ElementPlus from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

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
  classified_traces: 110,
  no_answer_rate: 0.4091,
  doc_delivered_rate: 0.0455,
  failure_rate: 0.0833,
  citation_missing_rate: 0.02,
  rerank_degraded_rate: 0.01,
  unique_users: 12,
  latency: { p50_ms: 900, p95_ms: 2500, p99_ms: 4000, avg_ms: 1100 },
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
  no_answer_rate: 0.3,
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

describe("DashboardView metrics", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    adminStore.selectedKnowledgeId = "";
  });

  it("renders overview cards, trend chart and drilldown from metrics", async () => {
    vi.spyOn(adminApi, "getMetricsOverview").mockResolvedValue(overview);
    vi.spyOn(adminApi, "getMetricsTimeseries").mockResolvedValue([dailyPoint]);
    vi.spyOn(adminApi, "listNoAnswerTraces").mockResolvedValue([trace]);
    adminStore.selectedKnowledgeId = "kb-1";

    const wrapper = mount(DashboardView, { global: { plugins: [ElementPlus] } });
    await flushPromises();

    expect(adminApi.getMetricsOverview).toHaveBeenCalledWith({ knowledgeId: "kb-1" });
    expect(adminApi.listNoAnswerTraces).toHaveBeenCalledWith("kb-1");
    expect(wrapper.text()).toContain("120"); // total queries
    expect(wrapper.text()).toContain("40.9%"); // no-answer rate
    expect(wrapper.text()).toContain("2,500 ms"); // p95
    expect(wrapper.find("svg.chart-svg").exists()).toBe(true);
    expect(wrapper.text()).toContain("如何配置？"); // drilldown question
    expect(wrapper.text()).toContain("mobile-user-1");
  });

  it("opens a bucket drilldown dialog with that bucket's traces on bar click", async () => {
    vi.spyOn(adminApi, "getMetricsOverview").mockResolvedValue(overview);
    vi.spyOn(adminApi, "getMetricsTimeseries").mockResolvedValue([dailyPoint]);
    vi.spyOn(adminApi, "listNoAnswerTraces").mockResolvedValue([]);
    const listTraces = vi
      .spyOn(adminApi, "listTraces")
      .mockResolvedValue([trace]);

    const wrapper = mount(DashboardView, { global: { plugins: [ElementPlus] } });
    await flushPromises();

    const bar = wrapper.find("svg.chart-svg rect.bar");
    expect(bar.exists()).toBe(true);
    await bar.trigger("click");
    await flushPromises();

    // daily bucket 2026-08-20T00:00:00Z -> range [bucket, +1d)
    expect(listTraces).toHaveBeenCalledWith(undefined, {
      from: "2026-08-20T00:00:00Z",
      to: "2026-08-21T00:00:00.000Z",
      limit: 100,
    });
    expect(wrapper.text()).toContain("如何配置？"); // dialog lists the trace
  });

  it("shows hourly labels when timeseries buckets are hourly", async () => {
    vi.spyOn(adminApi, "getMetricsOverview").mockResolvedValue(overview);
    vi.spyOn(adminApi, "getMetricsTimeseries").mockResolvedValue([
      { ...dailyPoint, bucket: "2026-08-20T05:00:00Z" },
      { ...dailyPoint, bucket: "2026-08-20T06:00:00Z" },
    ]);
    vi.spyOn(adminApi, "listNoAnswerTraces").mockResolvedValue([]);

    const wrapper = mount(DashboardView, { global: { plugins: [ElementPlus] } });
    await flushPromises();

    const ticks = wrapper.findAll("text.axis-label.bottom");
    expect(ticks[0]?.text()).toBe("08-20 05");
  });

  it("degrades to a warning when metrics are unavailable (old backend)", async () => {
    vi.spyOn(adminApi, "getMetricsOverview").mockRejectedValue(new Error("404"));
    vi.spyOn(adminApi, "getMetricsTimeseries").mockResolvedValue([]);
    vi.spyOn(adminApi, "listNoAnswerTraces").mockResolvedValue([]);

    const wrapper = mount(DashboardView, { global: { plugins: [ElementPlus] } });
    await flushPromises();

    expect(wrapper.find(".el-alert").exists()).toBe(true);
    expect(wrapper.find("svg.trend-chart").exists()).toBe(false);
  });

  it("ignores malformed overview payloads instead of crashing", async () => {
    // e.g. a gateway returning a JSON error array
    vi.spyOn(adminApi, "getMetricsOverview").mockResolvedValue([] as never);
    vi.spyOn(adminApi, "getMetricsTimeseries").mockResolvedValue([dailyPoint]);
    vi.spyOn(adminApi, "listNoAnswerTraces").mockResolvedValue([]);

    const wrapper = mount(DashboardView, { global: { plugins: [ElementPlus] } });
    await flushPromises();

    expect(wrapper.text()).toContain("质量指标加载失败");
  });
});

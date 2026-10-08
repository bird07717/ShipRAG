import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import TrendChart, { type TrendPoint } from "@/components/ui/TrendChart.vue";

function point(overrides: Partial<TrendPoint> & Pick<TrendPoint, "bucket" | "label">): TrendPoint {
  return {
    title: overrides.label,
    queries: 0,
    direct: 0,
    needsInformation: 0,
    outOfScope: 0,
    failed: 0,
    other: 0,
    directAnswerRate: null,
    needsInformationRate: null,
    p95Ms: null,
    ...overrides,
  };
}

const points: TrendPoint[] = [
  point({ bucket: "2026-08-19T00:00:00Z", label: "08-19", queries: 10, direct: 6, needsInformation: 2, failed: 1, other: 1, directAnswerRate: 0.75, needsInformationRate: 0.25, p95Ms: 1800 }),
  point({ bucket: "2026-08-20T00:00:00Z", label: "08-20", queries: 30, direct: 12, needsInformation: 12, outOfScope: 3, other: 3, directAnswerRate: 0.5, needsInformationRate: 0.5, p95Ms: 2400 }),
  point({ bucket: "2026-08-21T00:00:00Z", label: "08-21" }),
];

describe("TrendChart", () => {
  it("renders one hit area per bucket and emits select only for buckets with traffic", async () => {
    const wrapper = mount(TrendChart, { props: { points } });
    const hits = wrapper.findAll("rect.bar-hit");
    expect(hits).toHaveLength(3);

    await hits[1]!.trigger("click");
    expect(wrapper.emitted("select")?.[0]?.[0]).toEqual(points[1]);

    await hits[2]!.trigger("click"); // empty bucket: nothing to drill into
    expect(wrapper.emitted("select")).toHaveLength(1);
    expect(hits[2]!.attributes("tabindex")).toBe("-1");
  });

  it("stacks outcome segments and supports keyboard selection", async () => {
    const wrapper = mount(TrendChart, { props: { points } });
    // bucket 1: direct, needsInformation, failed, other → 4 segments; bucket 2: 4; bucket 3: none.
    expect(wrapper.findAll("path.segment")).toHaveLength(8);
    expect(wrapper.findAll("path.seg-failed")).toHaveLength(1);

    await wrapper.findAll("rect.bar-hit")[0]!.trigger("keydown", { key: "Enter" });
    expect(wrapper.emitted("select")?.[0]?.[0]).toEqual(points[0]);
  });

  it("shows a tooltip with the bucket breakdown on hover", async () => {
    const wrapper = mount(TrendChart, { props: { points } });
    expect(wrapper.find(".chart-tooltip").exists()).toBe(false);
    await wrapper.findAll("rect.bar-hit")[1]!.trigger("mouseenter");
    const tooltip = wrapper.get(".chart-tooltip");
    expect(tooltip.text()).toContain("08-20");
    expect(tooltip.text()).toContain("30");
    expect(tooltip.text()).toContain("50.0%");
    expect(tooltip.text()).toContain("2,400 ms");
    await wrapper.get(".plot").trigger("mouseleave");
    expect(wrapper.find(".chart-tooltip").exists()).toBe(false);
  });

  it("hides a series from the legend but keeps at least one visible", async () => {
    const wrapper = mount(TrendChart, { props: { points } });
    const legend = () => wrapper.findAll("button.legend-item");
    await legend().find((item) => item.text().includes("执行失败"))!.trigger("click");
    expect(wrapper.findAll("path.seg-failed")).toHaveLength(0);

    for (const label of ["直接回答", "待补充信息", "范围外", "寒暄与对话等"]) {
      await legend().find((item) => item.text().includes(label))!.trigger("click");
    }
    // The last visible series cannot be hidden.
    expect(legend().filter((item) => !item.classes("off"))).toHaveLength(1);
    expect(wrapper.findAll("path.segment").length).toBeGreaterThan(0);
  });

  it("breaks the line at buckets without a sample and switches the line metric", async () => {
    const wrapper = mount(TrendChart, { props: { points } });
    const d = wrapper.get("path.rate-line").attributes("d")!;
    expect(d.match(/[ML]/g)).toEqual(["M", "L"]); // third bucket is null → no segment to it
    expect(wrapper.text()).toContain("待补充信息率（右轴）");

    await wrapper.setProps({ lineMetric: "p95Ms" });
    expect(wrapper.text()).toContain("技术回答 P95 耗时（右轴）");
    expect(wrapper.findAll("text.axis-label.right").map((item) => item.text())).toContain("0 ms");

    await wrapper.setProps({ lineMetric: "none" });
    expect(wrapper.find("path.rate-line").exists()).toBe(false);
  });

  it("scales the y axis so the tallest bar fills most of the plot", () => {
    const tall = [point({ bucket: "2026-08-19T00:00:00Z", label: "08-19", queries: 101, direct: 101 })];
    const wrapper = mount(TrendChart, { props: { points: tall, lineMetric: "none" } });
    const labels = wrapper.findAll("text.axis-label.left").map((item) => item.text());
    expect(labels.at(-1)).toBe("125"); // was 200 with the old four-interval floor
  });

  it("thins x labels when there are many bars", () => {
    const dense = Array.from({ length: 40 }, (_, i) => point({
      bucket: `2026-08-${String(i + 1).padStart(2, "0")}T00:00:00Z`,
      label: `08-${String(i + 1).padStart(2, "0")}`,
      queries: i,
      direct: i,
    }));
    const wrapper = mount(TrendChart, { props: { points: dense } });
    const labels = wrapper.findAll("text.axis-label.bottom");
    expect(labels.length).toBeLessThan(dense.length);
    expect(labels[0]!.text()).toBe("08-01");
  });
});

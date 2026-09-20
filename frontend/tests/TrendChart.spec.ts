import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import TrendChart, { type TrendPoint } from "@/components/ui/TrendChart.vue";

const points: TrendPoint[] = [
  { bucket: "2026-08-19T00:00:00Z", label: "08-19", queries: 10, needsInformationRate: 0.2 },
  { bucket: "2026-08-20T00:00:00Z", label: "08-20", queries: 30, needsInformationRate: 0.5 },
  { bucket: "2026-08-21T00:00:00Z", label: "08-21", queries: 0, needsInformationRate: 0 },
];

describe("TrendChart", () => {
  it("renders one clickable bar per point and emits select on click", async () => {
    const wrapper = mount(TrendChart, { props: { points } });
    const bars = wrapper.findAll("rect.bar");
    expect(bars).toHaveLength(3);

    await bars[1]!.trigger("click");
    expect(wrapper.emitted("select")?.[0]?.[0]).toEqual(points[1]);
  });

  it("renders gridlines and a legend explaining both series", () => {
    const wrapper = mount(TrendChart, { props: { points } });
    expect(wrapper.findAll("line.gridline").length).toBeGreaterThan(1);
    expect(wrapper.text()).toContain("查询量");
    expect(wrapper.text()).toContain("待补充信息率");
    expect(wrapper.find("polyline.rate-line").exists()).toBe(true);
  });

  it("thins x labels when there are many bars", () => {
    const dense = Array.from({ length: 40 }, (_, i) => ({
      bucket: `2026-08-${String(i + 1).padStart(2, "0")}T00:00:00Z`,
      label: `08-${String(i + 1).padStart(2, "0")}`,
      queries: i,
      needsInformationRate: 0.1,
    }));
    const wrapper = mount(TrendChart, { props: { points: dense } });
    const labels = wrapper.findAll("text.axis-label.bottom");
    expect(labels.length).toBeLessThan(dense.length);
  });
});

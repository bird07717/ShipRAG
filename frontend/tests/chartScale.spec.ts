import { describe, expect, it } from "vitest";

import { niceAxis, niceStep } from "@/utils/chartScale";

describe("chartScale", () => {
  it("picks integer-friendly nice steps", () => {
    expect(niceStep(0.4)).toBe(1);
    expect(niceStep(1.8)).toBe(2);
    expect(niceStep(2.2)).toBe(5); // no 2.5 below 10: tick labels stay integers
    expect(niceStep(20.2)).toBe(25);
    expect(niceStep(200.2)).toBe(250);
  });

  it.each([3, 9, 13, 21, 47, 101, 260, 1001, 2400])("keeps the tallest bar above 80%% of the axis (max %i)", (max) => {
    const { step, top } = niceAxis(max);
    expect(top).toBeGreaterThanOrEqual(max);
    expect(max / top).toBeGreaterThanOrEqual(0.8);
    expect(top / step).toBeLessThanOrEqual(6);
    expect(Number.isInteger(step)).toBe(true);
  });

  it("returns a single step for an empty series", () => {
    expect(niceAxis(0)).toEqual({ step: 1, top: 1 });
  });
});

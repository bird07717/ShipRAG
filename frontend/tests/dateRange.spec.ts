import { describe, expect, it } from "vitest";

import { rangeDefaultTime } from "@/utils/dateRange";

describe("rangeDefaultTime", () => {
  it("starts at the beginning and ends at the end of the picked day", () => {
    const [start, end] = rangeDefaultTime;
    expect([start.getHours(), start.getMinutes(), start.getSeconds()]).toEqual([0, 0, 0]);
    expect([end.getHours(), end.getMinutes(), end.getSeconds()]).toEqual([23, 59, 59]);
  });
});

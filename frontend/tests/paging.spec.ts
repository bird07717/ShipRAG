import { describe, expect, it } from "vitest";

import { clampedPage } from "@/utils/paging";

describe("clampedPage", () => {
  it("moves an empty page beyond the end back to the last page", () => {
    expect(clampedPage(2, 20, 0, 20)).toBe(1);
    expect(clampedPage(5, 20, 0, 41)).toBe(3);
  });

  it("leaves valid, first and genuinely empty pages alone", () => {
    expect(clampedPage(2, 20, 5, 25)).toBeNull();
    expect(clampedPage(1, 20, 0, 0)).toBeNull();
    expect(clampedPage(3, 20, 0, 0)).toBeNull();
    expect(clampedPage(2, 20, 0, 30)).toBeNull();
  });
});

import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchReadiness } from "@/api/health";

describe("fetchReadiness", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("accepts a 503 readiness payload as a valid dependency report", async () => {
    const payload = { status: "not_ready", checks: {} };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 503, json: () => payload }),
    );

    await expect(fetchReadiness()).resolves.toEqual(payload);
  });

  it("rejects unexpected backend responses", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 500, json: () => ({}) }),
    );

    await expect(fetchReadiness()).rejects.toThrow("HTTP 500");
  });
});

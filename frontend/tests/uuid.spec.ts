import { afterEach, describe, expect, it, vi } from "vitest";

import { randomUUID } from "@/utils/uuid";

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe("randomUUID", () => {
  const native = crypto.randomUUID;

  afterEach(() => {
    Object.defineProperty(crypto, "randomUUID", {
      value: native,
      configurable: true,
      writable: true,
    });
  });

  it("delegates to the native implementation when available", () => {
    const spy = vi.fn(() => "00000000-0000-4000-8000-000000000000");
    Object.defineProperty(crypto, "randomUUID", { value: spy, configurable: true });
    expect(randomUUID()).toBe("00000000-0000-4000-8000-000000000000");
    expect(spy).toHaveBeenCalledOnce();
  });

  it("falls back to getRandomValues in insecure contexts", () => {
    Object.defineProperty(crypto, "randomUUID", {
      value: undefined,
      configurable: true,
    });
    const values = new Set(Array.from({ length: 100 }, () => randomUUID()));
    expect(values.size).toBe(100);
    for (const value of values) expect(value).toMatch(UUID_V4);
  });
});

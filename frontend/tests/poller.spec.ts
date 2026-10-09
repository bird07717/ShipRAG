import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { backendConnectivity } from "@/api/connectivity";
import { createPoller } from "@/utils/poller";

function setVisibility(state: "visible" | "hidden"): void {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
  document.dispatchEvent(new Event("visibilitychange"));
}

describe("createPoller", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    backendConnectivity.status = "online";
    setVisibility("visible");
  });
  afterEach(() => {
    vi.useRealTimers();
    backendConnectivity.status = "unknown";
    setVisibility("visible");
  });

  it("runs serially and never overlaps a slow tick", async () => {
    let finish!: () => void;
    const task = vi.fn(() => new Promise<void>((resolve) => { finish = resolve; }));
    const poller = createPoller(task, { intervalMs: 1_000 });
    poller.start();

    await vi.advanceTimersByTimeAsync(1_000);
    expect(task).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(5_000);
    expect(task).toHaveBeenCalledTimes(1);
    finish();
    await vi.advanceTimersByTimeAsync(1_000);
    expect(task).toHaveBeenCalledTimes(2);
    poller.stop();
  });

  it("skips ticks while hidden, offline or disabled, and resumes on visibility", async () => {
    const task = vi.fn();
    let enabled = true;
    const poller = createPoller(task, { intervalMs: 1_000, enabled: () => enabled });
    poller.start();

    setVisibility("hidden");
    await vi.advanceTimersByTimeAsync(3_000);
    expect(task).not.toHaveBeenCalled();
    setVisibility("visible");
    await vi.advanceTimersByTimeAsync(0);
    expect(task).toHaveBeenCalledTimes(1);

    backendConnectivity.status = "offline";
    await vi.advanceTimersByTimeAsync(3_000);
    expect(task).toHaveBeenCalledTimes(1);
    backendConnectivity.status = "online";
    enabled = false;
    await vi.advanceTimersByTimeAsync(3_000);
    expect(task).toHaveBeenCalledTimes(1);
    enabled = true;
    await vi.advanceTimersByTimeAsync(1_000);
    expect(task).toHaveBeenCalledTimes(2);
    poller.stop();
  });

  it("keeps polling after a failing tick and stops cleanly", async () => {
    const task = vi.fn().mockRejectedValueOnce(new Error("boom")).mockResolvedValue(undefined);
    const poller = createPoller(task, { intervalMs: 1_000 });
    poller.start();
    await vi.advanceTimersByTimeAsync(2_000);
    expect(task).toHaveBeenCalledTimes(2);
    poller.stop();
    expect(poller.running).toBe(false);
    await vi.advanceTimersByTimeAsync(5_000);
    expect(task).toHaveBeenCalledTimes(2);
  });

  it("trigger runs immediately and restarts the interval", async () => {
    const task = vi.fn();
    const poller = createPoller(task, { intervalMs: 1_000 });
    poller.trigger();
    expect(task).not.toHaveBeenCalled();
    poller.start();
    await vi.advanceTimersByTimeAsync(600);
    poller.trigger();
    await vi.advanceTimersByTimeAsync(0);
    expect(task).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(600);
    expect(task).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(400);
    expect(task).toHaveBeenCalledTimes(2);
    poller.stop();
  });
});

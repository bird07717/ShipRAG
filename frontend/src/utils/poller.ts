import { backendConnectivity } from "@/api/connectivity";

/*
 * Serial background poller shared by admin views.
 *
 * - Ticks are scheduled with setTimeout after the previous run settles, so a
 *   slow response never overlaps the next tick (no setInterval pile-up).
 * - A tick is skipped while the page is hidden or the backend is known to be
 *   offline; BackendStatusBanner owns reconnection and the shell reloads data
 *   once it is back. Becoming visible again runs one tick immediately.
 * - `enabled()` gates each tick (credential present, something to watch...).
 */

export interface PollerOptions {
  intervalMs: number;
  enabled?: () => boolean;
  pauseWhenHidden?: boolean;
  pauseWhenOffline?: boolean;
}

export interface Poller {
  start(): void;
  stop(): void;
  /** Run one tick now (if idle) and restart the interval from it. */
  trigger(): void;
  readonly running: boolean;
}

function pageHidden(): boolean {
  return typeof document !== "undefined" && document.visibilityState === "hidden";
}

export function createPoller(task: () => unknown, options: PollerOptions): Poller {
  const { intervalMs, enabled = () => true, pauseWhenHidden = true, pauseWhenOffline = true } = options;
  let running = false;
  let inFlight = false;
  let timer: ReturnType<typeof setTimeout> | undefined;

  function schedule(): void {
    clearTimeout(timer);
    timer = running ? setTimeout(() => void tick(), intervalMs) : undefined;
  }

  function paused(): boolean {
    return (pauseWhenHidden && pageHidden())
      || (pauseWhenOffline && backendConnectivity.status === "offline")
      || !enabled();
  }

  async function tick(): Promise<void> {
    if (!running) return;
    if (inFlight) return;
    if (!paused()) {
      inFlight = true;
      try {
        await task();
      } catch {
        // The task owns its error reporting; a failed tick must not stop polling.
      } finally {
        inFlight = false;
      }
    }
    schedule();
  }

  function onVisibilityChange(): void {
    if (running && !pageHidden()) trigger();
  }

  function trigger(): void {
    if (!running || inFlight) return;
    clearTimeout(timer);
    timer = undefined;
    void tick();
  }

  return {
    start() {
      if (running) return;
      running = true;
      if (typeof document !== "undefined") document.addEventListener("visibilitychange", onVisibilityChange);
      schedule();
    },
    stop() {
      running = false;
      clearTimeout(timer);
      timer = undefined;
      if (typeof document !== "undefined") document.removeEventListener("visibilitychange", onVisibilityChange);
    },
    trigger,
    get running() {
      return running;
    },
  };
}

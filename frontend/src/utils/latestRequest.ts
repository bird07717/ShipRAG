/*
 * "Only the latest request may commit" guard for view-level loaders.
 *
 * begin() supersedes the previous call: its isCurrent() turns false and its
 * signal is aborted (callers that pass the signal to fetch also stop the
 * network request). cancel() invalidates the in-flight call, e.g. on unmount
 * or when the scope (knowledge base, credential) changes.
 */
export interface LatestRequestTicket {
  isCurrent(): boolean;
  signal: AbortSignal;
}

export interface LatestRequest {
  begin(): LatestRequestTicket;
  cancel(): void;
}

export function createLatestRequest(): LatestRequest {
  let generation = 0;
  let controller: AbortController | undefined;
  return {
    begin() {
      controller?.abort();
      controller = new AbortController();
      const ticket = ++generation;
      return { isCurrent: () => ticket === generation, signal: controller.signal };
    },
    cancel() {
      generation += 1;
      controller?.abort();
      controller = undefined;
    },
  };
}

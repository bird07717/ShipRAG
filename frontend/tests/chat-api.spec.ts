import { afterEach, describe, expect, it, vi } from "vitest";

import { downloadDocument, fetchImageBlobUrl, streamChat } from "@/api/chat";

function sseResponse(frames: string[]): Response {
  const encoder = new TextEncoder();
  let index = 0;
  return {
    ok: true,
    body: {
      getReader: () => ({
        read: () =>
          index < frames.length
            ? Promise.resolve({ done: false, value: encoder.encode(frames[index++]) })
            : Promise.resolve({ done: true, value: undefined }),
      }),
    },
  } as unknown as Response;
}

describe("chat API", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    sessionStorage.clear();
  });

  it("streams SSE events and dispatches callbacks", async () => {
    sessionStorage.setItem("rag_service_token", "session-token");
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse([
        'event: trace\ndata: {"trace_id":"t-1","conversation_id":"c-1"}\n\n',
        'event: message\ndata: {"delta":"第一"}\n\n',
        'event: message\ndata: {"delta":"段"}\n\n',
        'event: done\ndata: {"conversation_id":"c-1","message_id":"m-1","answer":"第一段","response_type":"DOC_DELIVERED","answer_mode":null,"disclaimer":null,"content":[{"type":"text","text":"第一段"}],"references":[],"usage":{},"latency_ms":12}\n\n',
      ]),
    );
    vi.stubGlobal("fetch", fetchMock);

    const onTrace = vi.fn();
    const onDelta = vi.fn();
    const onDone = vi.fn();
    await streamChat("kb-1", "如何升级", undefined, {
      onTrace,
      onDelta,
      onDone,
      onError: vi.fn(),
    });

    expect(onTrace).toHaveBeenCalledWith("t-1", "c-1");
    expect(onDelta).toHaveBeenNthCalledWith(1, "第一");
    expect(onDelta).toHaveBeenNthCalledWith(2, "段");
    expect(onDone).toHaveBeenCalledTimes(1);
    const donePayload = onDone.mock.calls[0]?.[0];
    expect(donePayload.response_type).toBe("DOC_DELIVERED");
    expect(donePayload.content[0]).toEqual({ type: "text", text: "第一段" });

    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(request.method).toBe("POST");
    expect(JSON.parse(String(request.body))).toEqual({
      knowledge_id: "kb-1",
      conversation_id: null,
      question: "如何升级",
    });
    expect(new Headers(request.headers).get("Authorization")).toBe("Bearer session-token");
  });

  it("surfaces the backend error message on HTTP failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 409,
        json: () => Promise.resolve({ error: { message: "会话不属于指定知识库" } }),
      }),
    );

    await expect(
      streamChat("kb-1", "问题", "c-1", {
        onTrace: vi.fn(),
        onDelta: vi.fn(),
        onDone: vi.fn(),
        onError: vi.fn(),
      }),
    ).rejects.toThrow("会话不属于指定知识库");
  });

  it("dispatches onError for SSE error events", async () => {
    const onError = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse([
          'event: trace\ndata: {"trace_id":"t-1","conversation_id":"c-1"}\n\n',
          'event: error\ndata: {"code":"UPSTREAM_TIMEOUT","message":"上游超时"}\n\n',
        ]),
      ),
    );

    await streamChat("kb-1", "问题", undefined, {
      onTrace: vi.fn(),
      onDelta: vi.fn(),
      onDone: vi.fn(),
      onError,
    });

    expect(onError).toHaveBeenCalledWith("上游超时");
  });

  it("resolves image asset blobs into object URLs", async () => {
    const revokeObjectURL = vi.fn();
    const createObjectURL = vi.fn().mockReturnValue("blob:asset-url");
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL,
      revokeObjectURL,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        blob: () => Promise.resolve(new Blob(["image-bytes"])),
      }),
    );

    await expect(fetchImageBlobUrl("asset-1")).resolves.toBe("blob:asset-url");

    const [url] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0] as unknown as [string];
    expect(url).toContain("/api/v1/image-assets/asset-1/content");
  });

  it("downloads a document through an object URL anchor", async () => {
    const createObjectURL = vi.fn().mockReturnValue("blob:doc-url");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });
    const click = vi.fn();
    vi.stubGlobal(
      "document",
      {
        ...document,
        createElement: () => ({ click, href: "", download: "" }),
      },
    );
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        blob: () => Promise.resolve(new Blob(["docx-bytes"])),
      }),
    );

    await downloadDocument("doc-1", "文档.docx");

    expect(click).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:doc-url");
  });
});

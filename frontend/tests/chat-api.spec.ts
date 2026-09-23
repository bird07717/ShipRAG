import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { downloadDocument, fetchImageBlobUrl, streamChat } from "@/api/chat";

function sseResponse(frames: Array<string | Uint8Array>): Response {
  const encoder = new TextEncoder();
  let index = 0;
  return {
    ok: true,
    body: {
      getReader: () => ({
        read: () =>
          index < frames.length
            ? Promise.resolve({
                done: false,
                value:
                  typeof frames[index] === "string"
                    ? encoder.encode(frames[index++] as string)
                    : frames[index++],
              })
            : Promise.resolve({ done: true, value: undefined }),
      }),
    },
  } as unknown as Response;
}

describe("chat API", () => {
  beforeEach(() => {
    sessionStorage.setItem("rag_service_token", "session-token");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    sessionStorage.clear();
  });

  it("streams SSE events and dispatches callbacks", async () => {
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
    const onStatus = vi.fn();
    const onFallback = vi.fn();
    await streamChat("kb-1", "如何升级", undefined, {
      onTrace,
      onDelta,
      onDone,
      onError: vi.fn(),
      onStatus,
      onFallback,
    });

    expect(onTrace).toHaveBeenCalledWith("t-1", "c-1");
    expect(onDelta).toHaveBeenNthCalledWith(1, "第一");
    expect(onDelta).toHaveBeenNthCalledWith(2, "段");
    expect(onDone).toHaveBeenCalledTimes(1);
    const donePayload = onDone.mock.calls[0]?.[0];
    expect(donePayload.response_type).toBe("DOC_DELIVERED");
    expect(donePayload.content[0]).toEqual({ type: "text", text: "第一段" });

    const [url, request] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/chat/stream");
    expect(request.method).toBe("POST");
    expect(JSON.parse(String(request.body))).toEqual({
      knowledge_id: "kb-1",
      conversation_id: null,
      question: "如何升级",
    });
    expect(new Headers(request.headers).get("Authorization")).toBe("Bearer session-token");
  });

  it("dispatches engine status and automatic fallback events", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse([
          'event: status\ndata: {"stage":"gateway_routing","engine":"SUPPORT"}\n\n',
          'event: fallback\ndata: {"from":"SUPPORT","to":"RAG","reason":"NO_MATCH"}\n\n',
          'event: done\ndata: {"conversation_id":"c-1","message_id":"m-1","trace_id":"t-1","answer":"RAG回答","response_type":"ANSWERED","execution_engine":"RAG","content":[],"references":[],"usage":{},"latency_ms":10}\n\n',
        ]),
      ),
    );
    const onStatus = vi.fn();
    const onFallback = vi.fn();

    await streamChat("kb-1", "问题", undefined, {
      onTrace: vi.fn(),
      onDelta: vi.fn(),
      onDone: vi.fn(),
      onError: vi.fn(),
      onStatus,
      onFallback,
    });

    expect(onStatus).toHaveBeenCalledWith({ stage: "gateway_routing", engine: "SUPPORT" });
    expect(onFallback).toHaveBeenCalledWith({
      from: "SUPPORT",
      to: "RAG",
      reason: "NO_MATCH",
    });
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

  it("clears and announces a rejected credential for chat requests", async () => {
    const rejected = vi.fn();
    window.addEventListener("rag-service-auth-rejected", rejected, { once: true });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: () => Promise.resolve({ error: { message: "服务凭证无效" } }),
      }),
    );

    await expect(
      streamChat("kb-1", "问题", undefined, {
        onTrace: vi.fn(),
        onDelta: vi.fn(),
        onDone: vi.fn(),
        onError: vi.fn(),
      }),
    ).rejects.toThrow("服务凭证无效");

    expect(sessionStorage.getItem("rag_service_token")).toBeNull();
    expect(rejected).toHaveBeenCalledOnce();
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

    expect(onError).toHaveBeenCalledWith(
      "上游超时",
      { code: "UPSTREAM_TIMEOUT", message: "上游超时" },
    );
  });

  it("preserves an SSE event split across arbitrary network chunks", async () => {
    const onDone = vi.fn();
    const frame =
      'event: done\ndata: {"conversation_id":"c-1","message_id":"m-1","trace_id":"t-1","answer":"完整回答","response_type":"ANSWERED","content":[],"references":[],"usage":{},"latency_ms":10}\n\n';
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse([frame.slice(0, 17), frame.slice(17, 63), frame.slice(63)]),
      ),
    );

    await streamChat("kb-1", "问题", undefined, {
      onTrace: vi.fn(),
      onDelta: vi.fn(),
      onDone,
      onError: vi.fn(),
    });

    expect(onDone).toHaveBeenCalledTimes(1);
    expect(onDone.mock.calls[0]?.[0].answer).toBe("完整回答");
  });

  it("accepts CRLF framing, optional field spaces and a CRLF split across chunks", async () => {
    const onDone = vi.fn();
    const frame =
      'event:done\r\ndata:{"conversation_id":"c-1","message_id":"m-1","trace_id":"t-1","answer":"CRLF回答","response_type":"ANSWERED","content":[],"references":[],"usage":{},"latency_ms":10}\r\n\r\n';
    const split = frame.indexOf("\r\n") + 1;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(sseResponse([frame.slice(0, split), frame.slice(split)])),
    );

    await streamChat("kb-1", "问题", undefined, {
      onTrace: vi.fn(),
      onDelta: vi.fn(),
      onDone,
      onError: vi.fn(),
    });

    expect(onDone).toHaveBeenCalledTimes(1);
    expect(onDone.mock.calls[0]?.[0].answer).toBe("CRLF回答");
  });

  it("flushes and dispatches a final event without a trailing blank line", async () => {
    const onDone = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse([
          'event: done\ndata: {"conversation_id":"c-1","message_id":"m-1","trace_id":"t-1","answer":"尾帧","response_type":"ANSWERED","content":[],"references":[],"usage":{},"latency_ms":10}',
        ]),
      ),
    );

    await streamChat("kb-1", "问题", undefined, {
      onTrace: vi.fn(),
      onDelta: vi.fn(),
      onDone,
      onError: vi.fn(),
    });

    expect(onDone).toHaveBeenCalledTimes(1);
    expect(onDone.mock.calls[0]?.[0].answer).toBe("尾帧");
  });

  it("reports a stream that ends without done or error", async () => {
    const onError = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse(['event: message\ndata: {"delta":"未完成"}\n\n']),
      ),
    );

    await streamChat("kb-1", "问题", undefined, {
      onTrace: vi.fn(),
      onDelta: vi.fn(),
      onDone: vi.fn(),
      onError,
    });

    expect(onError).toHaveBeenCalledOnce();
    expect(onError).toHaveBeenCalledWith("问答流在完成前已断开");
  });

  it("preserves a multibyte UTF-8 character split between byte chunks", async () => {
    const onDelta = vi.fn();
    const encoder = new TextEncoder();
    const bytes = encoder.encode(
      'event: message\ndata: {"delta":"中文"}\n\nevent: done\ndata: {"conversation_id":"c-1","message_id":"m-1","trace_id":"t-1","answer":"中文","response_type":"ANSWERED","content":[],"references":[],"usage":{},"latency_ms":10}\n\n',
    );
    const chineseStart = bytes.findIndex((value) => value > 127);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse([bytes.slice(0, chineseStart + 1), bytes.slice(chineseStart + 1)]),
      ),
    );

    await streamChat("kb-1", "问题", undefined, {
      onTrace: vi.fn(),
      onDelta,
      onDone: vi.fn(),
      onError: vi.fn(),
    });

    expect(onDelta).toHaveBeenCalledWith("中文");
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

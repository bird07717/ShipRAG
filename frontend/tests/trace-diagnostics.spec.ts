import { describe, expect, it } from "vitest";
import { record, traceDiagnostics } from "@/utils/traceDiagnostics";

describe("shared Trace diagnostics", () => {
  it("uses V2 rerank and adopted evidence, not legacy candidates", () => {
    const rerank = { status: "PASSED", after_document_ids: ["candidate-a", "candidate-b"], scores: [0.9, 0.7] };
    const result = traceDiagnostics({
      runtime_snapshot: { generation: { pipeline: "V2", response_path: "KNOWLEDGE", retrieval: { document_rerank: rerank }, answer: { used_document_ids: ["candidate-b"] } } },
      rerank_result: { candidates: [{ document_id: "wrong-legacy" }] },
    });
    expect(result.rerank).toEqual(rerank);
    expect(result.routeLabel).toBe("知识检索");
    expect(result.candidates).toEqual([{ document_id: "candidate-a", document_title: "candidate-a", rerank_score: 0.9 }, { document_id: "candidate-b", document_title: "candidate-b", rerank_score: 0.7 }]);
    expect(result.usedDocumentIds).toEqual(["candidate-b"]);
  });
  it("distinguishes missing adopted IDs from intentionally empty IDs", () => {
    const generation = { pipeline: "V2", response_path: "KNOWLEDGE", retrieval: { candidate_document_ids: ["a"] } };
    expect(traceDiagnostics({ runtime_snapshot: { generation } }).usedDocumentIds).toBeNull();
    expect(traceDiagnostics({ runtime_snapshot: { generation: { ...generation, answer: { used_document_ids: [] } } } }).usedDocumentIds).toEqual([]);
    expect(traceDiagnostics({ runtime_snapshot: { generation: { ...generation, answer: { used_document_ids: [123] } } } }).usedDocumentIds).toBeNull();
  });
  it("displays skipped conversation retrieval and input guards", () => {
    const generation = { pipeline: "V2", response_path: "CONVERSATION", retrieval: { status: "SKIPPED", reason: "CONVERSATION_ROUTE" } };
    const result = traceDiagnostics({ runtime_snapshot: { generation } });
    expect(result.routeLabel).toBe("对话（不检索）");
    expect(result.rerank).toEqual(generation.retrieval);
    expect(result.usedDocumentIds).toEqual([]);
    expect(traceDiagnostics({ runtime_snapshot: { generation: { pipeline: "V2", input_guard: "GREETING" } } }).routeLabel).toContain("输入短路");
    expect(traceDiagnostics({ runtime_snapshot: { generation: { pipeline: "V2" } } }).routeLabel).toBe("未记录");
  });
  it("keeps legacy routing, titles and scores", () => {
    const result = traceDiagnostics({ retrieval_result: { doc_routing: { decision: "CLARIFY", doc_scores: [{ document_id: "a", title: "旧标题" }] } }, rerank_result: { candidates: [{ document_id: "a", rerank_score: 0.8 }, { document_id: "b" }, {}] } });
    expect(result.generation).toBeNull();
    expect(result.routeLabel).toBe("请用户澄清");
    expect(result.candidates[0]).toMatchObject({ document_title: "旧标题", rerank_score: 0.8 });
    expect(result.candidates[1]).toMatchObject({ document_title: "b" });
    expect(result.candidates[2]).toMatchObject({ document_title: "未记录" });
    expect(traceDiagnostics({ retrieval_result: { doc_routing: { decision: "CUSTOM" } } }).routeLabel).toBe("CUSTOM");
  });
  it("tolerates expired, absent and malformed legacy snapshots", () => {
    for (const value of [null, [], "bad", {}]) {
      expect(record(value)).toEqual({});
      const result = traceDiagnostics(value);
      expect(result.generation).toBeNull();
      expect(result.routeLabel).toBe("未记录");
      expect(result.candidates).toEqual([]);
      expect(result.usedDocumentIds).toBeNull();
    }
  });
});

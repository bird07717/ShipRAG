/** Management-only diagnostics. Never infer adopted evidence from candidates. */
export function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
}

export interface V2Generation {
  pipeline?: string;
  status?: string;
  response_path?: "KNOWLEDGE" | "CONVERSATION";
  diagnostic_reason?: string;
  input_guard?: string;
  query_interpreter?: { status?: string; reason?: string; latency_ms?: number; route_fallback_reason?: string };
  retrieval?: Record<string, unknown> & { status?: string; reason?: string; degradations?: string[]; document_rerank?: Record<string, unknown> & { status?: string; reason?: string } };
  context?: { document_count?: number; document_ids?: string[]; image_count?: number; estimated_tokens?: number; mode?: string };
  answer?: { response_kind?: string; basis?: string; used_document_ids?: string[]; used_document_count?: number; used_image_count?: number; public_leak_repair_count?: number; unsupported_parameter_count?: number };
}

function ids(value: unknown): string[] | null {
  return Array.isArray(value) && value.every((item) => typeof item === "string") ? value : null;
}

export function traceDiagnostics(value: unknown) {
  const diagnostics = record(value);
  const raw = record(record(diagnostics.runtime_snapshot).generation);
  const generation = raw.pipeline === "V2" ? raw as V2Generation : null;
  const routing = record(record(diagnostics.retrieval_result).doc_routing);
  const rerank = generation
    ? generation.retrieval?.document_rerank ?? (generation.retrieval?.status === "SKIPPED" ? generation.retrieval : null)
    : record(diagnostics.rerank_result);
  const afterIds = ids(record(rerank).after_document_ids) ?? ids(generation?.retrieval?.candidate_document_ids) ?? [];
  const scores = record(rerank).scores;
  const legacyCandidates = record(diagnostics.rerank_result).candidates;
  const docScores = Array.isArray(routing.doc_scores) ? routing.doc_scores.map(record) : [];
  const candidates = generation
    ? afterIds.map((id, index) => ({ document_id: id, document_title: id, rerank_score: Array.isArray(scores) ? scores[index] : undefined }))
    : (Array.isArray(legacyCandidates) ? legacyCandidates.map((value) => {
      const candidate = record(value);
      const title = docScores.find((item) => item.document_id === candidate.document_id)?.title;
      return { ...candidate, document_title: title || candidate.document_id || "未记录" };
    }) : []);
  const routeLabel = generation
    ? generation.input_guard ? `输入短路（${generation.input_guard}）`
      : generation.response_path === "CONVERSATION" ? "对话（不检索）"
        : generation.response_path === "KNOWLEDGE" ? "知识检索" : "未记录"
    : ({ DELIVER: "交付文档", CLARIFY: "请用户澄清", NO_MATCH: "未确认匹配", STAY: "沿用焦点文档", SWITCH: "切换文档" }[String(routing.decision)] || String(routing.decision || "未记录"));
  const adopted = ids(generation?.answer?.used_document_ids);
  // Conversation/input guards intentionally use no document evidence. Missing
  // IDs on a legacy V2 knowledge trace remain unknown, not an empty evidence set.
  const usedDocumentIds = adopted ?? (generation && (generation.input_guard || generation.response_path === "CONVERSATION") ? [] : null);
  return { generation, routing, rerank, candidates, routeLabel, usedDocumentIds };
}

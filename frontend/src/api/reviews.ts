import { apiRequest } from "@/api/admin";
import type { AnswerDetail, AnswerRecord, AnswerRetest, ReviewInput } from "@/types/reviews";

const base = (kb: string) => `/knowledge-bases/${encodeURIComponent(kb)}`;
export const reviewsApi = {
  list: (kb: string, filters: { marked: boolean; status: string; query: string; offset: number }, signal?: AbortSignal) => {
    const params = new URLSearchParams({ marked: String(filters.marked), query: filters.query, offset: String(filters.offset), limit: "20" });
    if (filters.status) params.set("status", filters.status);
    return apiRequest<{ items: AnswerRecord[]; total: number }>(`${base(kb)}/answer-records?${params}`, { signal });
  },
  detail: (kb: string, request: string, signal?: AbortSignal) =>
    apiRequest<AnswerDetail>(`${base(kb)}/answer-records/${encodeURIComponent(request)}`, { signal }),
  save: (kb: string, request: string, value: ReviewInput) =>
    apiRequest<AnswerDetail>(`${base(kb)}/answer-records/${encodeURIComponent(request)}/review`, { method: "PUT", body: JSON.stringify(value) }),
  retest: (kb: string, review: string, revision: number, runId: string) =>
    apiRequest<AnswerRetest>(`${base(kb)}/answer-reviews/${encodeURIComponent(review)}/retests`, { method: "POST", body: JSON.stringify({ revision, run_id: runId }) }),
};

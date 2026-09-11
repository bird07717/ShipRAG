import type { ChatContentBlock, ChatSupportSection } from "@/api/chat";

export interface AnswerPayload {
  answer?: string;
  response_type?: string;
  content?: ChatContentBlock[];
  sections?: ChatSupportSection[];
  references?: Array<{ document_id: string; title: string }>;
  document?: { document_id?: string; id?: string; title?: string } | null;
  clarification?: { message?: string; choices?: Array<{ choice_id: string; title: string }> } | null;
  disclaimer?: string | null;
  next_suggestions?: string[];
  trace_id?: string;
  conversation_id?: string;
}
export interface AnswerSnapshot {
  request_id: string;
  knowledge_id: string;
  created_at: string;
  status: string;
  question: string;
  external_user_id: string;
  conversation_id: string | null;
  trace_id: string | null;
  response: AnswerPayload;
  error: Record<string, unknown>;
  history: Array<{ id: string; role: string; content: string; response_payload: AnswerPayload }>;
  history_complete: boolean;
  diagnostics: Record<string, unknown>;
}
export type ReviewStatus = "OPEN" | "VERIFY" | "CLOSED";
export type ReviewCategory = "WRONG_DOCUMENT" | "MISSING_KNOWLEDGE" | "INCOMPLETE_CONTENT" | "WRONG_ANSWER" | "OTHER" | "ACCEPTABLE";
export interface ReviewInput {
  revision: number;
  category: ReviewCategory;
  status: ReviewStatus;
  expected_document_id: string | null;
  expectation: string;
  notes: string;
}
export interface AnswerReview extends ReviewInput {
  id: string;
  knowledge_id: string;
  request_id: string;
  expected_document_title: string | null;
  snapshot: AnswerSnapshot;
}
export interface AnswerRetest {
  id: string;
  review_id: string;
  review_revision: number;
  status: "RUNNING" | "COMPLETED" | "FAILED";
  expectation: { expected_document_id: string | null; expected_document_title: string | null; expectation: string };
  turns: Array<{ question: string; response: AnswerPayload; diagnostics: Record<string, unknown> }>;
  error: string | null;
  created_at: string;
}
export interface AnswerDetail {
  snapshot: AnswerSnapshot;
  review: AnswerReview | null;
  retests: AnswerRetest[];
}
export interface AnswerRecord {
  request_id: string;
  question: string;
  answer: string;
  response_type: string | null;
  document: AnswerPayload["document"];
  external_user_id: string;
  request_status: string;
  review_id: string | null;
  review_status: ReviewStatus | null;
  replay_count: number;
  created_at: string;
}

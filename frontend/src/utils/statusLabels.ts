import type { DocumentCardState, TraceSource } from "@/types/admin";

/*
 * Shared display vocabulary for backend enums. Badges, filter dropdowns and
 * scope descriptions read the same table so one status never shows two names.
 */

export type StatusTone = "success" | "processing" | "warning" | "danger" | "neutral";

const STATUS_MAP: Record<string, { label: string; tone: StatusTone }> = {
  ACTIVE: { label: "线上生效", tone: "success" },
  READY: { label: "已就绪", tone: "success" },
  READY_TO_PUBLISH: { label: "可发布", tone: "success" },
  APPROVED: { label: "审核通过", tone: "success" },
  PUBLISHED: { label: "已发布", tone: "success" },
  HISTORICAL_PUBLISHED: { label: "历史已发布", tone: "neutral" },
  COMPLETED: { label: "已完成", tone: "success" },
  SUCCEEDED: { label: "已完成", tone: "success" },
  ENABLED: { label: "已启用", tone: "success" },
  INDEXED: { label: "已索引", tone: "success" },
  OK: { label: "正常", tone: "success" },
  BUILDING: { label: "构建中", tone: "processing" },
  RUNNING: { label: "运行中", tone: "processing" },
  QUEUED: { label: "排队中", tone: "processing" },
  VALIDATING: { label: "验证中", tone: "processing" },
  UPDATING: { label: "更新中", tone: "processing" },
  PARSING: { label: "解析中", tone: "processing" },
  CHUNKING: { label: "分块中", tone: "processing" },
  EMBEDDING: { label: "向量化中", tone: "processing" },
  PROCESSING_IMAGES: { label: "处理图片", tone: "processing" },
  UPLOADING: { label: "上传中", tone: "processing" },
  STORED: { label: "已存储", tone: "warning" },
  DEPRECATED: { label: "已废弃", tone: "warning" },
  PENDING: { label: "待处理", tone: "warning" },
  VALIDATION_FAILED: { label: "验证失败", tone: "danger" },
  REJECTED: { label: "退回修改", tone: "danger" },
  NOT_READY: { label: "未就绪", tone: "warning" },
  DRAFT: { label: "草稿", tone: "neutral" },
  CREATE: { label: "新增", tone: "success" },
  UPDATE: { label: "更新", tone: "warning" },
  NONE: { label: "仅供参考", tone: "neutral" },
  FALLBACK: { label: "保守生成", tone: "warning" },
  LLM: { label: "模型生成", tone: "processing" },
  MANUAL: { label: "人工修改", tone: "neutral" },
  PAUSED: { label: "已暂停", tone: "warning" },
  RUNTIME: { label: "运行时", tone: "neutral" },
  FAILED: { label: "失败", tone: "danger" },
  CANCELLED: { label: "已取消", tone: "neutral" },
  SKIPPED: { label: "已跳过", tone: "warning" },
  ERROR: { label: "错误", tone: "danger" },
  DISABLED: { label: "已停用", tone: "neutral" },
};

export function statusDisplay(status: string | null | undefined): { label: string; tone: StatusTone } {
  const key = (status ?? "").toUpperCase();
  return STATUS_MAP[key] ?? { label: status || "—", tone: "neutral" };
}

export function statusLabel(status: string | null | undefined): string {
  return statusDisplay(status).label;
}

/** Trace/conversation channels. One wording everywhere (Traces, Logs, Conversations). */
export const TRACE_SOURCE_LABELS: Record<TraceSource, string> = {
  ASSISTANT_API: "外部 Assistant",
  INTERNAL_CHAT: "内部问答测试 / 试问",
  PLAYGROUND: "调试台",
};

export const TRACE_SOURCE_TAG: Record<TraceSource, "success" | "info" | "warning"> = {
  ASSISTANT_API: "success",
  INTERNAL_CHAT: "info",
  PLAYGROUND: "warning",
};

export const TRACE_SOURCES = Object.keys(TRACE_SOURCE_LABELS) as TraceSource[];

export function isTraceSource(value: unknown): value is TraceSource {
  return typeof value === "string" && value in TRACE_SOURCE_LABELS;
}

const RESPONSE_TYPE_LABELS: Record<string, string> = {
  ANSWERED: "已回答",
  UNCONFIRMED: "待补充信息",
  OUT_OF_SCOPE: "范围外",
  DOC_DELIVERED: "文档交付（遗留）",
  DOCUMENT_DELIVERED: "文档交付（遗留）",
  CLARIFICATION_REQUIRED: "需要澄清（遗留）",
  NO_MATCH: "无匹配（遗留）",
  ESCALATION_REQUIRED: "需要升级（遗留）",
};

/** Chinese label followed by the raw code, so operators can still match Trace JSON. */
export function responseTypeLabel(type: string): string {
  const label = RESPONSE_TYPE_LABELS[type];
  return label ? `${label} · ${type}` : type;
}

/**
 * RAG card review state of a document (server `card_state`; the SQL mapping
 * lives in IngestionRepository._DOCUMENT_CARD_STATE_SQL).
 */
export const DOCUMENT_CARD_STATES: Record<
  DocumentCardState,
  { label: string; tag: "success" | "warning" | "danger" | "info" }
> = {
  MISSING: { label: "未生成", tag: "info" },
  GENERATING: { label: "生成中", tag: "warning" },
  FAILED: { label: "生成失败", tag: "danger" },
  PENDING_REVIEW: { label: "待确认", tag: "warning" },
  REJECTED: { label: "已拒绝", tag: "danger" },
  CONFIRMED: { label: "已确认", tag: "success" },
};

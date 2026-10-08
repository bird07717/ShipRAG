import type { ModelRuntimeItem, ModelRuntimeStatus } from "@/types/admin";

export const MODEL_TYPE_LABELS: Record<ModelRuntimeItem["model_type"], string> = {
  LLM: "LLM（回答生成）",
  EMBEDDING: "Embedding（向量检索与索引构建）",
  RERANK: "Rerank（文档重排）",
  OCR: "OCR（图片文字识别）",
  VISION: "Vision（图片理解）",
};

const REASON_LABELS: Record<string, string> = {
  FAKE_MODE: "运行配置指定为模拟模式（*_PROVIDER=fake）",
  AUTO_NO_KEY: "未找到 API Key，自动化测试环境（APP_ENV=test）使用模拟模型",
  NO_API_KEY: "未配置 API Key，调用会失败（不会自动改用模拟模型）",
  FAKE_FORBIDDEN: "预发/生产环境禁止模拟模型（*_PROVIDER=fake），调用会失败",
  MODEL_NOT_ENABLED: "没有启用该类型的模型，问答会被拒绝",
  UNSUPPORTED_PROVIDER: "提供方不受支持，无法调用",
  EXPLICIT_PROVIDER: "指定了提供方但没有任何 API Key，调用会失败",
  MODE_DISABLED: "运行配置已停用该能力",
  CONFIGURED: "使用真实模型",
};

export function reasonLabel(reason: string): string {
  return REASON_LABELS[reason] ?? reason;
}

export interface ModelRuntimeIssue {
  item: ModelRuntimeItem;
  severity: "simulated" | "unavailable";
  label: string;
  detail: string;
}

/**
 * Models whose real behaviour differs from "a configured real model":
 * simulated (results look plausible but are not real) or unavailable
 * (calls fail). DISABLED is a deliberate configuration and not an issue.
 */
export function modelRuntimeIssues(status: ModelRuntimeStatus | null): ModelRuntimeIssue[] {
  if (!status) return [];
  return status.models.flatMap((item): ModelRuntimeIssue[] => {
    const label = MODEL_TYPE_LABELS[item.model_type] ?? item.model_type;
    const target = [item.provider, item.model_name].filter(Boolean).join(" · ");
    const fallback = item.source === "ENV_FALLBACK" ? "；数据库中启用的提供方不受支持，已改用环境默认提供方" : "";
    const detail = `${reasonLabel(item.reason)}${target ? `（${target}）` : ""}${fallback}`;
    if (item.kind === "FAKE") return [{ item, severity: "simulated", label, detail }];
    if (
      item.kind === "NOT_CONFIGURED"
      || item.kind === "UNAVAILABLE"
      || (item.kind === "REAL" && item.reason === "EXPLICIT_PROVIDER")
    ) {
      return [{ item, severity: "unavailable", label, detail }];
    }
    return [];
  });
}

export const ENVIRONMENT_LABELS: Record<string, { label: string; tone: "production" | "staging" | "development" | "test" }> = {
  production: { label: "生产环境", tone: "production" },
  staging: { label: "预发布环境", tone: "staging" },
  development: { label: "开发环境", tone: "development" },
  test: { label: "测试环境", tone: "test" },
};

export function environmentBadge(environment: string | null | undefined): { label: string; tone: string } | null {
  if (!environment) return null;
  return ENVIRONMENT_LABELS[environment] ?? { label: environment, tone: "test" };
}

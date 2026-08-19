import type { ReadinessResponse } from "@/types/health";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

export async function fetchReadiness(signal?: AbortSignal): Promise<ReadinessResponse> {
  const response = await fetch(`${apiBaseUrl}/health/ready`, {
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok && response.status !== 503) {
    throw new Error(`健康检查请求失败：HTTP ${response.status}`);
  }
  return (await response.json()) as ReadinessResponse;
}

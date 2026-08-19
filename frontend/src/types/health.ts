export type DependencyStatus = "ok" | "error";

export interface DependencyCheck {
  status: DependencyStatus;
  latency_ms: number;
  detail?: string;
  version?: string;
}

export interface ReadinessResponse {
  status: "ready" | "not_ready";
  checks: Record<"postgres" | "redis" | "minio", DependencyCheck>;
}

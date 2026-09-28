export type ComponentStatus = "ok" | "degraded" | "down";
export type ModelsStatus = "ready" | "missing" | "not_required";

export interface RedisHealth {
  status: ComponentStatus;
  detail: string | null;
}

export interface WorkersHealth {
  status: ComponentStatus;
  count: number;
  queue: string;
  detail: string | null;
}

export interface ModelsHealth {
  status: ModelsStatus;
  asr_model: string;
  device: string;
  detail: string | null;
}

export interface HealthLimits {
  max_upload_mb: number;
  allowed_extensions: string[];
}

export interface HealthResponse {
  status: ComponentStatus;
  version: string;
  app_env: string;
  redis: RedisHealth;
  workers: WorkersHealth;
  models: ModelsHealth;
  limits: HealthLimits;
}

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const response = await fetch(`${API_BASE_URL}/api/v1/health`, {
    cache: "no-store",
    signal,
  });
  if (!response.ok) {
    throw new Error(`API responded with ${response.status}`);
  }
  return (await response.json()) as HealthResponse;
}

import { API_BASE_URL } from "@/lib/api";

export const JOB_STATUSES = [
  "QUEUED",
  "PREPARING_AUDIO",
  "DIARIZING",
  "TRANSCRIBING",
  "ALIGNING",
  "GENERATING_EXPORTS",
  "COMPLETED",
  "FAILED",
] as const;

export type JobStatus = (typeof JOB_STATUSES)[number];

export type ErrorCode =
  | "UNSUPPORTED_FORMAT"
  | "FILE_TOO_LARGE"
  | "FFMPEG_FAILED"
  | "MODEL_NOT_AVAILABLE"
  | "HF_TOKEN_REQUIRED"
  | "DIARIZATION_FAILED"
  | "ASR_FAILED"
  | "EXPORT_FAILED"
  | "JOB_NOT_FOUND";

export interface JobSource {
  filename: string;
  size_bytes: number;
  content_type: string | null;
}

export interface Job {
  job_id: string;
  status: JobStatus;
  progress: number;
  current_stage: JobStatus;
  message: string | null;
  error_code: ErrorCode | null;
  source: JobSource;
  speaker_count: number | null;
  created_at: string;
  updated_at: string;
}

export const TERMINAL_STATUSES: ReadonlySet<JobStatus> = new Set<JobStatus>([
  "COMPLETED",
  "FAILED",
]);

export const STAGE_LABELS: Record<JobStatus, string> = {
  QUEUED: "В очереди",
  PREPARING_AUDIO: "Подготовка аудио",
  DIARIZING: "Разделение по спикерам",
  TRANSCRIBING: "Распознавание речи",
  ALIGNING: "Сопоставление слов и спикеров",
  GENERATING_EXPORTS: "Формирование файлов",
  COMPLETED: "Готово",
  FAILED: "Ошибка",
};

const ERROR_MESSAGES: Record<ErrorCode, string> = {
  UNSUPPORTED_FORMAT: "Формат файла не поддерживается.",
  FILE_TOO_LARGE: "Файл слишком большой.",
  FFMPEG_FAILED: "Не удалось обработать аудио.",
  MODEL_NOT_AVAILABLE: "Модель недоступна.",
  HF_TOKEN_REQUIRED: "Нужен HF_TOKEN для загрузки модели диаризации.",
  DIARIZATION_FAILED: "Не удалось разделить запись по спикерам.",
  ASR_FAILED: "Не удалось распознать речь.",
  EXPORT_FAILED: "Не удалось сформировать файлы результата.",
  JOB_NOT_FOUND: "Задача не найдена.",
};

export function describeError(
  code: ErrorCode | null | undefined,
  fallback?: string | null,
): string {
  if (code && code in ERROR_MESSAGES) {
    return ERROR_MESSAGES[code];
  }
  return fallback?.trim() || "Неизвестная ошибка.";
}

export class ApiError extends Error {
  readonly code: ErrorCode | null;
  readonly status: number;

  constructor(status: number, code: ErrorCode | null, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

async function toApiError(response: Response): Promise<ApiError> {
  let code: ErrorCode | null = null;
  let message = `Ошибка запроса (${response.status})`;
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object") {
      const payload = body as { error_code?: ErrorCode; message?: string; detail?: unknown };
      code = payload.error_code ?? null;
      if (typeof payload.message === "string") {
        message = payload.message;
      } else if (typeof payload.detail === "string") {
        message = payload.detail;
      }
    }
  } catch {
    // Response body was not JSON — keep the generic message.
  }
  return new ApiError(response.status, code, describeError(code, message));
}

export interface CreateJobInput {
  file: File;
  speakerCount: number | null;
}

export async function createJob({ file, speakerCount }: CreateJobInput): Promise<string> {
  const form = new FormData();
  form.append("file", file);
  if (speakerCount !== null) {
    form.append("speaker_count", String(speakerCount));
  }

  const response = await fetch(`${API_BASE_URL}/api/v1/jobs`, {
    method: "POST",
    body: form,
  });
  if (!response.ok) {
    throw await toApiError(response);
  }
  const body = (await response.json()) as { job_id: string };
  return body.job_id;
}

export async function fetchJob(jobId: string, signal?: AbortSignal): Promise<Job> {
  const response = await fetch(`${API_BASE_URL}/api/v1/jobs/${jobId}`, {
    cache: "no-store",
    signal,
  });
  if (!response.ok) {
    throw await toApiError(response);
  }
  return (await response.json()) as Job;
}

export function jobEventsUrl(jobId: string): string {
  return `${API_BASE_URL}/api/v1/jobs/${jobId}/events`;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} Б`;
  const units = ["КБ", "МБ", "ГБ"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`;
}

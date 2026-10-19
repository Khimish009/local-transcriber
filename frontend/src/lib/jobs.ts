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
  | "JOB_NOT_FOUND"
  | "RESULT_NOT_READY"
  | "WORKER_CRASHED";

export interface JobSource {
  filename: string;
  size_bytes: number;
  content_type: string | null;
}

export interface AudioMetadata {
  duration_seconds: number;
  format_name: string | null;
  codec_name: string | null;
  sample_rate: number | null;
  channels: number | null;
}

export interface Job {
  job_id: string;
  status: JobStatus;
  progress: number;
  current_stage: JobStatus;
  message: string | null;
  error_code: ErrorCode | null;
  source: JobSource;
  /** Filled in once the audio preparation stage has probed the recording. */
  audio: AudioMetadata | null;
  speaker_count: number | null;
  created_at: string;
  updated_at: string;
  /** Null until the worker picks the job up; null for records written before this existed. */
  started_at: string | null;
  finished_at: string | null;
  /**
   * Computed by the server at the moment it sent the record — up to now for a running job.
   * Null when unknown (queued, or failed before the worker picked it up).
   */
  processing_seconds: number | null;
  /** The figure is a legacy created → updated span that also covers queue time. */
  processing_includes_queue: boolean;
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
  RESULT_NOT_READY: "Результат ещё не готов.",
  WORKER_CRASHED: "Обработка прервана: worker перезапустился. Запустите задачу заново.",
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

export async function toApiError(response: Response): Promise<ApiError> {
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

/** T8.3 — manual cleanup: removes the record, the queue entry and the files. */
export async function deleteJob(jobId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/v1/jobs/${jobId}`, {
    method: "DELETE",
  });
  if (!response.ok && response.status !== 404) {
    throw await toApiError(response);
  }
}

/** T11.1 — every job, newest first. Survives a page reload; the job list links to each. */
export async function fetchJobs(signal?: AbortSignal): Promise<Job[]> {
  const response = await fetch(`${API_BASE_URL}/api/v1/jobs`, {
    cache: "no-store",
    signal,
  });
  if (!response.ok) {
    throw await toApiError(response);
  }
  return (await response.json()) as Job[];
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

export interface ProcessingTime {
  seconds: number;
  /** Also covers upload and time spent waiting in the queue: an old record, no exact stamps. */
  includesQueue: boolean;
}

/**
 * Processing time as shown to the user (docs/processing-time.md).
 *
 * The server does the arithmetic. For a running job its figure is "up to the moment the
 * record was sent", so the time since `receivedAt` is added on top. Both of those are
 * browser-clock readings, so a skew between the browser and the server cancels out.
 * Without `clock`, a running job's figure is returned as the server sent it.
 */
export function processingTime(
  job: Job,
  clock?: { receivedAt: number; now: number },
): ProcessingTime | null {
  if (job.processing_seconds === null) return null;
  const running = !TERMINAL_STATUSES.has(job.status);
  const extra = running && clock ? Math.max(0, clock.now - clock.receivedAt) / 1000 : 0;
  return { seconds: job.processing_seconds + extra, includesQueue: job.processing_includes_queue };
}

/** "2 ч 14 мин" / "3 мин 20 с" / "45 с" — a wall-clock span, not a media position. */
export function formatElapsed(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours > 0) return `${hours} ч ${minutes} мин`;
  if (minutes > 0) return `${minutes} мин ${secs} с`;
  return `${secs} с`;
}

export function formatDuration(seconds: number): string {
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const pad = (value: number) => String(value).padStart(2, "0");
  return hours > 0 ? `${hours}:${pad(minutes)}:${pad(secs)}` : `${minutes}:${pad(secs)}`;
}

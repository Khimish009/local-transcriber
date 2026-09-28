import { API_BASE_URL } from "@/lib/api";
import { toApiError } from "@/lib/jobs";

export const EXPORT_FORMATS = ["json", "txt", "docx", "pdf"] as const;
export type ExportFormat = (typeof EXPORT_FORMATS)[number];

export interface EngineInfo {
  engine: string;
  model: string;
}

export interface TranscriptSource {
  filename: string;
  duration_seconds: number | null;
}

export interface TranscriptSpeaker {
  id: string;
  display_name: string;
}

export interface TranscriptWord {
  start: number;
  end: number;
  text: string;
  /** null when the word could not be attributed to a speaker confidently. */
  speaker_id: string | null;
}

export interface TranscriptSegment {
  id: string;
  start: number;
  end: number;
  speaker_id: string | null;
  text: string;
}

export interface Transcript {
  version: number;
  job_id: string;
  source: TranscriptSource;
  language: string;
  asr: EngineInfo;
  diarization: EngineInfo;
  speakers: TranscriptSpeaker[];
  words: TranscriptWord[];
  segments: TranscriptSegment[];
  created_at: string;
}

/** Shown for segments the pipeline could not attribute. Mirrors the backend label. */
export const UNKNOWN_SPEAKER = "Спикер не определён";

export function speakerName(transcript: Transcript, speakerId: string | null): string {
  if (!speakerId) return UNKNOWN_SPEAKER;
  return transcript.speakers.find((speaker) => speaker.id === speakerId)?.display_name ?? speakerId;
}

export function formatTimecode(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(Math.floor(total / 3600))}:${pad(Math.floor((total % 3600) / 60))}:${pad(total % 60)}`;
}

export async function fetchTranscript(jobId: string, signal?: AbortSignal): Promise<Transcript> {
  const response = await fetch(`${API_BASE_URL}/api/v1/jobs/${jobId}/transcript`, {
    cache: "no-store",
    signal,
  });
  if (!response.ok) {
    throw await toApiError(response);
  }
  return (await response.json()) as Transcript;
}

/** Renames speakers. The backend rewrites transcript.json and regenerates the exports. */
export async function renameSpeakers(
  jobId: string,
  names: Record<string, string>,
): Promise<Transcript> {
  const response = await fetch(`${API_BASE_URL}/api/v1/jobs/${jobId}/speakers`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(names),
  });
  if (!response.ok) {
    throw await toApiError(response);
  }
  return (await response.json()) as Transcript;
}

export function audioUrl(jobId: string): string {
  return `${API_BASE_URL}/api/v1/jobs/${jobId}/audio`;
}

export function downloadUrl(jobId: string, format: ExportFormat): string {
  return `${API_BASE_URL}/api/v1/jobs/${jobId}/download/${format}`;
}

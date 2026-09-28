"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/lib/jobs";
import { fetchTranscript, renameSpeakers, type Transcript } from "@/lib/transcript";

export interface TranscriptState {
  transcript: Transcript | null;
  error: string | null;
  loading: boolean;
  saving: boolean;
  /** Renames speakers server-side; the canonical JSON and the exports are rebuilt there. */
  rename: (names: Record<string, string>) => Promise<void>;
}

export function useTranscript(jobId: string | null): TranscriptState {
  const [transcript, setTranscript] = useState<Transcript | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!jobId) {
      setTranscript(null);
      setError(null);
      return;
    }

    const controller = new AbortController();
    setLoading(true);
    fetchTranscript(jobId, controller.signal)
      .then((next) => {
        setTranscript(next);
        setError(null);
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        setError(cause instanceof ApiError ? cause.message : "Не удалось загрузить транскрипт.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    return () => controller.abort();
  }, [jobId]);

  const rename = useCallback(
    async (names: Record<string, string>) => {
      if (!jobId) return;
      setSaving(true);
      try {
        setTranscript(await renameSpeakers(jobId, names));
        setError(null);
      } catch (cause: unknown) {
        setError(cause instanceof ApiError ? cause.message : "Не удалось сохранить имена.");
      } finally {
        setSaving(false);
      }
    },
    [jobId],
  );

  return { transcript, error, loading, saving, rename };
}

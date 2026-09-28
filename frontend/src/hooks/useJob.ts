"use client";

import { useEffect, useState } from "react";

import {
  ApiError,
  fetchJob,
  jobEventsUrl,
  TERMINAL_STATUSES,
  type Job,
} from "@/lib/jobs";

const POLL_FALLBACK_MS = 2000;

export interface JobState {
  job: Job | null;
  error: string | null;
  /** True while SSE is unavailable and the UI falls back to polling. */
  degraded: boolean;
}

/** Follows a job through SSE, falling back to polling if the stream drops. */
export function useJob(jobId: string | null): JobState {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [degraded, setDegraded] = useState(false);

  useEffect(() => {
    if (!jobId) {
      setJob(null);
      setError(null);
      setDegraded(false);
      return;
    }

    let closed = false;
    let source: EventSource | null = null;
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    const controller = new AbortController();

    const finish = () => {
      closed = true;
      source?.close();
      if (pollTimer) clearInterval(pollTimer);
      controller.abort();
    };

    const apply = (next: Job) => {
      setJob(next);
      setError(null);
      if (TERMINAL_STATUSES.has(next.status)) {
        finish();
      }
    };

    const poll = async () => {
      try {
        const next = await fetchJob(jobId, controller.signal);
        if (!closed) apply(next);
      } catch (cause: unknown) {
        if (closed || controller.signal.aborted) return;
        setError(cause instanceof ApiError ? cause.message : "Не удалось получить статус задачи.");
      }
    };

    const startPolling = () => {
      if (pollTimer || closed) return;
      setDegraded(true);
      void poll();
      pollTimer = setInterval(() => void poll(), POLL_FALLBACK_MS);
    };

    // Initial snapshot so the UI has data even before the first SSE event.
    void poll();

    source = new EventSource(jobEventsUrl(jobId));
    source.addEventListener("job", (event) => {
      apply(JSON.parse((event as MessageEvent<string>).data) as Job);
    });
    source.addEventListener("done", (event) => {
      apply(JSON.parse((event as MessageEvent<string>).data) as Job);
      finish();
    });
    // The job was deleted while we were watching it — stop, do not fall back to polling.
    source.addEventListener("gone", finish);
    source.onerror = () => {
      // The browser retries on its own; polling keeps progress visible meanwhile.
      if (!closed) startPolling();
    };

    return finish;
  }, [jobId]);

  return { job, error, degraded };
}

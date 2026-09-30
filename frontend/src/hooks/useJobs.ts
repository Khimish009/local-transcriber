"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError, fetchJobs, TERMINAL_STATUSES, type Job } from "@/lib/jobs";

const POLL_INTERVAL_MS = 5000;

export interface JobsState {
  jobs: Job[];
  error: string | null;
  loading: boolean;
  refresh: () => void;
}

/** The job list. Polls only while something is still running, so an idle page stays quiet. */
export function useJobs(): JobsState {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => setTick((value) => value + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;

    fetchJobs(controller.signal)
      .then((next) => {
        if (cancelled) return;
        setJobs(next);
        setError(null);
      })
      .catch((cause: unknown) => {
        if (cancelled || controller.signal.aborted) return;
        setError(cause instanceof ApiError ? cause.message : "Не удалось получить список задач.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [tick]);

  const active = jobs.some((job) => !TERMINAL_STATUSES.has(job.status));

  useEffect(() => {
    if (!active) return;
    const id = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [active, refresh]);

  return { jobs, error, loading, refresh };
}

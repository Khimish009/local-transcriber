"use client";

import { useCallback, useEffect, useState } from "react";

import { fetchHealth, type HealthResponse } from "@/lib/api";

const POLL_INTERVAL_MS = 10_000;
// A single "down" can be a blip (one Redis hiccup, a heartbeat thread that ran late); the
// warning it triggers says the job will have to be restarted, so it needs a second opinion.
// Costs one poll interval of detection delay for a worker that really died.
const DOWN_REPORTS_TO_WARN = 2;

export interface HealthState {
  data: HealthResponse | null;
  error: string | null;
  loading: boolean;
  /** Workers reported not ok by several health checks in a row. */
  workersDown: boolean;
  refresh: () => void;
}

export function useHealth(): HealthState {
  const [data, setData] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [downStreak, setDownStreak] = useState(0);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => setTick((value) => value + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;

    setLoading(true);
    fetchHealth(controller.signal)
      .then((response) => {
        if (cancelled) return;
        setData(response);
        setError(null);
        setDownStreak((streak) => (response.workers.status === "ok" ? 0 : streak + 1));
      })
      .catch((cause: unknown) => {
        if (cancelled || controller.signal.aborted) return;
        setData(null);
        setError(cause instanceof Error ? cause.message : "Unknown error");
        setDownStreak(0); // API unreachable says nothing about the worker
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [tick]);

  useEffect(() => {
    const id = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  return { data, error, loading, workersDown: downStreak >= DOWN_REPORTS_TO_WARN, refresh };
}

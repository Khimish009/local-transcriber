"use client";

import { useCallback, useEffect, useState } from "react";

import { fetchHealth, type HealthResponse } from "@/lib/api";

const POLL_INTERVAL_MS = 10_000;

export interface HealthState {
  data: HealthResponse | null;
  error: string | null;
  loading: boolean;
  refresh: () => void;
}

export function useHealth(): HealthState {
  const [data, setData] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
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
      })
      .catch((cause: unknown) => {
        if (cancelled || controller.signal.aborted) return;
        setData(null);
        setError(cause instanceof Error ? cause.message : "Unknown error");
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

  return { data, error, loading, refresh };
}

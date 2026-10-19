"use client";

import { useRouter } from "next/navigation";

import { JobProgress } from "@/components/JobProgress";
import { useHealth } from "@/hooks/useHealth";

/** A single job on its own route. "Back" and "delete" both return to the list. */
export function JobView({ jobId }: { jobId: string }) {
  const router = useRouter();
  const { workersDown } = useHealth();

  return (
    <JobProgress jobId={jobId} workersDown={workersDown} onReset={() => router.push("/")} />
  );
}

"use client";

import { useRouter } from "next/navigation";

import { JobProgress } from "@/components/JobProgress";

/** A single job on its own route. "Back" and "delete" both return to the list. */
export function JobView({ jobId }: { jobId: string }) {
  const router = useRouter();

  return <JobProgress jobId={jobId} onReset={() => router.push("/")} />;
}

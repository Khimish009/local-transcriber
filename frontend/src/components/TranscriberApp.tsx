"use client";

import { useState } from "react";

import { HealthPanel } from "@/components/HealthPanel";
import { JobProgress } from "@/components/JobProgress";
import { UploadForm, type UploadLimits } from "@/components/UploadForm";
import { useHealth } from "@/hooks/useHealth";

export function TranscriberApp() {
  const health = useHealth();
  const [jobId, setJobId] = useState<string | null>(null);

  const limits: UploadLimits | null = health.data
    ? {
        maxUploadMb: health.data.limits.max_upload_mb,
        allowedExtensions: health.data.limits.allowed_extensions,
      }
    : null;

  const workersDown = health.data?.workers.status !== "ok" && health.data !== null;

  return (
    <>
      {workersDown && (
        <p className="warning" role="status">
          Worker недоступен — задачи будут стоять в очереди.
        </p>
      )}

      {jobId ? (
        <JobProgress jobId={jobId} onReset={() => setJobId(null)} />
      ) : (
        <UploadForm limits={limits} onCreated={setJobId} />
      )}

      <HealthPanel state={health} />
    </>
  );
}

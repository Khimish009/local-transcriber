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

  const workersDown = health.data !== null && health.data.workers.status !== "ok";
  const modelsMissing = health.data !== null && health.data.models.status === "missing";

  return (
    <>
      {workersDown && (
        <p className="warning" role="status">
          Worker недоступен — задачи будут стоять в очереди.
        </p>
      )}

      {modelsMissing && (
        <p className="warning" role="status">
          Модель диаризации ещё не скачана. Укажите <code>HF_TOKEN</code> в <code>.env</code> и
          примите условия модели на Hugging Face — иначе задача остановится на этапе разделения по
          спикерам.
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

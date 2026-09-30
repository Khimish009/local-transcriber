"use client";

import { useRouter } from "next/navigation";

import { HealthPanel } from "@/components/HealthPanel";
import { JobList } from "@/components/JobList";
import { UploadForm, type UploadLimits } from "@/components/UploadForm";
import { useHealth } from "@/hooks/useHealth";

export function TranscriberApp() {
  const health = useHealth();
  const router = useRouter();

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

      {/* The job keeps its own URL, so a reload or a shared link still finds the result. */}
      <UploadForm limits={limits} onCreated={(jobId) => router.push(`/jobs/${jobId}`)} />

      <JobList />

      <HealthPanel state={health} />
    </>
  );
}

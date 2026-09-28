"use client";

import { TranscriptView } from "@/components/TranscriptView";
import { useJob } from "@/hooks/useJob";
import { describeError, formatBytes, formatDuration, STAGE_LABELS } from "@/lib/jobs";

interface Props {
  jobId: string;
  onReset: () => void;
}

export function JobProgress({ jobId, onReset }: Props) {
  const { job, error, degraded } = useJob(jobId);

  if (error && !job) {
    return (
      <section className="panel">
        <h2>Обработка</h2>
        <p className="error" role="alert">
          {error}
        </p>
        <button type="button" onClick={onReset}>
          Назад
        </button>
      </section>
    );
  }

  if (!job) {
    return (
      <section className="panel">
        <h2>Обработка</h2>
        <p className="muted">Загрузка статуса…</p>
      </section>
    );
  }

  const failed = job.status === "FAILED";
  const completed = job.status === "COMPLETED";

  return (
    <section className="panel">
      <header className="panel__header">
        <h2>{job.source.filename}</h2>
        <button type="button" onClick={onReset}>
          {completed || failed ? "Новая запись" : "Отменить просмотр"}
        </button>
      </header>

      <p className="muted">
        {[
          formatBytes(job.source.size_bytes),
          job.audio ? `длительность ${formatDuration(job.audio.duration_seconds)}` : null,
          job.speaker_count
            ? `${job.speaker_count} спикера(ов)`
            : "спикеры определяются автоматически",
        ]
          .filter(Boolean)
          .join(" · ")}
      </p>

      {!completed && (
        <>
          <div
            className="progress"
            role="progressbar"
            aria-valuenow={job.progress}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <div
              className={`progress__bar${failed ? " progress__bar--failed" : ""}`}
              style={{ width: `${job.progress}%` }}
            />
          </div>

          <div className="row">
            <div className="row__label">{STAGE_LABELS[job.current_stage]}</div>
            <span className="badge badge--ok">{job.progress}%</span>
            <div className="row__detail">{degraded ? "SSE недоступен, опрос по таймеру" : ""}</div>
          </div>
        </>
      )}

      {failed && (
        <p className="error" role="alert">
          {describeError(job.error_code, job.message)}
        </p>
      )}

      {completed && <TranscriptView jobId={job.job_id} />}
    </section>
  );
}

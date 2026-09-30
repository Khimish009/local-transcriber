"use client";

import { useEffect, useState } from "react";

import { TranscriptView } from "@/components/TranscriptView";
import { useJob } from "@/hooks/useJob";
import {
  ApiError,
  deleteJob,
  describeError,
  elapsedTime,
  formatBytes,
  formatDuration,
  formatElapsed,
  processingTime,
  STAGE_LABELS,
  TERMINAL_STATUSES,
  type Job,
} from "@/lib/jobs";

interface Props {
  jobId: string;
  onReset: () => void;
}

/** One line about time: how long it is taking, or how long it took. */
function describeTiming(job: Job, now: number): string {
  // No speed estimate while it runs: progress is not linear in audio time across stages,
  // so any "×N faster than realtime" shown mid-job would be made up.
  const running = elapsedTime(job, now);
  if (running !== null) return `Идёт ${formatElapsed(running)}`;

  const done = processingTime(job);
  if (done === null) return "";

  const verb = job.status === "FAILED" ? "Остановилась через" : "Расшифровка заняла";
  const note = done.exact
    ? compareToRecording(job, done.seconds)
    : " — вместе с ожиданием в очереди";
  return `${verb} ${formatElapsed(done.seconds)}${note}`;
}

/** "быстрее записи в 1.7 раза" — states the direction, so the ratio cannot be read backwards. */
function compareToRecording(job: Job, seconds: number): string {
  if (!job.audio || seconds <= 0 || job.status === "FAILED") return "";
  const ratio = job.audio.duration_seconds / seconds;
  if (ratio >= 1) return ` — быстрее записи в ${ratio.toFixed(1)} раза`;
  return ` — медленнее записи в ${(1 / ratio).toFixed(1)} раза`;
}

export function JobProgress({ jobId, onReset }: Props) {
  const { job, error, degraded } = useJob(jobId);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  // The running clock ticks on its own: progress events arrive far too irregularly to
  // drive it, and a stalled stage would otherwise freeze the elapsed time.
  const running = job !== null && !TERMINAL_STATUSES.has(job.status);
  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [running]);

  const handleDelete = async () => {
    if (!window.confirm("Удалить задачу вместе с записью и результатами?")) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteJob(jobId);
      onReset();
    } catch (cause: unknown) {
      setDeleteError(cause instanceof ApiError ? cause.message : "Не удалось удалить задачу.");
      setDeleting(false);
    }
  };

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
        <div className="panel__actions">
          <button type="button" onClick={onReset} disabled={deleting}>
            Новая запись
          </button>
          <button
            type="button"
            className="danger"
            onClick={() => void handleDelete()}
            disabled={deleting}
          >
            {deleting ? "Удаление…" : "Удалить задачу"}
          </button>
        </div>
      </header>

      {deleteError && (
        <p className="error" role="alert">
          {deleteError}
        </p>
      )}

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

      <p className="muted">{describeTiming(job, now)}</p>

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

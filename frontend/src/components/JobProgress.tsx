"use client";

import { useEffect, useState } from "react";

import { TranscriptView } from "@/components/TranscriptView";
import { useJob } from "@/hooks/useJob";
import {
  ApiError,
  deleteJob,
  describeError,
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
  /** From /health: nobody is processing jobs right now. */
  workersDown: boolean;
  onReset: () => void;
}

/** One line about time: how long it is taking, or how long it took. */
function describeTiming(job: Job, receivedAt: number, now: number, stalled: boolean): string {
  const spent = processingTime(job, { receivedAt, now });
  if (spent === null) return "";

  // No speed estimate while it runs: progress is not linear in audio time across stages,
  // so any "×N faster than realtime" shown mid-job would be made up.
  if (!TERMINAL_STATUSES.has(job.status)) {
    // No duration for a stalled job: the server measures up to "now", so the figure would
    // absorb all the downtime since the crash and keep growing with every poll. The last
    // update is a server stamp, the one fact known for sure.
    return stalled
      ? `Последнее обновление в ${formatClock(job.updated_at)}: worker недоступен`
      : `Идёт ${formatElapsed(spent.seconds)}`;
  }

  const verb =
    job.error_code === "WORKER_CRASHED"
      ? // Recovery stamps the last update, not the crash itself: a lower bound.
        "Остановилась не раньше чем через"
      : job.status === "FAILED"
        ? "Остановилась через"
        : "Расшифровка заняла";
  const note = spent.includesQueue
    ? " — вместе с ожиданием в очереди"
    : compareToRecording(job, spent.seconds);
  return `${verb} ${formatElapsed(spent.seconds)}${note}`;
}

function formatClock(iso: string): string {
  return new Date(iso).toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const RATIO_FORMAT = new Intl.NumberFormat("ru-RU", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

/** "быстрее записи в 1,7 раза" — states the direction, so the ratio cannot be read backwards. */
function compareToRecording(job: Job, seconds: number): string {
  if (!job.audio || seconds <= 0 || job.status === "FAILED") return "";
  const ratio = job.audio.duration_seconds / seconds;
  // Rounded once, so the parity check and the printed number cannot disagree.
  const factor = Math.round((ratio >= 1 ? ratio : 1 / ratio) * 10) / 10;
  // Near parity both directions would read "в 1,0 раза", which says nothing either way.
  if (factor <= 1) return " — примерно за длительность записи";
  const direction = ratio >= 1 ? "быстрее" : "медленнее";
  return ` — ${direction} записи в ${RATIO_FORMAT.format(factor)} раза`;
}

export function JobProgress({ jobId, workersDown, onReset }: Props) {
  const { job, receivedAt, error, degraded } = useJob(jobId);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  // The running clock ticks on its own: progress events arrive far too irregularly to
  // drive it, and a stalled stage would otherwise freeze the elapsed time. Nothing to tick
  // while the job is queued: there is no processing time yet, only an empty line. Nor
  // while the worker is down: a growing counter would pass a dead job off as a live one.
  const unfinished = job !== null && !TERMINAL_STATUSES.has(job.status);
  const stalled = unfinished && workersDown;
  const running = unfinished && !stalled && job.processing_seconds !== null;
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

  const timing = describeTiming(job, receivedAt, now, stalled);
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

      {stalled && (
        <p className="warning" role="status">
          {job.processing_seconds === null
            ? "Worker недоступен — задача стоит в очереди и начнётся, когда он вернётся."
            : "Worker недоступен — обработка не идёт. После его перезапуска задача будет помечена как прерванная, и её придётся запустить заново."}
        </p>
      )}

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

      {timing && <p className="muted">{timing}</p>}

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

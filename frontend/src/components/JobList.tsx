"use client";

import Link from "next/link";

import { useJobs } from "@/hooks/useJobs";
import { formatBytes, formatDuration, STAGE_LABELS, type Job } from "@/lib/jobs";

const TONE_BY_STATUS: Record<string, string> = {
  COMPLETED: "ok",
  FAILED: "bad",
};

function when(iso: string): string {
  const date = new Date(iso);
  return date.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function details(job: Job): string {
  return [
    when(job.created_at),
    formatBytes(job.source.size_bytes),
    job.audio ? formatDuration(job.audio.duration_seconds) : null,
    job.status === "COMPLETED" ? null : `${job.progress}%`,
  ]
    .filter(Boolean)
    .join(" · ");
}

export function JobList() {
  const { jobs, error, loading } = useJobs();

  if (loading) {
    return (
      <section className="panel">
        <h2>Задачи</h2>
        <p className="muted">Загрузка…</p>
      </section>
    );
  }

  if (error) {
    return (
      <section className="panel">
        <h2>Задачи</h2>
        <p className="error" role="alert">
          {error}
        </p>
      </section>
    );
  }

  if (jobs.length === 0) {
    return null; // nothing uploaded yet: the upload form is the whole story
  }

  return (
    <section className="panel">
      <h2>Задачи</h2>
      <ul className="job-list">
        {jobs.map((job) => (
          <li key={job.job_id}>
            <Link href={`/jobs/${job.job_id}`} className="job-list__item">
              <span className="job-list__name">{job.source.filename}</span>
              <span className={`badge badge--${TONE_BY_STATUS[job.status] ?? "warn"}`}>
                {STAGE_LABELS[job.status]}
              </span>
              <span className="muted job-list__meta">{details(job)}</span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

"use client";

import { useRef, useState, type DragEvent } from "react";

import { ApiError, createJob, formatBytes } from "@/lib/jobs";

const SPEAKER_OPTIONS = ["auto", "2", "3", "4", "5", "6"] as const;

export interface UploadLimits {
  maxUploadMb: number;
  allowedExtensions: string[];
}

interface Props {
  limits: UploadLimits | null;
  onCreated: (jobId: string) => void;
}

function extensionOf(filename: string): string {
  const index = filename.lastIndexOf(".");
  return index === -1 ? "" : filename.slice(index + 1).toLowerCase();
}

export function UploadForm({ limits, onCreated }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [speakers, setSpeakers] = useState<string>("auto");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const accept = limits?.allowedExtensions.map((ext) => `.${ext}`).join(",");

  const validate = (candidate: File): string | null => {
    if (!limits) return null;
    if (!limits.allowedExtensions.includes(extensionOf(candidate.name))) {
      return `Поддерживаются только: ${limits.allowedExtensions.join(", ")}.`;
    }
    if (candidate.size > limits.maxUploadMb * 1024 * 1024) {
      return `Файл больше ${limits.maxUploadMb} МБ.`;
    }
    return null;
  };

  const select = (candidate: File | null) => {
    if (!candidate) return;
    const problem = validate(candidate);
    setError(problem);
    setFile(problem ? null : candidate);
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    select(event.dataTransfer.files.item(0));
  };

  const submit = async () => {
    if (!file || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const jobId = await createJob({
        file,
        speakerCount: speakers === "auto" ? null : Number(speakers),
      });
      onCreated(jobId);
    } catch (cause: unknown) {
      setError(
        cause instanceof ApiError ? cause.message : "Не удалось загрузить файл. Проверьте API.",
      );
      setSubmitting(false);
    }
  };

  return (
    <section className="panel">
      <h2>Новая расшифровка</h2>

      <div
        className={`dropzone${dragging ? " dropzone--active" : ""}`}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") inputRef.current?.click();
        }}
      >
        <input
          ref={inputRef}
          type="file"
          accept={accept}
          hidden
          onChange={(event) => select(event.target.files?.item(0) ?? null)}
        />
        {file ? (
          <>
            <strong>{file.name}</strong>
            <span className="muted">{formatBytes(file.size)}</span>
          </>
        ) : (
          <>
            <strong>Перетащите запись сюда</strong>
            <span className="muted">
              или нажмите, чтобы выбрать файл
              {limits ? ` · ${limits.allowedExtensions.join(", ")} · до ${limits.maxUploadMb} МБ` : ""}
            </span>
          </>
        )}
      </div>

      <div className="form-row">
        <label htmlFor="speakers">Количество спикеров</label>
        <select
          id="speakers"
          value={speakers}
          onChange={(event) => setSpeakers(event.target.value)}
        >
          {SPEAKER_OPTIONS.map((option) => (
            <option key={option} value={option}>
              {option === "auto" ? "Определить автоматически" : option}
            </option>
          ))}
        </select>

        <button type="button" className="primary" onClick={submit} disabled={!file || submitting}>
          {submitting ? "Загрузка…" : "Расшифровать"}
        </button>
      </div>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

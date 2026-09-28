"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { SpeakerEditor } from "@/components/SpeakerEditor";
import { useTranscript } from "@/hooks/useTranscript";
import {
  audioUrl,
  downloadUrl,
  EXPORT_FORMATS,
  formatTimecode,
  speakerName,
  type TranscriptSegment,
} from "@/lib/transcript";

interface Props {
  jobId: string;
}

/** T7.1/T7.2/T7.5 — transcript viewer, player seeking and downloads. */
export function TranscriptView({ jobId }: Props) {
  const { transcript, error, loading, saving, rename } = useTranscript(jobId);
  const player = useRef<HTMLAudioElement>(null);
  const [playhead, setPlayhead] = useState(0);

  const seek = useCallback((seconds: number) => {
    const audio = player.current;
    if (!audio) return;
    audio.currentTime = seconds;
    void audio.play().catch(() => {
      // Autoplay can be blocked; the seek itself already happened.
    });
  }, []);

  useEffect(() => {
    const audio = player.current;
    if (!audio) return;
    const onTime = () => setPlayhead(audio.currentTime);
    audio.addEventListener("timeupdate", onTime);
    return () => audio.removeEventListener("timeupdate", onTime);
  }, [transcript]);

  if (loading && !transcript) {
    return <p className="muted">Загрузка транскрипта…</p>;
  }

  if (!transcript) {
    return (
      <p className="error" role="alert">
        {error ?? "Транскрипт недоступен."}
      </p>
    );
  }

  const isCurrent = (segment: TranscriptSegment) =>
    playhead >= segment.start && playhead < segment.end;

  return (
    <div className="transcript">
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      {/* T7.2 — the player the timecodes seek. */}
      <audio ref={player} src={audioUrl(jobId)} controls preload="metadata" className="player" />

      <SpeakerEditor transcript={transcript} saving={saving} onSave={rename} />

      {/* The `download` attribute is ignored cross-origin; the save is forced by the
          Content-Disposition header the API sends, which also carries the filename. */}
      <div className="downloads">
        {EXPORT_FORMATS.map((format) => (
          <a key={format} className="download" href={downloadUrl(jobId, format)} download>
            {format.toUpperCase()}
          </a>
        ))}
      </div>

      <ol className="segments">
        {transcript.segments.map((segment) => (
          <li
            key={segment.id}
            className={`segment${isCurrent(segment) ? " segment--current" : ""}`}
          >
            <button
              type="button"
              className="segment__time"
              onClick={() => seek(segment.start)}
              title="Перейти к этому месту записи"
            >
              {formatTimecode(segment.start)} – {formatTimecode(segment.end)}
            </button>
            <span className="segment__speaker">
              {speakerName(transcript, segment.speaker_id)}
            </span>
            <p className="segment__text">{segment.text}</p>
          </li>
        ))}
      </ol>

      {transcript.segments.length === 0 && <p className="muted">В записи не нашлось речи.</p>}

      <p className="muted transcript__meta">
        {`ASR: ${transcript.asr.model} · диаризация: ${transcript.diarization.model} · слов: ${transcript.words.length}`}
      </p>
    </div>
  );
}

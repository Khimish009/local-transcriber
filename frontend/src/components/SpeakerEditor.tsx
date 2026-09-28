"use client";

import { useEffect, useState } from "react";

import type { Transcript } from "@/lib/transcript";

interface Props {
  transcript: Transcript;
  saving: boolean;
  onSave: (names: Record<string, string>) => void;
}

/** T7.4 — editing names only. Timestamps and words are never touched here. */
export function SpeakerEditor({ transcript, saving, onSave }: Props) {
  const [names, setNames] = useState<Record<string, string>>({});

  useEffect(() => {
    setNames(
      Object.fromEntries(transcript.speakers.map((speaker) => [speaker.id, speaker.display_name])),
    );
  }, [transcript]);

  const changed = transcript.speakers.filter(
    (speaker) => (names[speaker.id] ?? "").trim() !== speaker.display_name,
  );
  const empty = transcript.speakers.some((speaker) => !(names[speaker.id] ?? "").trim());

  if (transcript.speakers.length === 0) {
    return null;
  }

  return (
    <form
      className="speakers"
      onSubmit={(event) => {
        event.preventDefault();
        onSave(
          Object.fromEntries(changed.map((speaker) => [speaker.id, names[speaker.id].trim()])),
        );
      }}
    >
      <div className="speakers__list">
        {transcript.speakers.map((speaker) => (
          <label key={speaker.id} className="speakers__item">
            <span className="speakers__id">{speaker.id}</span>
            <input
              type="text"
              value={names[speaker.id] ?? ""}
              maxLength={80}
              disabled={saving}
              onChange={(event) =>
                setNames((current) => ({ ...current, [speaker.id]: event.target.value }))
              }
            />
          </label>
        ))}
      </div>

      <button type="submit" className="primary" disabled={saving || empty || changed.length === 0}>
        {saving ? "Сохранение…" : "Сохранить имена"}
      </button>
    </form>
  );
}

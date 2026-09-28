"""Reading and editing the finished result (TASKS.md T7.4).

Renaming a speaker touches `result/transcript.json` and regenerates the views from it.
Diarization and ASR are never re-run (SPEC.md §4).
"""

import logging
import os
from pathlib import Path

from app.core.errors import AppError, ErrorCode
from app.schemas.transcript import Transcript
from app.services.exports import load_transcript, regenerate_exports
from app.services.storage import JobStorage

logger = logging.getLogger(__name__)

MAX_DISPLAY_NAME_LENGTH = 80


def read_transcript(storage: JobStorage, job_id: str) -> Transcript:
    path = storage.transcript_path(job_id)
    if not path.is_file():
        raise AppError(
            ErrorCode.RESULT_NOT_READY,
            "The transcript is not ready yet",
            status_code=409,
        )
    return load_transcript(path)


def result_file(storage: JobStorage, job_id: str, filename: str) -> Path:
    path = storage.result_dir(job_id) / filename
    if not path.is_file():
        raise AppError(
            ErrorCode.RESULT_NOT_READY,
            "This result file is not ready yet",
            status_code=409,
        )
    return path


def _write_atomically(path: Path, payload: str) -> None:
    """The canonical JSON must never be left half-written by a crash."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, path)


def apply_speaker_names(transcript: Transcript, names: dict[str, str]) -> list[str]:
    """Rename in place, returning the ids that are not part of this transcript."""
    known = {speaker.id: speaker for speaker in transcript.speakers}
    unknown = sorted(speaker_id for speaker_id in names if speaker_id not in known)
    for speaker_id, display_name in names.items():
        speaker = known.get(speaker_id)
        if speaker is not None:
            speaker.display_name = display_name.strip()[:MAX_DISPLAY_NAME_LENGTH]
    return unknown


def rename_speakers(
    storage: JobStorage,
    job_id: str,
    names: dict[str, str],
    font_path: Path | None = None,
) -> Transcript:
    """T7.4 — update display names and rebuild TXT/DOCX/PDF. No model ever runs here."""
    transcript = read_transcript(storage, job_id)
    unknown = apply_speaker_names(transcript, names)
    if unknown:
        raise AppError(
            ErrorCode.JOB_NOT_FOUND,
            f"Unknown speaker ids: {', '.join(unknown)}",
            status_code=404,
        )

    transcript_path = storage.transcript_path(job_id)
    _write_atomically(transcript_path, transcript.model_dump_json(indent=2))
    regenerate_exports(transcript_path, storage.result_dir(job_id), font_path)

    logger.info(
        "speakers renamed",
        extra={"job_id": job_id, "stage": "result", "speakers": len(names)},
    )
    return transcript

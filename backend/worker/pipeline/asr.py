"""GigaAM speech recognition stage (TASKS.md T4.2-T4.4).

Two independent pieces live here:

* `build_asr_chunks` — pure, deterministic geometry. It turns diarization speech regions
  into ASR chunks: small silences are merged so the model gets enough context, and chunks
  are capped at a safe length. No ML imports, fully unit-testable.
* `transcribe_chunks` — runs the model over those chunks and shifts the local word
  timestamps onto the global timeline (SPEC.md §3.3).

Chunk boundaries deliberately do NOT follow speaker turns: very short fragments degrade
recognition quality (SPEC.md §3.3).
"""

import logging
import tempfile
import wave
from collections.abc import Callable
from math import ceil
from pathlib import Path
from typing import Any

from app.core.errors import AppError, ErrorCode
from app.schemas.asr import AsrChunk, AsrResult, AsrWord
from app.schemas.diarization import SpeakerSegment
from app.services.model_cache import ASR_ENGINE

logger = logging.getLogger(__name__)

# Anything shorter carries no recognizable speech and only wastes an inference call.
MIN_CHUNK_SECONDS = 0.1

ProgressCallback = Callable[[float, float], None]


def merge_speech_intervals(segments: list[SpeakerSegment]) -> list[list[float]]:
    """Union of the speaker segments — the parts of the recording that contain speech.

    Overlapping speech (two people at once) collapses into a single interval: ASR
    transcribes audio, not speakers.
    """
    ordered = sorted((s.start, s.end) for s in segments if s.end > s.start)
    merged: list[list[float]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _split_evenly(start: float, end: float, max_seconds: float) -> list[tuple[float, float]]:
    """Cut an over-long run into equal parts, so no part is a sliver."""
    duration = end - start
    if duration < MIN_CHUNK_SECONDS:
        return []
    parts = max(1, ceil(duration / max_seconds))
    step = duration / parts
    return [
        (start + index * step, end if index == parts - 1 else start + (index + 1) * step)
        for index in range(parts)
    ]


def build_asr_chunks(
    segments: list[SpeakerSegment],
    max_chunk_seconds: float,
    merge_gap_seconds: float,
    total_duration: float | None = None,
) -> list[AsrChunk]:
    """T4.2 — speech regions in, ASR chunks out.

    `max_chunk_seconds` (MAX_ASR_CHUNK_SECONDS) and `merge_gap_seconds`
    (MERGE_SILENCE_GAP_MS) come from settings — never hardcoded in the pipeline.
    """
    if max_chunk_seconds <= 0:
        raise ValueError("max_chunk_seconds must be positive")
    merge_gap_seconds = max(0.0, merge_gap_seconds)

    runs: list[list[float]] = []
    for start, end in merge_speech_intervals(segments):
        if runs and start - runs[-1][1] <= merge_gap_seconds:
            runs[-1][1] = end
        else:
            runs.append([start, end])

    # Diarization boundaries clip word onsets, so widen each run. Half the merge gap is the
    # largest padding that can never make two neighbouring runs overlap.
    padding = merge_gap_seconds / 2

    bounds: list[tuple[float, float]] = []
    for start, end in runs:
        padded_start = max(0.0, start - padding)
        padded_end = end + padding
        if total_duration is not None:
            padded_end = min(padded_end, total_duration)
        bounds.extend(_split_evenly(padded_start, padded_end, max_chunk_seconds))

    return [
        AsrChunk(index=index, start=round(start, 3), end=round(end, 3))
        for index, (start, end) in enumerate(bounds)
    ]


def _open_normalized(audio_path: Path) -> wave.Wave_read:
    """The normalized WAV is PCM s16le/mono/16 kHz, so stdlib `wave` can slice it exactly."""
    if not audio_path.is_file():
        raise AppError(ErrorCode.ASR_FAILED, "Normalized audio is missing")
    try:
        return wave.open(str(audio_path), "rb")
    except wave.Error as exc:
        raise AppError(ErrorCode.ASR_FAILED, "Could not read the normalized audio") from exc


def _write_chunk(source: wave.Wave_read, chunk: AsrChunk, destination: Path) -> float:
    """Copy `chunk` out of the open WAV into its own file. Returns the written duration."""
    rate = source.getframerate()
    total_frames = source.getnframes()
    first = max(0, min(total_frames, int(chunk.start * rate)))
    last = max(first, min(total_frames, int(round(chunk.end * rate))))

    source.setpos(first)
    frames = source.readframes(last - first)

    with wave.open(str(destination), "wb") as out:
        out.setnchannels(source.getnchannels())
        out.setsampwidth(source.getsampwidth())
        out.setframerate(rate)
        out.writeframes(frames)
    return (last - first) / rate


def _extract_words(result: Any, offset: float) -> list[AsrWord]:
    """Shift the model's chunk-local word timestamps onto the global timeline."""
    words = getattr(result, "words", None)
    if words is None:
        if not (getattr(result, "text", "") or "").strip():
            return []  # silence — nothing was recognized in this chunk
        raise AppError(
            ErrorCode.ASR_FAILED,
            "The ASR model returned text without word-level timestamps",
        )

    shifted: list[AsrWord] = []
    for word in words:
        text = (getattr(word, "text", "") or "").strip()
        if not text:
            continue
        start = float(getattr(word, "start", 0.0)) + offset
        end = float(getattr(word, "end", 0.0)) + offset
        shifted.append(AsrWord(start=round(start, 3), end=round(max(start, end), 3), text=text))
    return shifted


def transcribe_chunks(
    audio_path: Path,
    chunks: list[AsrChunk],
    model: Any,
    model_name: str,
    on_progress: ProgressCallback | None = None,
) -> AsrResult:
    """T4.3/T4.4 — recognize every chunk and report progress by processed duration."""
    total = sum(chunk.duration for chunk in chunks)
    processed = 0.0
    words: list[AsrWord] = []

    with _open_normalized(audio_path) as source, tempfile.TemporaryDirectory() as tmp_dir:
        chunk_path = Path(tmp_dir) / "chunk.wav"
        for chunk in chunks:
            # A chunk can end up empty when diarization points past the end of the file.
            if _write_chunk(source, chunk, chunk_path) >= MIN_CHUNK_SECONDS:
                try:
                    result = model.transcribe(str(chunk_path), word_timestamps=True)
                except AppError:
                    raise
                except Exception as exc:
                    logger.exception(
                        "ASR failed on a chunk",
                        extra={"stage": "asr", "chunk": chunk.index, "start": chunk.start},
                    )
                    raise AppError(ErrorCode.ASR_FAILED, "Could not recognize the speech") from exc
                words.extend(_extract_words(result, chunk.start))

            processed += chunk.duration
            if on_progress is not None:
                on_progress(processed, total)

    words.sort(key=lambda word: (word.start, word.end))
    logger.info(
        "ASR finished",
        extra={
            "stage": "asr",
            "model": model_name,
            "chunks": len(chunks),
            "words": len(words),
            "speech_seconds": round(total, 3),
        },
    )
    return AsrResult(engine=ASR_ENGINE, model=model_name, chunks=chunks, words=words)

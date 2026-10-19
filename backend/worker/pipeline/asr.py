"""GigaAM speech recognition stage (TASKS.md T4.2-T4.4).

Two independent pieces live here:

* `build_asr_chunks` — pure, deterministic geometry. It turns diarization speech regions
  into ASR chunks: small silences are merged so the model gets enough context, and chunks
  are capped at a safe length. No ML imports, fully unit-testable.
* `transcribe_chunks` — runs a recognizer over those chunks in batches and shifts the local
  word timestamps onto the global timeline (SPEC.md §3.3). `GigaAmBatchRecognizer` is the
  real recognizer; tests pass a fake one, since torch only exists in the worker image.

Chunk boundaries deliberately do NOT follow speaker turns: very short fragments degrade
recognition quality (SPEC.md §3.3).
"""

import logging
import wave
from collections.abc import Callable
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import Any, Protocol

from app.core.errors import AppError, ErrorCode
from app.schemas.asr import AsrChunk, AsrResult, AsrWord
from app.schemas.diarization import SpeakerSegment
from app.services.model_cache import ASR_ENGINE

logger = logging.getLogger(__name__)

# Anything shorter carries no recognizable speech and only wastes an inference call.
MIN_CHUNK_SECONDS = 0.1

ProgressCallback = Callable[[float, float], None]


@dataclass(frozen=True)
class Recognition:
    """One chunk's text and chunk-local word timestamps (None when the model gave none)."""

    text: str
    words: list[Any] | None


class Recognizer(Protocol):
    """Recognizes a batch of PCM s16le mono chunks; one result per chunk, in order.

    A result exposes `text` and `words` (each with `text`, `start`, `end` in seconds from
    the start of its chunk).
    """

    def __call__(self, pcm_batch: list[bytes], sample_rate: int) -> list[Any]: ...


class GigaAmBatchRecognizer:
    """Batched GigaAM inference (T12.2): one padded forward pass per batch of chunks.

    `GigaAMASR.transcribe` handles one file per call and spawns ffmpeg to decode it. This
    runs the same `forward` + `_decode` that GigaAM's own `transcribe_longform` batches
    with. On CPU it is 2.2x faster at batch 8, and text and word timestamps match
    `transcribe` exactly (`python -m benchmark.equivalence`).

    Exactness rests on two properties of the pinned commit, so re-run the check after
    bumping gigaam: the v3 preprocessor uses `center=False`, so no mel frame reaches past
    a chunk's own samples into the zero padding, and the encoder masks padded frames by
    length (`_mask_time`, attention and conv masks). `_decode` is private, which is only
    acceptable because gigaam is pinned to a commit (requirements-worker.txt).
    """

    def __init__(self, model: Any) -> None:
        self.model = model

    def __call__(self, pcm_batch: list[bytes], sample_rate: int) -> list[Recognition]:
        import torch
        from gigaam.model import LONGFORM_THRESHOLD
        from gigaam.preprocess import SAMPLE_RATE

        if sample_rate != SAMPLE_RATE:
            raise ValueError(f"GigaAM expects {SAMPLE_RATE} Hz audio, got {sample_rate} Hz")

        # The same int16 -> float conversion as gigaam.preprocess.load_audio.
        waves = [
            torch.frombuffer(bytearray(pcm), dtype=torch.int16).float() / 32768.0
            for pcm in pcm_batch
        ]
        longest = max(wave_.shape[0] for wave_ in waves)
        if longest > LONGFORM_THRESHOLD:
            # transcribe() refuses such input as well; MAX_ASR_CHUNK_SECONDS is too high.
            raise ValueError(f"chunk of {longest / sample_rate:.1f} s is too long for GigaAM")

        device, dtype = self.model._device, self.model._dtype
        batch = torch.zeros(len(waves), longest, dtype=dtype, device=device)
        for row, wave_ in enumerate(waves):
            batch[row, : wave_.shape[0]] = wave_
        lengths = torch.tensor([wave_.shape[0] for wave_ in waves], device=device)

        with torch.inference_mode():
            encoded, encoded_len = self.model.forward(batch, lengths)
            decoded = self.model._decode(encoded, encoded_len, lengths, word_timestamps=True)
        return [Recognition(text=text, words=words) for text, words in decoded]


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


def _read_chunk(source: wave.Wave_read, chunk: AsrChunk) -> tuple[bytes, float]:
    """The PCM frames of `chunk` from the open WAV, and their duration in seconds."""
    rate = source.getframerate()
    total_frames = source.getnframes()
    first = max(0, min(total_frames, int(chunk.start * rate)))
    last = max(first, min(total_frames, int(round(chunk.end * rate))))

    source.setpos(first)
    return source.readframes(last - first), (last - first) / rate


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
    recognizer: Recognizer,
    model_name: str,
    batch_size: int = 1,
    on_progress: ProgressCallback | None = None,
) -> AsrResult:
    """T4.3/T4.4/T12.2 — recognize the chunks `batch_size` at a time.

    Progress is reported after every batch, by processed duration.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be positive")

    total = sum(chunk.duration for chunk in chunks)
    processed = 0.0
    words: list[AsrWord] = []

    with _open_normalized(audio_path) as source:
        rate = source.getframerate()
        for first in range(0, len(chunks), batch_size):
            batch = chunks[first : first + batch_size]
            pieces = [(chunk, *_read_chunk(source, chunk)) for chunk in batch]
            # A chunk can end up empty when diarization points past the end of the file.
            audible = [
                (chunk, pcm) for chunk, pcm, seconds in pieces if seconds >= MIN_CHUNK_SECONDS
            ]
            if audible:
                try:
                    results = recognizer([pcm for _, pcm in audible], rate)
                    if len(results) != len(audible):
                        raise ValueError(f"{len(results)} results for {len(audible)} chunks")
                except AppError:
                    raise
                except Exception as exc:
                    logger.exception(
                        "ASR failed on a batch",
                        extra={
                            "stage": "asr",
                            "first_chunk": audible[0][0].index,
                            "last_chunk": audible[-1][0].index,
                            "start": audible[0][0].start,
                        },
                    )
                    raise AppError(ErrorCode.ASR_FAILED, "Could not recognize the speech") from exc
                for (chunk, _), result in zip(audible, results, strict=True):
                    words.extend(_extract_words(result, chunk.start))

            processed += sum(chunk.duration for chunk in batch)
            if on_progress is not None:
                on_progress(processed, total)

    words.sort(key=lambda word: (word.start, word.end))
    logger.info(
        "ASR finished",
        extra={
            "stage": "asr",
            "model": model_name,
            "chunks": len(chunks),
            "batch_size": batch_size,
            "words": len(words),
            "speech_seconds": round(total, 3),
        },
    )
    return AsrResult(engine=ASR_ENGINE, model=model_name, chunks=chunks, words=words)

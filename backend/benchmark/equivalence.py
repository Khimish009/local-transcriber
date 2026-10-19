"""Check that the Phase 12 speedups leave the result unchanged (TASKS.md T12.1, T12.2).

Usage, from the repository root, on a run directory prepared by `benchmark.run`:

    docker compose run --rm --no-deps worker python -m benchmark.equivalence \\
        --run /data/benchmark/runs/<запись>

Every optimization is compared with the code it replaced, on the same audio:

* diarization — stock pyannote against the worker's pipeline with shared-trunk embeddings,
  on an excerpt from the middle of the recording (`--seconds 0` takes all of it). The
  speakers and segments must match exactly. The embeddings are also compared directly,
  on a two-minute excerpt.
* ASR — one `transcribe()` call per chunk through a temporary WAV, as before T12.2,
  against batched recognition, on chunks built from the run's `diarization.json`. Text and
  word timestamps must match exactly.

Embeddings themselves are not bit-identical, and cannot be: the fbank FFT rounds slightly
differently depending on what else is in the batch. Stock pyannote shows the same effect
on its own — changing `embedding_batch_size` from 32 to 33 moves embeddings by ~2e-7. The
check reports the largest difference and fails above `EMBEDDING_TOLERANCE`.

The exit code is non-zero if anything differs. Run it after bumping pyannote.audio or
GigaAM: both speedups rely on internals of the pinned versions.
"""

import argparse
import sys
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.schemas.diarization import DiarizationResult
from worker.pipeline.asr import build_asr_chunks, transcribe_chunks
from worker.pipeline.diarization import run_diarization
from worker.pipeline.embeddings import shared_trunk_embeddings
from worker.pipeline.models import (
    get_asr_model,
    get_asr_recognizer,
    get_diarization_pipeline,
    load_diarization_pipeline,
)

# Two orders of magnitude above the rounding noise measured on stock pyannote (~2e-7).
EMBEDDING_TOLERANCE = 1e-5
# Stock embeddings run at ~0.4 s per second of audio on CPU, so this check stays short even
# when the diarization check covers the whole recording.
EMBEDDING_EXCERPT_SECONDS = 120.0


def write_excerpt(normalized: Path, seconds: float, destination: Path) -> Path:
    """`seconds` of audio from the middle of the recording; 0 or more than it has means all."""
    with wave.open(str(normalized), "rb") as source:
        total = source.getnframes()
        length = total if seconds <= 0 else min(total, int(seconds * source.getframerate()))
        source.setpos((total - length) // 2)
        frames = source.readframes(length)
        with wave.open(str(destination), "wb") as out:
            out.setparams(source.getparams())
            out.writeframes(frames)
    return destination


def as_pyannote_file(path: Path) -> dict[str, Any]:
    """In-memory file for calling pipeline steps directly, bypassing `Pipeline.__call__`."""
    import torch

    with wave.open(str(path), "rb") as source:
        rate = source.getframerate()
        frames = source.readframes(source.getnframes())
    waveform = torch.frombuffer(bytearray(frames), dtype=torch.int16).float().div(32768)
    return {"waveform": waveform[None], "sample_rate": rate, "uri": path.stem}


def check_embeddings(excerpt: Path, stock: Any, optimized: Any) -> bool:
    import numpy as np

    if not optimized._segmentation.model.specifications.powerset:
        raise SystemExit("expected a powerset segmentation model, as in community-1")

    file = as_pyannote_file(excerpt)
    # Powerset segmentation is already binary — the pipeline uses it as is.
    segmentations = optimized.get_segmentations(file)
    exclude_overlap = optimized.embedding_exclude_overlap

    started = time.monotonic()
    expected = stock.get_embeddings(file, segmentations, exclude_overlap=exclude_overlap)
    stock_seconds = time.monotonic() - started

    started = time.monotonic()
    actual = optimized.get_embeddings(file, segmentations, exclude_overlap=exclude_overlap)
    optimized_seconds = time.monotonic() - started

    if expected.shape != actual.shape:
        print(f"embeddings: shape {actual.shape} instead of {expected.shape} — DIFFERENT")
        return False
    max_diff = float(np.nanmax(np.abs(expected - actual)))
    same_nan = bool(np.array_equal(np.isnan(expected), np.isnan(actual)))
    ok = same_nan and max_diff <= EMBEDDING_TOLERANCE
    print(
        f"embeddings: {segmentations.data.shape[0]} windows, "
        f"stock {stock_seconds:.1f} s, shared trunk {optimized_seconds:.1f} s "
        f"({stock_seconds / optimized_seconds:.2f}x), max |diff| {max_diff:.1e} — "
        f"{'ok' if ok else 'DIFFERENT'}"
    )
    return ok


def check_diarization(excerpt: Path, stock: Any, optimized: Any) -> bool:
    started = time.monotonic()
    expected = run_diarization(excerpt, stock)
    stock_seconds = time.monotonic() - started

    started = time.monotonic()
    actual = run_diarization(excerpt, optimized)
    optimized_seconds = time.monotonic() - started

    identical = expected == actual
    print(
        f"diarization: {len(expected.speakers)} speakers, {len(expected.segments)} segments, "
        f"stock {stock_seconds:.1f} s, optimized {optimized_seconds:.1f} s "
        f"({stock_seconds / optimized_seconds:.2f}x) — "
        f"{'identical' if identical else 'DIFFERENT'}"
    )
    return identical


class TranscribeRecognizer:
    """The pre-T12.2 path: one `transcribe()` per chunk, through a temporary WAV file."""

    def __init__(self, model: Any, tmp_dir: Path) -> None:
        self.model = model
        self.path = tmp_dir / "chunk.wav"

    def __call__(self, pcm_batch: list[bytes], sample_rate: int) -> list[Any]:
        results = []
        for pcm in pcm_batch:
            with wave.open(str(self.path), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(sample_rate)
                out.writeframes(pcm)
            results.append(self.model.transcribe(str(self.path), word_timestamps=True))
        return results


def check_asr(run: Path, settings: Settings, limit: int, tmp_dir: Path) -> bool:
    path = run / "diarization.json"
    if not path.is_file():
        raise SystemExit(f"{path} is missing — run benchmark.run first")
    diarization = DiarizationResult.model_validate_json(path.read_text(encoding="utf-8"))
    normalized = run / "normalized.wav"
    with wave.open(str(normalized), "rb") as source:
        duration = source.getnframes() / source.getframerate()
    # The same chunk geometry as the worker's ASR stage.
    chunks = build_asr_chunks(
        diarization.exclusive or diarization.segments,
        max_chunk_seconds=settings.max_asr_chunk_seconds,
        merge_gap_seconds=settings.merge_silence_gap_ms / 1000,
        total_duration=duration,
    )
    if limit > 0:
        chunks = chunks[:limit]

    started = time.monotonic()
    expected = transcribe_chunks(
        normalized, chunks, TranscribeRecognizer(get_asr_model(settings), tmp_dir), "reference"
    )
    reference_seconds = time.monotonic() - started

    started = time.monotonic()
    actual = transcribe_chunks(
        normalized,
        chunks,
        get_asr_recognizer(settings),
        "reference",
        batch_size=settings.asr_batch_size,
    )
    batched_seconds = time.monotonic() - started

    identical = expected.words == actual.words
    print(
        f"asr: {len(chunks)} chunks, {len(expected.words)} words, "
        f"one by one {reference_seconds:.1f} s, batch {settings.asr_batch_size} "
        f"{batched_seconds:.1f} s ({reference_seconds / batched_seconds:.2f}x) — "
        f"{'identical' if identical else 'DIFFERENT'}"
    )
    return identical


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="a benchmark.run output folder")
    parser.add_argument(
        "--seconds",
        type=float,
        default=120.0,
        help="excerpt length for the diarization checks; 0 means the whole recording",
    )
    parser.add_argument(
        "--chunks", type=int, default=0, help="ASR chunks to compare; 0 means all of them"
    )
    parser.add_argument(
        "--only", choices=("diarization", "asr"), default=None, help="run one group of checks"
    )
    args = parser.parse_args()

    configure_logging()
    settings = get_settings()
    normalized = args.run / "normalized.wav"
    if not normalized.is_file():
        raise SystemExit(f"{normalized} is missing — run benchmark.run first")

    results: list[bool] = []
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        if args.only in (None, "diarization"):
            stock = load_diarization_pipeline(settings, settings.hf_token)
            optimized = get_diarization_pipeline(settings, settings.hf_token)
            if getattr(optimized.get_embeddings, "__func__", None) is not shared_trunk_embeddings:
                # Otherwise stock would be compared with stock and the check would pass empty.
                raise SystemExit("the worker pipeline fell back to stock embeddings — see logs")
            short = write_excerpt(normalized, EMBEDDING_EXCERPT_SECONDS, tmp / "embeddings.wav")
            excerpt = write_excerpt(normalized, args.seconds, tmp / "diarization.wav")
            results.append(check_embeddings(short, stock, optimized))
            results.append(check_diarization(excerpt, stock, optimized))
        if args.only in (None, "asr"):
            results.append(check_asr(args.run, settings, args.chunks, tmp))
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()

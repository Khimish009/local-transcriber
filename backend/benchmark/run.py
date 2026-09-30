"""Run the pipeline over benchmark samples for several ASR models (T10.1, T10.2).

Usage, from the repository root:

    docker compose run --rm --no-deps worker python -m benchmark.run

Every recording is normalized and diarized exactly once; the cached artifacts are then
reused by every model, so the only variable between runs is ASR_MODEL. That matters both
for honesty (the comparison must not be polluted by diarization jitter) and for cost —
diarization is by far the slowest stage.

Everything is idempotent: an artifact that already exists is not recomputed, so adding a
third model to an existing run costs only that model's inference.
"""

import argparse
import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.schemas.diarization import DiarizationResult
from worker.pipeline.alignment import build_transcript
from worker.pipeline.asr import build_asr_chunks, transcribe_chunks
from worker.pipeline.audio import normalize_audio, probe_audio
from worker.pipeline.diarization import run_diarization
from worker.pipeline.models import get_asr_model, get_diarization_pipeline

logger = logging.getLogger(__name__)

DEFAULT_SAMPLES = Path("/data/benchmark/samples")
DEFAULT_OUTPUT = Path("/data/benchmark/runs")
DEFAULT_MODELS = ("v3_e2e_rnnt", "v3_rnnt")

AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".mp4", ".webm", ".ogg"}


@dataclass
class Timing:
    """Seconds spent per stage, plus the real-time factor against the recording length."""

    audio_seconds: float
    diarization_seconds: float | None
    model_load_seconds: dict[str, float]
    asr_seconds: dict[str, float]

    def rtf(self, seconds: float) -> float:
        return seconds / self.audio_seconds if self.audio_seconds else 0.0


def find_samples(samples_dir: Path) -> list[Path]:
    if not samples_dir.is_dir():
        raise SystemExit(f"No samples directory at {samples_dir}")
    found = sorted(p for p in samples_dir.iterdir() if p.suffix.lower() in AUDIO_SUFFIXES)
    if not found:
        raise SystemExit(f"No audio files in {samples_dir} (looked for {sorted(AUDIO_SUFFIXES)})")
    return found


def prepare(sample: Path, out_dir: Path, settings: Settings) -> tuple[Path, float]:
    """Normalize the recording once; return the WAV path and its duration."""
    normalized = out_dir / "normalized.wav"
    metadata = probe_audio(sample, settings.ffprobe_bin)
    if not normalized.is_file():
        normalize_audio(sample, normalized, settings.ffmpeg_bin)
    return normalized, metadata.duration_seconds


def diarize_once(
    normalized: Path, out_dir: Path, settings: Settings, speaker_count: int | None
) -> tuple[DiarizationResult, float | None]:
    """Diarize unless a cached result is already there. Shared by every model."""
    path = out_dir / "diarization.json"
    if path.is_file():
        logger.info("reusing cached diarization", extra={"stage": "benchmark"})
        return DiarizationResult.model_validate_json(path.read_text(encoding="utf-8")), None

    pipeline = get_diarization_pipeline(settings, settings.hf_token)
    started = time.monotonic()
    result = run_diarization(normalized, pipeline, speaker_count=speaker_count)
    elapsed = time.monotonic() - started
    path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return result, elapsed


def transcribe_with(
    model_name: str,
    normalized: Path,
    diarization: DiarizationResult,
    duration: float,
    filename: str,
    out_dir: Path,
    settings: Settings,
) -> tuple[float, float] | None:
    """Recognize and align with one model. Returns (load, asr) seconds, or None if cached."""
    target = out_dir / f"transcript.{model_name}.json"
    if target.is_file():
        logger.info("reusing cached transcript", extra={"stage": "benchmark", "model": model_name})
        return None

    model_settings = settings.model_copy(update={"asr_model": model_name})
    chunks = build_asr_chunks(
        diarization.exclusive or diarization.segments,
        max_chunk_seconds=model_settings.max_asr_chunk_seconds,
        merge_gap_seconds=model_settings.merge_silence_gap_ms / 1000,
        total_duration=duration,
    )
    if not chunks:
        raise SystemExit(f"{filename}: diarization found no speech to transcribe")

    started = time.monotonic()
    model = get_asr_model(model_settings)
    load_seconds = time.monotonic() - started

    started = time.monotonic()
    asr = transcribe_chunks(normalized, chunks, model, model_name)
    asr_seconds = time.monotonic() - started

    transcript = build_transcript(
        job_id=out_dir.name,
        filename=filename,
        duration_seconds=duration,
        asr=asr,
        diarization=diarization,
        tolerance_seconds=model_settings.speaker_match_tolerance_ms / 1000,
        max_block_seconds=model_settings.max_block_seconds,
        block_gap_seconds=model_settings.block_silence_gap_ms / 1000,
    )
    target.write_text(transcript.model_dump_json(indent=2), encoding="utf-8")
    return load_seconds, asr_seconds


def run_sample(
    sample: Path, out_root: Path, models: list[str], settings: Settings, speaker_count: int | None
) -> Timing:
    out_dir = out_root / sample.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    normalized, duration = prepare(sample, out_dir, settings)
    logger.info(
        "sample prepared",
        extra={"stage": "benchmark", "sample": sample.name, "duration": round(duration, 1)},
    )

    diarization, diarization_seconds = diarize_once(normalized, out_dir, settings, speaker_count)
    timing = Timing(duration, diarization_seconds, {}, {})

    for model_name in models:
        measured = transcribe_with(
            model_name, normalized, diarization, duration, sample.name, out_dir, settings
        )
        if measured is None:
            continue
        timing.model_load_seconds[model_name], timing.asr_seconds[model_name] = measured
        logger.info(
            "model done",
            extra={
                "stage": "benchmark",
                "sample": sample.name,
                "model": model_name,
                "asr_seconds": round(timing.asr_seconds[model_name], 1),
                "rtf": round(timing.rtf(timing.asr_seconds[model_name]), 3),
            },
        )

    (out_dir / "timing.json").write_text(
        json.dumps(asdict(timing), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return timing


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, default=DEFAULT_SAMPLES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--models",
        default=",".join(DEFAULT_MODELS),
        help="comma-separated ASR_MODEL values to compare",
    )
    parser.add_argument(
        "--speakers",
        type=int,
        default=None,
        help="exact speaker count; omitted means auto-detect, as in the app",
    )
    args = parser.parse_args()

    configure_logging()
    settings = get_settings()
    models = [name.strip() for name in args.models.split(",") if name.strip()]
    samples = find_samples(args.samples)

    logger.info(
        "benchmark starting",
        extra={"stage": "benchmark", "samples": len(samples), "models": models},
    )

    total_audio = 0.0
    for sample in samples:
        timing = run_sample(sample, args.out, models, settings, args.speakers)
        total_audio += timing.audio_seconds

    logger.info(
        "benchmark finished",
        extra={
            "stage": "benchmark",
            "samples": len(samples),
            "audio_minutes": round(total_audio / 60, 1),
            "out": str(args.out),
        },
    )


if __name__ == "__main__":
    main()

"""FFmpeg-backed audio preparation (TASKS.md T2.1, T2.2).

The original upload is never modified. Everything downstream works on a single
normalized representation: WAV / mono / 16 kHz / PCM s16le (SPEC.md §2).
"""

import json
import logging
import subprocess
from pathlib import Path

from app.core.errors import AppError, ErrorCode
from app.schemas.audio import AudioMetadata

logger = logging.getLogger(__name__)

TARGET_SAMPLE_RATE = 16_000
TARGET_CHANNELS = 1
TARGET_CODEC = "pcm_s16le"

PROBE_TIMEOUT_SECONDS = 60
# Long meetings on a slow CPU still decode much faster than real time, but leave headroom.
NORMALIZE_TIMEOUT_SECONDS = 3 * 60 * 60


def _run(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603 - fixed argv, no shell
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )
    except FileNotFoundError as exc:
        raise AppError(
            ErrorCode.FFMPEG_FAILED,
            "FFmpeg is not available in this environment",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        logger.error("ffmpeg timed out", extra={"stage": "audio", "command": command[0]})
        raise AppError(ErrorCode.FFMPEG_FAILED, "Audio processing timed out") from exc
    except subprocess.CalledProcessError as exc:
        # Technical details stay in the logs; the user gets a stable error code.
        logger.error(
            "ffmpeg failed",
            extra={
                "stage": "audio",
                "command": command[0],
                "returncode": exc.returncode,
                "stderr": (exc.stderr or "")[-2000:],
            },
        )
        raise AppError(ErrorCode.FFMPEG_FAILED, "Could not read or convert the audio file") from exc


def parse_probe_output(payload: str) -> AudioMetadata:
    """Turn `ffprobe -show_format -show_streams` JSON into metadata.

    Duration is read from the audio stream when present and from the container otherwise,
    because some formats only carry it in one of the two places.
    """
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise AppError(ErrorCode.FFMPEG_FAILED, "Could not read the audio file") from exc

    container = data.get("format") or {}
    streams = [s for s in data.get("streams") or [] if s.get("codec_type") == "audio"]
    if not streams:
        raise AppError(ErrorCode.UNSUPPORTED_FORMAT, "The file contains no audio stream")
    stream = streams[0]

    duration = _to_float(stream.get("duration")) or _to_float(container.get("duration"))
    if duration is None or duration <= 0:
        raise AppError(ErrorCode.FFMPEG_FAILED, "Could not determine the recording duration")

    return AudioMetadata(
        duration_seconds=round(duration, 3),
        format_name=container.get("format_name"),
        codec_name=stream.get("codec_name"),
        sample_rate=_to_int(stream.get("sample_rate")),
        channels=_to_int(stream.get("channels")),
    )


def _to_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _to_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def probe_audio(source: Path, ffprobe_bin: str = "ffprobe") -> AudioMetadata:
    if not source.is_file():
        raise AppError(ErrorCode.FFMPEG_FAILED, "Source file is missing")

    result = _run(
        [
            ffprobe_bin,
            "-hide_banner",
            "-loglevel",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(source),
        ],
        timeout=PROBE_TIMEOUT_SECONDS,
    )
    return parse_probe_output(result.stdout)


def normalize_audio(source: Path, destination: Path, ffmpeg_bin: str = "ffmpeg") -> Path:
    """Convert any supported input into the canonical WAV used by the rest of the pipeline."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        _run(
            [
                ffmpeg_bin,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-i",
                str(source),
                "-vn",
                "-map",
                "0:a:0",
                "-ac",
                str(TARGET_CHANNELS),
                "-ar",
                str(TARGET_SAMPLE_RATE),
                "-acodec",
                TARGET_CODEC,
                str(destination),
            ],
            timeout=NORMALIZE_TIMEOUT_SECONDS,
        )
    except BaseException:
        destination.unlink(missing_ok=True)
        raise

    if not destination.is_file() or destination.stat().st_size == 0:
        destination.unlink(missing_ok=True)
        raise AppError(ErrorCode.FFMPEG_FAILED, "Audio conversion produced an empty file")

    logger.info(
        "audio normalized",
        extra={
            "stage": "audio",
            "sample_rate": TARGET_SAMPLE_RATE,
            "channels": TARGET_CHANNELS,
            "size_bytes": destination.stat().st_size,
        },
    )
    return destination

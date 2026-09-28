import json
import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path

import pytest

from app.core.errors import AppError, ErrorCode
from worker.pipeline.audio import (
    TARGET_CHANNELS,
    TARGET_SAMPLE_RATE,
    normalize_audio,
    parse_probe_output,
    probe_audio,
)

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
requires_ffmpeg = pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not installed")

PROBE_JSON = json.dumps(
    {
        "streams": [
            {"codec_type": "video", "codec_name": "png"},
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "44100",
                "channels": 2,
                "duration": "3522.412000",
            },
        ],
        "format": {"format_name": "mov,mp4,m4a", "duration": "3522.500000"},
    }
)


def test_parse_probe_output_reads_stream_metadata() -> None:
    metadata = parse_probe_output(PROBE_JSON)

    assert metadata.duration_seconds == 3522.412
    assert metadata.sample_rate == 44100
    assert metadata.channels == 2
    assert metadata.codec_name == "aac"
    assert metadata.format_name == "mov,mp4,m4a"


def test_parse_probe_output_falls_back_to_container_duration() -> None:
    payload = json.dumps(
        {
            "streams": [{"codec_type": "audio", "codec_name": "mp3", "channels": 1}],
            "format": {"format_name": "mp3", "duration": "12.5"},
        }
    )

    assert parse_probe_output(payload).duration_seconds == 12.5


def test_parse_probe_output_rejects_file_without_audio() -> None:
    payload = json.dumps({"streams": [{"codec_type": "video"}], "format": {"duration": "10"}})

    with pytest.raises(AppError) as exc:
        parse_probe_output(payload)

    assert exc.value.code is ErrorCode.UNSUPPORTED_FORMAT


@pytest.mark.parametrize("payload", ["not json", json.dumps({"streams": []})])
def test_parse_probe_output_rejects_broken_payloads(payload) -> None:
    with pytest.raises(AppError):
        parse_probe_output(payload)


def test_parse_probe_output_requires_a_duration() -> None:
    payload = json.dumps({"streams": [{"codec_type": "audio"}], "format": {}})

    with pytest.raises(AppError) as exc:
        parse_probe_output(payload)

    assert exc.value.code is ErrorCode.FFMPEG_FAILED


def test_probe_missing_file_fails_with_ffmpeg_code(tmp_path) -> None:
    with pytest.raises(AppError) as exc:
        probe_audio(tmp_path / "nope.wav")

    assert exc.value.code is ErrorCode.FFMPEG_FAILED


# --- integration: real ffmpeg -------------------------------------------------


def write_wav(path: Path, seconds: float = 1.5, rate: int = 44100, channels: int = 2) -> Path:
    frames = bytearray()
    for index in range(int(rate * seconds)):
        sample = int(8000 * math.sin(2 * math.pi * 440 * index / rate))
        frames += struct.pack("<h", sample) * channels
    with wave.open(str(path), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(bytes(frames))
    return path


def convert(source: Path, destination: Path) -> Path:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), str(destination)],
        check=True,
        capture_output=True,
    )
    return destination


@pytest.fixture
def stereo_wav(tmp_path) -> Path:
    return write_wav(tmp_path / "source.wav")


@requires_ffmpeg
@pytest.mark.parametrize("extension", ["wav", "mp3", "m4a", "ogg"])
def test_normalizes_supported_formats_to_canonical_wav(stereo_wav, tmp_path, extension) -> None:
    source = (
        stereo_wav if extension == "wav" else convert(stereo_wav, tmp_path / f"source.{extension}")
    )
    destination = tmp_path / "work" / "normalized.wav"
    source_before = source.read_bytes()

    normalize_audio(source, destination)

    with wave.open(str(destination), "rb") as produced:
        assert produced.getnchannels() == TARGET_CHANNELS
        assert produced.getframerate() == TARGET_SAMPLE_RATE
        assert produced.getsampwidth() == 2  # PCM s16le
        assert produced.getnframes() > 0
    assert source.read_bytes() == source_before, "source must not be modified"


@requires_ffmpeg
def test_probe_reports_source_properties(stereo_wav) -> None:
    metadata = probe_audio(stereo_wav)

    assert metadata.sample_rate == 44100
    assert metadata.channels == 2
    assert 1.4 < metadata.duration_seconds < 1.6


@requires_ffmpeg
def test_probe_duration_survives_transcoding(stereo_wav, tmp_path) -> None:
    mp3 = convert(stereo_wav, tmp_path / "source.mp3")

    assert abs(probe_audio(mp3).duration_seconds - probe_audio(stereo_wav).duration_seconds) < 0.2


@requires_ffmpeg
def test_normalize_rejects_a_corrupted_file(tmp_path) -> None:
    broken = tmp_path / "broken.wav"
    broken.write_bytes(b"RIFF" + b"\x00" * 64)
    destination = tmp_path / "normalized.wav"

    with pytest.raises(AppError) as exc:
        normalize_audio(broken, destination)

    assert exc.value.code is ErrorCode.FFMPEG_FAILED
    assert not destination.exists(), "no partial output must be left behind"


@requires_ffmpeg
def test_normalize_uses_a_missing_binary_gracefully(stereo_wav, tmp_path) -> None:
    with pytest.raises(AppError) as exc:
        normalize_audio(stereo_wav, tmp_path / "out.wav", ffmpeg_bin="ffmpeg-does-not-exist")

    assert exc.value.code is ErrorCode.FFMPEG_FAILED

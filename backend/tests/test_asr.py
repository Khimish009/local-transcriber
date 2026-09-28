"""T4.3/T4.4 — recognition over real WAV slices with a fake model (torch is worker-only)."""

import math
import struct
import wave
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.core.errors import AppError, ErrorCode
from app.schemas.asr import AsrChunk
from worker.pipeline.asr import transcribe_chunks

SAMPLE_RATE = 16_000


@dataclass
class FakeWord:
    text: str
    start: float
    end: float


@dataclass
class FakeTranscription:
    text: str
    words: list[FakeWord] | None


class FakeAsrModel:
    """Mimics `gigaam.GigaAMASR.transcribe`. Reports one word per chunk second."""

    def __init__(self, results=None, raises: Exception | None = None) -> None:
        self.results = results
        self.raises = raises
        self.calls: list[tuple[str, float]] = []

    def transcribe(self, wav_file: str, word_timestamps: bool = False):
        assert word_timestamps is True, "word timestamps are mandatory (SPEC.md §3.1)"
        with wave.open(wav_file, "rb") as handle:
            duration = handle.getnframes() / handle.getframerate()
        self.calls.append((wav_file, round(duration, 3)))

        if self.raises is not None:
            raise self.raises
        if self.results is not None:
            return self.results[len(self.calls) - 1]

        count = max(1, int(duration))
        words = [FakeWord(text=f"w{i}", start=float(i), end=i + 0.5) for i in range(count)]
        return FakeTranscription(text=" ".join(w.text for w in words), words=words)


def write_wav(path: Path, seconds: float) -> Path:
    """A 440 Hz tone in the canonical normalized format (mono / 16 kHz / PCM s16le)."""
    frames = int(seconds * SAMPLE_RATE)
    payload = b"".join(
        struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / SAMPLE_RATE)))
        for i in range(frames)
    )
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(payload)
    return path


@pytest.fixture
def audio(tmp_path) -> Path:
    return write_wav(tmp_path / "normalized.wav", 30.0)


def chunk(index: int, start: float, end: float) -> AsrChunk:
    return AsrChunk(index=index, start=start, end=end)


def test_words_land_on_the_global_timeline(audio) -> None:
    model = FakeAsrModel()
    chunks = [chunk(0, 5.0, 8.0), chunk(1, 20.0, 23.0)]

    result = transcribe_chunks(audio, chunks, model, "v3_e2e_rnnt")

    assert [round(word.start, 3) for word in result.words] == [5.0, 6.0, 7.0, 20.0, 21.0, 22.0]
    assert result.words[0].end == pytest.approx(5.5)
    assert result.model == "v3_e2e_rnnt"
    assert result.engine == "gigaam"


def test_only_the_requested_slice_is_sent_to_the_model(audio) -> None:
    model = FakeAsrModel()

    transcribe_chunks(audio, [chunk(0, 4.0, 11.5)], model, "v3_e2e_rnnt")

    assert model.calls[0][1] == pytest.approx(7.5)


def test_progress_follows_processed_duration(audio) -> None:
    seen: list[tuple[float, float]] = []
    chunks = [chunk(0, 0.0, 10.0), chunk(1, 10.0, 20.0), chunk(2, 20.0, 30.0)]

    transcribe_chunks(
        audio, chunks, FakeAsrModel(), "v3_e2e_rnnt", on_progress=lambda p, t: seen.append((p, t))
    )

    assert seen == [(10.0, 30.0), (20.0, 30.0), (30.0, 30.0)]


def test_silent_chunk_contributes_no_words(audio) -> None:
    model = FakeAsrModel(results=[FakeTranscription(text="", words=None)])

    result = transcribe_chunks(audio, [chunk(0, 0.0, 5.0)], model, "v3_e2e_rnnt")

    assert result.words == []
    assert result.chunks[0].end == 5.0


def test_text_without_timestamps_is_an_error(audio) -> None:
    model = FakeAsrModel(results=[FakeTranscription(text="добрый день", words=None)])

    with pytest.raises(AppError) as exc:
        transcribe_chunks(audio, [chunk(0, 0.0, 5.0)], model, "v3_e2e_rnnt")

    assert exc.value.code is ErrorCode.ASR_FAILED


def test_model_failure_is_reported_with_a_stable_code(audio) -> None:
    model = FakeAsrModel(raises=RuntimeError("boom"))

    with pytest.raises(AppError) as exc:
        transcribe_chunks(audio, [chunk(0, 0.0, 5.0)], model, "v3_e2e_rnnt")

    assert exc.value.code is ErrorCode.ASR_FAILED


def test_missing_audio_is_reported(tmp_path) -> None:
    with pytest.raises(AppError) as exc:
        transcribe_chunks(tmp_path / "nope.wav", [chunk(0, 0.0, 1.0)], FakeAsrModel(), "m")

    assert exc.value.code is ErrorCode.ASR_FAILED


def test_chunk_beyond_the_end_of_the_file_is_skipped(audio) -> None:
    model = FakeAsrModel()

    result = transcribe_chunks(audio, [chunk(0, 120.0, 130.0)], model, "v3_e2e_rnnt")

    assert model.calls == []
    assert result.words == []


def test_empty_words_are_dropped(audio) -> None:
    spoken = [FakeWord("  ", 0.0, 0.1), FakeWord("да", 1.0, 1.2)]
    model = FakeAsrModel(results=[FakeTranscription(text="да", words=spoken)])

    result = transcribe_chunks(audio, [chunk(0, 2.0, 5.0)], model, "v3_e2e_rnnt")

    assert [word.text for word in result.words] == ["да"]

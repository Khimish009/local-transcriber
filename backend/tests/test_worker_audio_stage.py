import shutil
import wave
from pathlib import Path

import pytest

from app.core.errors import AppError, ErrorCode
from app.schemas.job import JobSource, JobStatus
from tests.test_audio_pipeline import write_wav
from worker.jobs import process_job
from worker.pipeline.audio import TARGET_CHANNELS, TARGET_SAMPLE_RATE

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)


@pytest.fixture
def queued_job(store, storage, settings, monkeypatch, redis):
    """A job whose upload is already on disk, as after POST /api/v1/jobs."""
    monkeypatch.setattr("worker.jobs.get_redis", lambda: redis)
    monkeypatch.setattr("worker.jobs.get_settings", lambda: settings)
    monkeypatch.setattr("worker.jobs.diarize", lambda *args, **kwargs: None)
    monkeypatch.setattr("worker.jobs.transcribe", lambda *args, **kwargs: None)
    monkeypatch.setattr("worker.jobs.align", lambda *args, **kwargs: None)

    def _make(source_factory) -> str:
        job = store.create(JobSource(filename="meeting.wav", size_bytes=0))
        storage.prepare_job_dirs(job.job_id)
        source_factory(storage.source_path(job.job_id, "wav"))
        return job.job_id

    return _make


@requires_ffmpeg
def test_preparing_audio_writes_normalized_wav_and_metadata(queued_job, store, storage) -> None:
    job_id = queued_job(lambda path: write_wav(path, seconds=1.0, rate=44100, channels=2))

    process_job(job_id, stage_seconds=0)

    normalized = storage.normalized_path(job_id)
    with wave.open(str(normalized), "rb") as produced:
        assert produced.getnchannels() == TARGET_CHANNELS
        assert produced.getframerate() == TARGET_SAMPLE_RATE

    job = store.get(job_id)
    assert job.status is JobStatus.COMPLETED
    assert job.audio is not None
    assert job.audio.sample_rate == 44100
    assert job.audio.channels == 2
    assert 0.9 < job.audio.duration_seconds < 1.1


@requires_ffmpeg
def test_source_recording_is_left_untouched(queued_job, store, storage) -> None:
    job_id = queued_job(lambda path: write_wav(path, seconds=1.0))
    source = storage.find_source(job_id)
    before = source.read_bytes()

    process_job(job_id, stage_seconds=0)

    assert source.read_bytes() == before


@requires_ffmpeg
def test_corrupted_audio_fails_the_job_with_ffmpeg_code(queued_job, store) -> None:
    job_id = queued_job(lambda path: Path(path).write_bytes(b"RIFF" + b"\x00" * 64))

    with pytest.raises(AppError):
        process_job(job_id, stage_seconds=0)

    job = store.get(job_id)
    assert job.status is JobStatus.FAILED
    assert job.error_code is ErrorCode.FFMPEG_FAILED
    assert job.progress < 100


def test_missing_source_fails_the_job(store, storage, settings, monkeypatch, redis) -> None:
    monkeypatch.setattr("worker.jobs.get_redis", lambda: redis)
    monkeypatch.setattr("worker.jobs.get_settings", lambda: settings)
    job = store.create(JobSource(filename="meeting.wav", size_bytes=0))

    with pytest.raises(AppError):
        process_job(job.job_id, stage_seconds=0)

    failed = store.get(job.job_id)
    assert failed.status is JobStatus.FAILED
    assert failed.error_code is ErrorCode.FFMPEG_FAILED

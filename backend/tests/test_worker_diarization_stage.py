import json

import pytest

from app.core.errors import AppError, ErrorCode
from app.schemas.job import STAGE_PROGRESS_RANGE, JobSource, JobStatus
from tests.test_diarization import (
    EXCLUSIVE,
    REGULAR,
    FakeAnnotation,
    FakeOutput,
    FakePipeline,
    ReportingPipeline,
)
from worker.jobs import process_job


@pytest.fixture
def worker_env(monkeypatch, redis, settings, storage):
    """Real diarization stage, fake model — audio preparation is covered by its own tests."""
    monkeypatch.setattr("worker.jobs.get_redis", lambda: redis)
    monkeypatch.setattr("worker.jobs.get_settings", lambda: settings)

    def fake_prepare(job_id, store, storage_, settings_):
        storage_.prepare_job_dirs(job_id)
        storage_.normalized_path(job_id).write_bytes(b"RIFF fake wav")

    monkeypatch.setattr("worker.jobs.prepare_audio", fake_prepare)
    # ASR has its own stage tests; here it must not need a model.
    monkeypatch.setattr("worker.jobs.transcribe", lambda *args, **kwargs: None)
    monkeypatch.setattr("worker.jobs.align", lambda *args, **kwargs: None)
    monkeypatch.setattr("worker.jobs.generate_exports", lambda *args, **kwargs: None)


def install_pipeline(monkeypatch, pipeline) -> None:
    monkeypatch.setattr("worker.jobs.get_diarization_pipeline", lambda *args: pipeline)


def make_job(store, speaker_count=None) -> str:
    job = store.create(JobSource(filename="meeting.wav", size_bytes=10), speaker_count)
    return job.job_id


def test_stage_writes_diarization_json(worker_env, store, storage, monkeypatch) -> None:
    install_pipeline(
        monkeypatch, FakePipeline(FakeOutput(FakeAnnotation(REGULAR), FakeAnnotation(EXCLUSIVE)))
    )
    job_id = make_job(store)

    process_job(job_id)

    payload = json.loads(storage.diarization_path(job_id).read_text(encoding="utf-8"))
    assert payload["speakers"] == ["SPEAKER_00", "SPEAKER_01"]
    assert len(payload["segments"]) == 3
    assert len(payload["exclusive"]) == 3
    assert store.get(job_id).status is JobStatus.COMPLETED


def test_exact_speaker_count_reaches_the_pipeline(worker_env, store, monkeypatch) -> None:
    pipeline = FakePipeline(FakeOutput(FakeAnnotation(REGULAR), FakeAnnotation(EXCLUSIVE)))
    install_pipeline(monkeypatch, pipeline)

    process_job(make_job(store, speaker_count=2))

    assert pipeline.calls[0]["num_speakers"] == 2


def test_missing_hf_token_fails_the_job(worker_env, store, monkeypatch) -> None:
    def refuse(*args):
        raise AppError(ErrorCode.HF_TOKEN_REQUIRED, "HF_TOKEN is required")

    monkeypatch.setattr("worker.jobs.get_diarization_pipeline", refuse)
    job_id = make_job(store)

    with pytest.raises(AppError):
        process_job(job_id)

    job = store.get(job_id)
    assert job.status is JobStatus.FAILED
    assert job.error_code is ErrorCode.HF_TOKEN_REQUIRED
    start, end = STAGE_PROGRESS_RANGE[JobStatus.DIARIZING]
    assert start <= job.progress < end, "failure happened inside the diarization progress window"


def test_diarization_failure_marks_the_job(worker_env, store, monkeypatch) -> None:
    install_pipeline(monkeypatch, FakePipeline(None, raises=RuntimeError("boom")))
    job_id = make_job(store)

    with pytest.raises(AppError):
        process_job(job_id)

    assert store.get(job_id).error_code is ErrorCode.DIARIZATION_FAILED


def test_progress_follows_pyannote_steps(worker_env, store, monkeypatch) -> None:
    """T12.4 — the bar moves during diarization instead of sitting still for most of it."""
    install_pipeline(monkeypatch, ReportingPipeline(batches=10))
    job_id = make_job(store)
    start, end = STAGE_PROGRESS_RANGE[JobStatus.DIARIZING]
    seen: list[tuple[int, str]] = []
    original = store.__class__.update

    def record(self, job_id_, **kwargs):
        job = original(self, job_id_, **kwargs)
        if job.status is JobStatus.DIARIZING:
            seen.append((job.progress, job.message))
        return job

    monkeypatch.setattr(store.__class__, "update", record)

    process_job(job_id)

    progress = [value for value, _ in seen]
    assert progress[0] == start
    assert progress[-1] == end
    assert progress == sorted(progress)
    assert len(set(progress)) > 10, "progress must reflect pyannote's batches"
    messages = [message for _, message in seen]
    assert len(messages) == len(set(messages)), "repeated percents must not reach Redis"


def test_deleting_the_job_stops_diarization_midway(worker_env, store, monkeypatch) -> None:
    """The progress update is the first moment the worker learns the job is gone."""
    pipeline = ReportingPipeline(batches=10)
    install_pipeline(monkeypatch, pipeline)
    job_id = make_job(store)
    original = store.__class__.update

    def vanish(self, job_id_, **kwargs):
        if (kwargs.get("message") or "").endswith("%"):
            self.delete(job_id_)
        return original(self, job_id_, **kwargs)

    monkeypatch.setattr(store.__class__, "update", vanish)

    process_job(job_id)  # must not raise: the user asked to forget this job

    assert store.get(job_id) is None
    assert pipeline.reported < pipeline.steps, "pyannote must stop at the first update"

import pytest

from app.core.errors import AppError, ErrorCode
from app.schemas.job import JobSource, JobStatus
from app.services.job_store import events_channel, job_key


def _source() -> JobSource:
    return JobSource(filename="meeting.m4a", size_bytes=1024, content_type="audio/mp4")


def test_create_persists_job_with_uuid(store, redis) -> None:
    job = store.create(_source(), speaker_count=3)

    assert job.status is JobStatus.QUEUED
    assert job.progress == 0
    assert job.speaker_count == 3
    assert redis.exists(job_key(job.job_id))
    assert store.get(job.job_id) == job


def test_get_unknown_job_returns_none(store) -> None:
    assert store.get("11111111-1111-1111-1111-111111111111") is None


def test_require_unknown_job_raises_job_not_found(store) -> None:
    with pytest.raises(AppError) as exc:
        store.require("11111111-1111-1111-1111-111111111111")

    assert exc.value.code is ErrorCode.JOB_NOT_FOUND
    assert exc.value.status_code == 404


def test_update_advances_status_stage_and_timestamp(store) -> None:
    job = store.create(_source())

    updated = store.update(job.job_id, status=JobStatus.DIARIZING, progress=20)

    assert updated.status is JobStatus.DIARIZING
    assert updated.current_stage is JobStatus.DIARIZING
    assert updated.progress == 20
    assert updated.updated_at >= job.updated_at


def test_update_clamps_progress(store) -> None:
    job = store.create(_source())

    assert store.update(job.job_id, progress=140).progress == 100
    assert store.update(job.job_id, progress=-5).progress == 0


def test_fail_sets_terminal_state_and_error_code(store) -> None:
    job = store.create(_source())

    failed = store.fail(job.job_id, "boom", error_code=ErrorCode.FFMPEG_FAILED)

    assert failed.status is JobStatus.FAILED
    assert failed.error_code is ErrorCode.FFMPEG_FAILED
    assert failed.message == "boom"
    assert failed.is_terminal


def test_updates_are_published_to_the_job_channel(store, redis) -> None:
    job = store.create(_source())
    pubsub = redis.pubsub()
    pubsub.subscribe(events_channel(job.job_id))
    pubsub.get_message(timeout=1)  # subscribe confirmation

    store.update(job.job_id, status=JobStatus.TRANSCRIBING, progress=40)

    message = pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
    assert message is not None
    assert b'"TRANSCRIBING"' in message["data"]


def test_delete_removes_record(store) -> None:
    job = store.create(_source())

    store.delete(job.job_id)

    assert store.get(job.job_id) is None


# --- processing time (T11.2) --------------------------------------------------


def test_timestamps_are_absent_until_the_worker_picks_the_job_up(store) -> None:
    job = store.create(JobSource(filename="meeting.wav", size_bytes=10))

    assert job.started_at is None
    assert job.finished_at is None
    assert job.processing_seconds is None


def test_first_processing_stage_stamps_the_start(store) -> None:
    job = store.create(JobSource(filename="meeting.wav", size_bytes=10))

    started = store.update(job.job_id, status=JobStatus.PREPARING_AUDIO)

    assert started.started_at is not None
    assert started.finished_at is None
    assert started.processing_seconds is None, "still running"


def test_start_is_stamped_once_and_never_moves(store) -> None:
    job = store.create(JobSource(filename="meeting.wav", size_bytes=10))
    first = store.update(job.job_id, status=JobStatus.PREPARING_AUDIO).started_at

    later = store.update(job.job_id, status=JobStatus.TRANSCRIBING)

    assert later.started_at == first


def test_completion_stamps_the_finish_and_yields_a_duration(store) -> None:
    job = store.create(JobSource(filename="meeting.wav", size_bytes=10))
    store.update(job.job_id, status=JobStatus.DIARIZING)

    done = store.update(job.job_id, status=JobStatus.COMPLETED, progress=100)

    assert done.finished_at is not None
    assert done.processing_seconds is not None
    assert done.processing_seconds >= 0


def test_failure_also_stamps_the_finish(store) -> None:
    job = store.create(JobSource(filename="meeting.wav", size_bytes=10))
    store.update(job.job_id, status=JobStatus.DIARIZING)

    failed = store.fail(job.job_id, "boom")

    assert failed.finished_at is not None
    assert failed.processing_seconds is not None


def test_job_that_never_started_has_no_duration(store) -> None:
    """Crash recovery can fail a job that was still sitting in the queue."""
    job = store.create(JobSource(filename="meeting.wav", size_bytes=10))

    failed = store.fail(job.job_id, "worker crashed")

    assert failed.started_at is None
    assert failed.finished_at is not None
    assert failed.processing_seconds is None

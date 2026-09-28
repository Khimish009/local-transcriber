import pytest

from app.schemas.job import STAGE_PROGRESS_RANGE, JobSource, JobStatus
from worker.jobs import PLACEHOLDER_STAGES, process_job, stage_progress


@pytest.fixture
def worker_env(monkeypatch, redis, settings):
    """Isolate the state machine from the real audio stage, which has its own tests."""
    monkeypatch.setattr("worker.jobs.get_redis", lambda: redis)
    monkeypatch.setattr("worker.jobs.get_settings", lambda: settings)
    monkeypatch.setattr("worker.jobs.prepare_audio", lambda *args, **kwargs: None)


def test_stage_progress_stays_inside_the_stage_window() -> None:
    for stage in (JobStatus.PREPARING_AUDIO, *PLACEHOLDER_STAGES):
        start, end = STAGE_PROGRESS_RANGE[stage]
        assert stage_progress(stage, 0, 4) == start
        assert stage_progress(stage, 4, 4) == end
        assert start <= stage_progress(stage, 2, 4) <= end


def test_process_job_walks_the_state_machine(worker_env, store, monkeypatch) -> None:
    job = store.create(JobSource(filename="meeting.wav", size_bytes=10))
    seen: list[tuple[str, int]] = []
    original_update = store.__class__.update

    def record(self, job_id, **kwargs):
        result = original_update(self, job_id, **kwargs)
        seen.append((result.status.value, result.progress))
        return result

    monkeypatch.setattr(store.__class__, "update", record)

    process_job(job.job_id, stage_seconds=0)

    statuses = [status for status, _ in seen]
    assert statuses[-1] == JobStatus.COMPLETED.value
    for stage in PLACEHOLDER_STAGES:
        assert stage.value in statuses

    progress = [value for _, value in seen]
    assert progress == sorted(progress), "progress must never go backwards"

    final = store.get(job.job_id)
    assert final.status is JobStatus.COMPLETED
    assert final.progress == 100
    assert final.is_terminal


def test_process_job_marks_failure_and_reraises(worker_env, store, monkeypatch) -> None:
    job = store.create(JobSource(filename="meeting.wav", size_bytes=10))
    original_update = store.__class__.update

    def flaky(self, job_id, **kwargs):
        if kwargs.get("status") is JobStatus.DIARIZING:
            raise RuntimeError("stage exploded")
        return original_update(self, job_id, **kwargs)

    monkeypatch.setattr(store.__class__, "update", flaky)

    with pytest.raises(RuntimeError):
        process_job(job.job_id, stage_seconds=0)

    final = store.get(job.job_id)
    assert final.status is JobStatus.FAILED
    assert final.message == "Internal error while processing the job"
    assert final.error_code is None

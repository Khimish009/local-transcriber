"""T8.2 — jobs orphaned by a worker crash must not stay unfinished forever."""

from rq.job import Job as RqJob

from app.core.errors import ErrorCode
from app.schemas.job import JobSource, JobStatus
from app.services.queue import enqueue_job, fetch_rq_job
from app.services.recovery import recover_orphaned_jobs


def _job(store, status: JobStatus | None = None, progress: int = 0):
    job = store.create(JobSource(filename="meeting.wav", size_bytes=1))
    if status is not None:
        store.update(job.job_id, status=status, progress=progress)
    return job.job_id


def test_running_job_without_a_worker_is_failed(store, redis) -> None:
    job_id = _job(store, JobStatus.TRANSCRIBING, 40)

    assert recover_orphaned_jobs(redis) == [job_id]

    job = store.require(job_id)
    assert job.status is JobStatus.FAILED
    assert job.error_code is ErrorCode.WORKER_CRASHED
    assert job.progress == 40  # progress is not rewound: it shows where the crash happened


def test_job_still_waiting_in_the_queue_is_left_alone(store, redis, settings) -> None:
    job_id = _job(store)
    enqueue_job(redis, settings, job_id)

    assert recover_orphaned_jobs(redis) == []
    assert store.require(job_id).status is JobStatus.QUEUED


def test_queued_job_whose_rq_record_vanished_is_failed(store, redis, settings) -> None:
    job_id = _job(store)
    enqueue_job(redis, settings, job_id)
    RqJob.fetch(job_id, connection=redis).delete()

    assert recover_orphaned_jobs(redis) == [job_id]
    assert store.require(job_id).error_code is ErrorCode.WORKER_CRASHED


def test_started_rq_job_is_treated_as_orphaned(store, redis, settings) -> None:
    job_id = _job(store, JobStatus.DIARIZING, 10)
    enqueue_job(redis, settings, job_id)
    RqJob.fetch(job_id, connection=redis).set_status("started")

    assert recover_orphaned_jobs(redis) == [job_id]
    # The stale RQ record is dropped too, so the job is never picked up again.
    assert fetch_rq_job(redis, job_id) is None


def test_finished_jobs_are_untouched(store, redis) -> None:
    completed = _job(store, JobStatus.COMPLETED, 100)
    failed = _job(store, JobStatus.FAILED)

    assert recover_orphaned_jobs(redis) == []
    assert store.require(completed).status is JobStatus.COMPLETED
    assert store.require(failed).error_code is None


def test_recovery_is_idempotent(store, redis) -> None:
    _job(store, JobStatus.ALIGNING, 88)

    assert len(recover_orphaned_jobs(redis)) == 1
    assert recover_orphaned_jobs(redis) == []


def test_unreadable_record_does_not_stop_recovery(store, redis) -> None:
    redis.set("job:broken", b"{not json}")
    job_id = _job(store, JobStatus.TRANSCRIBING, 50)

    assert recover_orphaned_jobs(redis) == [job_id]

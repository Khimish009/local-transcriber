"""T8.3 — manual job deletion."""

import io

from rq import Queue
from rq.job import Job as RqJob

from app.schemas.job import JobSource, JobStatus
from app.services.cleanup import delete_job
from app.services.job_store import job_key
from app.services.queue import enqueue_job, fetch_rq_job

WAV_BYTES = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32


def _upload(client, filename="meeting.wav"):
    return client.post(
        "/api/v1/jobs",
        files={"file": (filename, io.BytesIO(WAV_BYTES), "audio/wav")},
    ).json()["job_id"]


def test_delete_removes_record_files_and_queue_entry(client, settings, redis, storage) -> None:
    job_id = _upload(client)
    assert storage.job_dir(job_id).is_dir()

    response = client.delete(f"/api/v1/jobs/{job_id}")

    assert response.status_code == 204
    assert client.get(f"/api/v1/jobs/{job_id}").status_code == 404
    assert not storage.job_dir(job_id).exists()
    assert Queue(settings.queue_name, connection=redis).job_ids == []
    assert not redis.exists(job_key(job_id))


def test_delete_leaves_other_jobs_alone(client, storage) -> None:
    kept = _upload(client, filename="kept.wav")
    removed = _upload(client, filename="removed.wav")

    client.delete(f"/api/v1/jobs/{removed}")

    assert client.get(f"/api/v1/jobs/{kept}").status_code == 200
    assert storage.job_dir(kept).is_dir()


def test_delete_unknown_job_is_404(client) -> None:
    response = client.delete("/api/v1/jobs/2f1f5c3e-0000-4000-8000-000000000000")

    assert response.status_code == 404
    assert response.json()["error_code"] == "JOB_NOT_FOUND"


def test_delete_rejects_a_non_uuid_id(client) -> None:
    assert client.delete("/api/v1/jobs/../../etc").status_code == 404


def test_delete_a_running_job_drops_the_rq_record(settings, redis, store, storage) -> None:
    job = store.create(JobSource(filename="meeting.wav", size_bytes=1))
    enqueue_job(redis, settings, job.job_id)
    rq_job = RqJob.fetch(job.job_id, connection=redis)
    rq_job.set_status("started")
    store.update(job.job_id, status=JobStatus.DIARIZING, progress=10)

    delete_job(redis, store, storage, job.job_id)

    assert store.get(job.job_id) is None
    assert fetch_rq_job(redis, job.job_id) is None

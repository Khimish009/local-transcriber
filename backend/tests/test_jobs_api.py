import io

from rq import Queue

from app.schemas.job import JobStatus

WAV_BYTES = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32


def _upload(client, filename="meeting.wav", content=WAV_BYTES, content_type="audio/wav", **data):
    return client.post(
        "/api/v1/jobs",
        files={"file": (filename, io.BytesIO(content), content_type)},
        data=data,
    )


def test_create_job_stores_file_and_enqueues(client, settings, redis, storage) -> None:
    response = _upload(client, speaker_count=2)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == JobStatus.QUEUED.value

    job_id = body["job_id"]
    source = storage.source_path(job_id, "wav")
    assert source.read_bytes() == WAV_BYTES
    assert storage.work_dir(job_id).is_dir()
    assert storage.result_dir(job_id).is_dir()

    queue = Queue(settings.queue_name, connection=redis)
    assert queue.job_ids == [job_id]


def test_create_job_records_metadata(client) -> None:
    job_id = _upload(client, filename="../Запись встречи.wav", speaker_count=3).json()["job_id"]

    job = client.get(f"/api/v1/jobs/{job_id}").json()

    assert job["source"]["filename"] == "Запись_встречи.wav"
    assert job["source"]["size_bytes"] == len(WAV_BYTES)
    assert job["speaker_count"] == 3
    assert job["progress"] == 0
    assert job["current_stage"] == JobStatus.QUEUED.value


def test_create_job_without_speaker_count_means_auto(client) -> None:
    job_id = _upload(client).json()["job_id"]

    assert client.get(f"/api/v1/jobs/{job_id}").json()["speaker_count"] is None


def test_rejects_unsupported_extension(client, settings) -> None:
    response = _upload(client, filename="notes.txt", content_type="text/plain")

    assert response.status_code == 400
    assert response.json()["error_code"] == "UNSUPPORTED_FORMAT"
    assert not (settings.data_dir / "jobs").exists()


def test_rejects_unsupported_content_type(client) -> None:
    response = _upload(client, filename="meeting.wav", content_type="text/plain")

    assert response.status_code == 400
    assert response.json()["error_code"] == "UNSUPPORTED_FORMAT"


def test_rejects_file_over_the_limit(client, settings, redis) -> None:
    oversized = b"x" * (settings.max_upload_bytes + 1024)

    response = _upload(client, content=oversized)

    assert response.status_code == 413
    assert response.json()["error_code"] == "FILE_TOO_LARGE"
    # Nothing is left behind: no job record, no queued work, no partial file.
    assert Queue(settings.queue_name, connection=redis).job_ids == []
    assert not any((settings.data_dir / "jobs").glob("*/source/*"))


def test_rejects_invalid_speaker_count(client) -> None:
    assert _upload(client, speaker_count=0).status_code == 422
    assert _upload(client, speaker_count=99).status_code == 422


def test_get_unknown_job_returns_404(client) -> None:
    response = client.get("/api/v1/jobs/11111111-1111-1111-1111-111111111111")

    assert response.status_code == 404
    assert response.json()["error_code"] == "JOB_NOT_FOUND"


def test_get_job_with_non_uuid_id_returns_404(client) -> None:
    # Job ids must be UUIDs; this is what keeps a crafted id out of the data directory.
    response = client.get("/api/v1/jobs/etc-passwd")

    assert response.status_code == 404
    assert response.json()["error_code"] == "JOB_NOT_FOUND"


# --- job list (T11.1) ---------------------------------------------------------


def test_list_returns_jobs_newest_first(client) -> None:
    first = _upload(client, filename="first.wav").json()["job_id"]
    second = _upload(client, filename="second.wav").json()["job_id"]

    body = client.get("/api/v1/jobs").json()

    assert [job["job_id"] for job in body] == [second, first]
    assert body[0]["source"]["filename"] == "second.wav"


def test_list_is_empty_when_nothing_was_uploaded(client) -> None:
    assert client.get("/api/v1/jobs").json() == []


def test_list_respects_the_limit(client) -> None:
    for index in range(3):
        _upload(client, filename=f"recording-{index}.wav")

    assert len(client.get("/api/v1/jobs", params={"limit": 2}).json()) == 2


def test_list_rejects_a_nonsense_limit(client) -> None:
    assert client.get("/api/v1/jobs", params={"limit": 0}).status_code == 422


def test_deleted_job_disappears_from_the_list(client) -> None:
    kept = _upload(client, filename="kept.wav").json()["job_id"]
    removed = _upload(client, filename="removed.wav").json()["job_id"]

    client.delete(f"/api/v1/jobs/{removed}")

    assert [job["job_id"] for job in client.get("/api/v1/jobs").json()] == [kept]


def test_list_skips_a_record_it_cannot_parse(client, redis) -> None:
    job_id = _upload(client).json()["job_id"]
    redis.set("job:broken", b"{not json}")

    assert [job["job_id"] for job in client.get("/api/v1/jobs").json()] == [job_id]

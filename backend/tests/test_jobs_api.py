import io

import pytest
from redis.exceptions import RedisError
from rq import Queue

from app.schemas.job import JobStatus
from app.services.cleanup import delete_job
from app.services.job_store import JobStore
from app.services.upload import MULTIPART_OVERHEAD_BYTES
from tests.test_upload import CONTENT_TYPE, Part, multipart_body

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


def test_job_carries_processing_time_computed_on_the_server(client, store) -> None:
    """The UI does not re-derive processing time; the API ships it (docs/processing-time.md)."""
    job_id = _upload(client).json()["job_id"]
    store.update(job_id, status=JobStatus.DIARIZING)

    job = client.get(f"/api/v1/jobs/{job_id}").json()
    listed = client.get("/api/v1/jobs").json()[0]

    assert job["processing_seconds"] >= 0
    assert job["processing_includes_queue"] is False
    assert "processing_seconds" in listed


# --- upload parsed as it arrives (T13.7) ------------------------------------------------

AUDIO_PART = Part("file", WAV_BYTES, filename="meeting.wav", content_type="audio/wav")
NOTHING_LEFT = ([], [], [])


def _post_form(client, *parts: Part):
    """POST a hand-built multipart body: unlike httpx's `files=`, the part order is ours."""
    return client.post(
        "/api/v1/jobs",
        content=multipart_body(*parts),
        headers={"content-type": CONTENT_TYPE},
    )


def _leftovers(settings, redis, store):
    """Job records, queued ids and job folders: all empty when a refused upload left no trace."""
    jobs_dir = settings.data_dir / "jobs"
    return (
        [job.job_id for job in store.list_jobs()],
        Queue(settings.queue_name, connection=redis).job_ids,
        sorted(path.name for path in jobs_dir.glob("*")),
    )


@pytest.fixture
def created_jobs(monkeypatch) -> list[str]:
    """Ids handed out by `JobStore.create`, to tell a clean refusal from a cleaned-up job."""
    ids: list[str] = []
    original_create = JobStore.create

    def recording_create(self, *args, **kwargs):
        job = original_create(self, *args, **kwargs)
        ids.append(job.job_id)
        return job

    monkeypatch.setattr(JobStore, "create", recording_create)
    return ids


async def _asgi_upload(client, receive) -> int:
    """POST /jobs straight at the ASGI app, so the test decides what arrives and when."""
    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/jobs",
        "raw_path": b"/api/v1/jobs",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver"), (b"content-type", CONTENT_TYPE.encode())],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    await client.app(scope, receive, send)
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def test_speaker_count_sent_after_the_file_is_honoured(client, storage) -> None:
    """Browsers send fields in form order, so `speaker_count` normally follows the file."""
    response = _post_form(client, AUDIO_PART, Part("speaker_count", b"4"))

    assert response.status_code == 201
    job_id = response.json()["job_id"]
    assert client.get(f"/api/v1/jobs/{job_id}").json()["speaker_count"] == 4
    assert storage.source_path(job_id, "wav").read_bytes() == WAV_BYTES
    assert [path.name for path in storage.source_dir(job_id).iterdir()] == ["original.wav"]


def test_chunked_upload_without_content_length_is_accepted(client, storage) -> None:
    body = multipart_body(AUDIO_PART, Part("speaker_count", b"2"))

    def pieces():
        yield body[:40]
        yield body[40:]

    response = client.post("/api/v1/jobs", content=pieces(), headers={"content-type": CONTENT_TYPE})

    assert "content-length" not in response.request.headers
    assert response.status_code == 201
    job_id = response.json()["job_id"]
    assert storage.source_path(job_id, "wav").read_bytes() == WAV_BYTES
    assert client.get(f"/api/v1/jobs/{job_id}").json()["speaker_count"] == 2


def test_upload_declared_too_large_is_refused_before_a_job_exists(
    client, settings, redis, store, monkeypatch
) -> None:
    """The Content-Length alone settles it: the body is never parsed, no record is created."""

    def create_must_not_run(*args, **kwargs):
        pytest.fail("a job was created for an upload declared too large")

    monkeypatch.setattr(JobStore, "create", create_must_not_run)
    allowance = settings.max_upload_bytes + MULTIPART_OVERHEAD_BYTES
    oversized = Part("file", b"x" * allowance, filename="meeting.wav", content_type="audio/wav")

    response = _post_form(client, oversized)

    assert int(response.request.headers["content-length"]) > allowance
    assert response.status_code == 413
    assert response.json()["error_code"] == "FILE_TOO_LARGE"
    assert _leftovers(settings, redis, store) == NOTHING_LEFT


def test_oversized_chunked_upload_is_cut_off_and_leaves_nothing_behind(
    client, settings, redis, store, created_jobs
) -> None:
    """Without a Content-Length there is nothing to judge up front: the byte count decides."""
    oversized = Part(
        "file",
        b"x" * (settings.max_upload_bytes + 1),
        filename="meeting.wav",
        content_type="audio/wav",
    )
    body = multipart_body(oversized)

    def pieces():
        for start in range(0, len(body), 64 * 1024):
            yield body[start : start + 64 * 1024]

    response = client.post("/api/v1/jobs", content=pieces(), headers={"content-type": CONTENT_TYPE})

    assert "content-length" not in response.request.headers
    assert response.status_code == 413
    assert response.json()["error_code"] == "FILE_TOO_LARGE"
    assert len(created_jobs) == 1, "the job existed while the file was arriving"
    assert _leftovers(settings, redis, store) == NOTHING_LEFT


def test_missing_file_is_a_validation_error_on_body_file(client, settings, redis, store) -> None:
    response = _post_form(client, Part("speaker_count", b"2"))

    assert response.status_code == 422
    error = response.json()["detail"][0]
    assert error["loc"] == ["body", "file"]
    assert error["type"] == "missing"
    assert _leftovers(settings, redis, store) == NOTHING_LEFT


@pytest.mark.parametrize(
    "request_kwargs",
    [
        pytest.param({"json": {"file": "meeting.wav"}}, id="json"),
        pytest.param({"data": {"speaker_count": "2"}}, id="urlencoded"),
        pytest.param({}, id="no-body"),
    ],
)
def test_body_that_is_not_a_form_is_a_validation_error(client, request_kwargs) -> None:
    response = client.post("/api/v1/jobs", **request_kwargs)

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "file"]


VALID_FORM = multipart_body(AUDIO_PART)


@pytest.mark.parametrize(
    ("content", "content_type"),
    [
        pytest.param(b"this is not multipart", CONTENT_TYPE, id="garbage"),
        pytest.param(
            multipart_body(AUDIO_PART, boundary="other"), CONTENT_TYPE, id="wrong-boundary"
        ),
        pytest.param(VALID_FORM, "multipart/form-data", id="no-boundary"),
    ],
)
def test_malformed_multipart_is_a_400(
    client, settings, redis, store, content, content_type
) -> None:
    response = client.post("/api/v1/jobs", content=content, headers={"content-type": content_type})

    assert response.status_code == 400
    assert isinstance(response.json()["detail"], str)
    assert _leftovers(settings, redis, store) == NOTHING_LEFT


def test_truncated_upload_discards_the_job_it_had_started(
    client, settings, redis, store, created_jobs
) -> None:
    halfway = VALID_FORM.index(WAV_BYTES) + 10

    response = client.post(
        "/api/v1/jobs", content=VALID_FORM[:halfway], headers={"content-type": CONTENT_TYPE}
    )

    assert response.status_code == 400
    assert len(created_jobs) == 1, "the job existed once the file's headers had arrived"
    assert _leftovers(settings, redis, store) == NOTHING_LEFT


def test_empty_speaker_count_means_auto_detect(client) -> None:
    response = _post_form(client, AUDIO_PART, Part("speaker_count", b""))

    assert response.status_code == 201
    assert client.get(f"/api/v1/jobs/{response.json()['job_id']}").json()["speaker_count"] is None


@pytest.mark.parametrize("value", [b"abc", b"0", b"21", b"1.5"])
@pytest.mark.parametrize("file_first", [True, False], ids=["file-first", "count-first"])
def test_invalid_speaker_count_is_a_422_and_leaves_nothing_behind(
    client, settings, redis, store, value, file_first
) -> None:
    count = Part("speaker_count", value)

    response = _post_form(client, *((AUDIO_PART, count) if file_first else (count, AUDIO_PART)))

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "speaker_count"]
    assert _leftovers(settings, redis, store) == NOTHING_LEFT


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        pytest.param("notes.txt", "audio/wav", id="extension"),
        pytest.param("recording", "audio/wav", id="no-extension"),
        pytest.param("meeting.wav", "text/plain", id="content-type"),
    ],
)
def test_refused_file_creates_no_job_record(
    client, settings, redis, store, created_jobs, filename, content_type
) -> None:
    """The checks run on the part's headers, before a record that would show in the list."""
    response = _upload(client, filename=filename, content_type=content_type)

    assert response.status_code == 400
    assert response.json()["error_code"] == "UNSUPPORTED_FORMAT"
    assert created_jobs == []
    assert _leftovers(settings, redis, store) == NOTHING_LEFT


def test_second_file_part_is_a_422_and_the_job_already_started_is_discarded(
    client, settings, redis, store, created_jobs
) -> None:
    second = Part("file", WAV_BYTES, filename="second.wav", content_type="audio/wav")

    response = _post_form(client, AUDIO_PART, second)

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "file"]
    assert len(created_jobs) == 1
    assert _leftovers(settings, redis, store) == NOTHING_LEFT


def test_file_sent_as_text_is_a_422_that_does_not_claim_it_is_missing(
    client, settings, redis, store
) -> None:
    """What `curl -F file=meeting.wav` (no `@`) sends."""
    response = _post_form(client, Part("file", b"meeting.wav"))

    assert response.status_code == 422
    error = response.json()["detail"][0]
    assert error["loc"] == ["body", "file"]
    assert error["type"] != "missing"
    assert _leftovers(settings, redis, store) == NOTHING_LEFT


def test_job_deleted_during_the_upload_does_not_come_back(
    client, settings, redis, store, monkeypatch
) -> None:
    """T13.6 at the door: a DELETE landing before the record is saved must win."""
    original_replace = JobStore.replace

    def delete_then_replace(self, job):
        self.delete(job.job_id)  # the user hit "delete" while the upload was finishing
        return original_replace(self, job)

    monkeypatch.setattr(JobStore, "replace", delete_then_replace)

    response = _upload(client)

    assert response.status_code == 404
    assert response.json()["error_code"] == "JOB_NOT_FOUND"
    assert _leftovers(settings, redis, store) == NOTHING_LEFT


def test_queue_failure_discards_the_uploaded_job(
    client, settings, redis, store, created_jobs, monkeypatch
) -> None:
    def queue_is_down(*args, **kwargs):
        raise RedisError("queue is down")

    monkeypatch.setattr("app.api.v1.jobs.enqueue_job", queue_is_down)

    response = _upload(client)

    assert response.status_code == 503
    assert len(created_jobs) == 1
    assert _leftovers(settings, redis, store) == NOTHING_LEFT


def test_redis_down_as_the_upload_starts_is_a_503(
    client, settings, redis, store, monkeypatch
) -> None:
    def redis_is_down(*args, **kwargs):
        raise RedisError("redis is down")

    monkeypatch.setattr(JobStore, "create", redis_is_down)

    response = _upload(client)

    assert response.status_code == 503
    assert _leftovers(settings, redis, store) == NOTHING_LEFT


async def test_client_disconnect_mid_upload_leaves_nothing_behind(
    client, settings, redis, store
) -> None:
    """The user closed the page: the half-received job and its file must not linger."""
    body = multipart_body(AUDIO_PART)
    messages = iter(
        [
            {"type": "http.request", "body": body[: body.index(WAV_BYTES) + 10], "more_body": True},
            {"type": "http.disconnect"},
        ]
    )
    records_when_dropped: list[list[str]] = []

    async def receive() -> dict:
        message = next(messages)
        if message["type"] == "http.disconnect":
            records_when_dropped.append(_leftovers(settings, redis, store)[0])
        return message

    status = await _asgi_upload(client, receive)

    assert status == 400
    assert len(records_when_dropped[0]) == 1, "a job was in flight when the connection dropped"
    assert _leftovers(settings, redis, store) == NOTHING_LEFT


async def test_job_deleted_while_the_file_is_arriving_does_not_come_back(
    client, settings, redis, store, storage
) -> None:
    """The job is in the list from the first bytes, so DELETE can land mid-upload."""
    body = multipart_body(AUDIO_PART)
    halfway = body.index(WAV_BYTES) + 10
    delivered: list[bool] = []

    async def receive() -> dict:
        if not delivered:
            delivered.append(True)
            return {"type": "http.request", "body": body[:halfway], "more_body": True}
        (job,) = store.list_jobs()
        delete_job(redis, store, storage, job.job_id)  # what DELETE /api/v1/jobs/{id} does
        return {"type": "http.request", "body": body[halfway:], "more_body": False}

    status = await _asgi_upload(client, receive)

    assert status == 404
    assert _leftovers(settings, redis, store) == NOTHING_LEFT


async def test_file_is_in_its_job_folder_before_the_body_ends(client, settings) -> None:
    """T13.7: written once, straight to its place, not spooled by the framework first."""
    body = multipart_body(AUDIO_PART)
    halfway = body.index(WAV_BYTES) + 10
    delivered: list[bool] = []
    in_source_folders: list[str] = []

    async def receive() -> dict:
        if not delivered:
            delivered.append(True)
            return {"type": "http.request", "body": body[:halfway], "more_body": True}
        in_source_folders.extend(p.name for p in (settings.data_dir / "jobs").glob("*/source/*"))
        return {"type": "http.request", "body": body[halfway:], "more_body": False}

    status = await _asgi_upload(client, receive)

    assert status == 201
    assert in_source_folders == ["original.wav"]


def test_openapi_documents_the_multipart_form(client) -> None:
    """The endpoint reads the body itself, so /docs only knows the form from `openapi_extra`."""
    operation = client.get("/openapi.json").json()["paths"]["/api/v1/jobs"]["post"]

    request_body = operation["requestBody"]
    assert request_body["required"] is True
    assert list(request_body["content"]) == ["multipart/form-data"]
    schema = request_body["content"]["multipart/form-data"]["schema"]
    assert schema["required"] == ["file"]
    assert schema["properties"]["file"]["format"] == "binary"
    speaker_count = schema["properties"]["speaker_count"]
    assert speaker_count["type"] == "integer"
    assert (speaker_count["minimum"], speaker_count["maximum"]) == (1, 20)

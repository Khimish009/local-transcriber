import json

from app.schemas.job import JobSource, JobStatus
from app.services.events import job_event_stream


def _parse_events(chunks: list[str]) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for raw in "".join(chunks).split("\n\n"):
        if not raw.strip() or raw.startswith(":"):
            continue
        lines = dict(line.split(": ", 1) for line in raw.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


async def test_stream_emits_snapshot_then_updates(store, async_redis, settings) -> None:
    job = store.create(JobSource(filename="meeting.wav", size_bytes=10))
    chunks: list[str] = []

    stream = job_event_stream(job.job_id, store, async_redis, settings)
    chunks.append(await anext(stream))

    store.update(job.job_id, status=JobStatus.TRANSCRIBING, progress=40)
    chunks.append(await anext(stream))

    store.update(job.job_id, status=JobStatus.COMPLETED, progress=100)
    chunks.append(await anext(stream))  # job event
    chunks.append(await anext(stream))  # done event
    await stream.aclose()

    events = _parse_events(chunks)
    assert [name for name, _ in events] == ["job", "job", "job", "done"]
    assert events[0][1]["status"] == JobStatus.QUEUED.value
    assert events[1][1]["progress"] == 40
    assert events[-1][1]["status"] == JobStatus.COMPLETED.value


async def test_stream_closes_immediately_for_terminal_job(store, async_redis, settings) -> None:
    job = store.create(JobSource(filename="meeting.wav", size_bytes=10))
    store.fail(job.job_id, "boom")

    chunks = [chunk async for chunk in job_event_stream(job.job_id, store, async_redis, settings)]

    events = _parse_events(chunks)
    assert [name for name, _ in events] == ["job", "done"]
    assert events[0][1]["status"] == JobStatus.FAILED.value


def test_events_endpoint_returns_404_for_unknown_job(client) -> None:
    response = client.get("/api/v1/jobs/11111111-1111-1111-1111-111111111111/events")

    assert response.status_code == 404
    assert response.json()["error_code"] == "JOB_NOT_FOUND"


async def test_stream_closes_when_the_job_is_deleted(store, async_redis, settings) -> None:
    """T8.3 — a deleted job must end the stream instead of streaming keep-alives forever."""
    job = store.create(JobSource(filename="meeting.wav", size_bytes=10))

    stream = job_event_stream(job.job_id, store, async_redis, settings)
    snapshot = await anext(stream)
    store.delete(job.job_id)
    closing = await anext(stream)

    assert "event: job" in snapshot
    assert closing == f"event: gone\ndata: {job.job_id}\n\n"

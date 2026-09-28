import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import datetime

from redis.asyncio import Redis as AsyncRedis

from app.core.config import Settings
from app.schemas.job import Job
from app.services.job_store import JobStore, events_channel

logger = logging.getLogger(__name__)


def format_sse(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


async def job_event_stream(
    job_id: str,
    store: JobStore,
    async_redis: AsyncRedis,
    settings: Settings,
) -> AsyncIterator[str]:
    """SSE stream of job updates.

    Pub/sub delivers updates immediately; a slow poll covers messages published while the
    client was reconnecting. The stream closes once the job reaches a terminal state.
    """
    job = store.require(job_id)
    yield format_sse("job", job.model_dump_json())
    if job.is_terminal:
        yield format_sse("done", job.model_dump_json())
        return

    last_seen: datetime = job.updated_at
    pubsub = async_redis.pubsub()
    await pubsub.subscribe(events_channel(job_id))
    idle_seconds = 0.0

    try:
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=settings.sse_poll_seconds,
            )
            current: Job | None = None

            if message is not None:
                current = Job.model_validate_json(message["data"])
            else:
                # Fallback poll — also covers a publish lost during reconnection.
                current = store.get(job_id)

            if current is not None and current.updated_at > last_seen:
                last_seen = current.updated_at
                idle_seconds = 0.0
                yield format_sse("job", current.model_dump_json())
                if current.is_terminal:
                    yield format_sse("done", current.model_dump_json())
                    return
            else:
                idle_seconds += settings.sse_poll_seconds
                if idle_seconds >= settings.sse_keepalive_seconds:
                    idle_seconds = 0.0
                    yield ": keep-alive\n\n"
    except asyncio.CancelledError:
        raise
    finally:
        await pubsub.aclose()

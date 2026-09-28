import logging
import shutil
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from redis.exceptions import RedisError

from app.api.deps import (
    AsyncRedisDep,
    JobStorageDep,
    JobStoreDep,
    RedisDep,
    SettingsDep,
)
from app.core.errors import AppError, ErrorCode
from app.schemas.job import Job, JobCreatedResponse, JobSource, JobStatus
from app.services.events import job_event_stream
from app.services.queue import enqueue_job
from app.services.storage import extract_extension, sanitize_filename, validate_job_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])

UPLOAD_CHUNK_SIZE = 1024 * 1024

# Browsers report very inconsistent types for m4a/ogg, so this is a coarse guard only —
# the extension check above it is the real filter.
ALLOWED_CONTENT_TYPE_PREFIXES = ("audio/", "video/", "application/octet-stream")


def _validate_content_type(content_type: str | None) -> None:
    if not content_type:
        return
    main_type = content_type.split(";")[0].strip().lower()
    if not main_type.startswith(ALLOWED_CONTENT_TYPE_PREFIXES):
        raise AppError(
            ErrorCode.UNSUPPORTED_FORMAT,
            f"Unsupported content type: {main_type}",
        )


async def _upload_chunks(upload: UploadFile):
    while chunk := await upload.read(UPLOAD_CHUNK_SIZE):
        yield chunk


@router.post("", response_model=JobCreatedResponse, status_code=201)
async def create_job(
    settings: SettingsDep,
    redis: RedisDep,
    store: JobStoreDep,
    storage: JobStorageDep,
    file: Annotated[UploadFile, File()],
    speaker_count: Annotated[int | None, Form(ge=1, le=20)] = None,
) -> JobCreatedResponse:
    filename = sanitize_filename(file.filename)
    extension = extract_extension(filename, settings.allowed_extensions)
    _validate_content_type(file.content_type)

    job = store.create(
        JobSource(filename=filename, size_bytes=0, content_type=file.content_type),
        speaker_count=speaker_count,
    )

    try:
        storage.prepare_job_dirs(job.job_id)
        size = await storage.save_stream(
            _upload_chunks(file),
            storage.source_path(job.job_id, extension),
            settings.max_upload_bytes,
        )
        job.source.size_bytes = size
        store.replace(job)
        enqueue_job(redis, settings, job.job_id)
    except RedisError:
        logger.exception("failed to enqueue job", extra={"job_id": job.job_id})
        _discard(store, storage, job.job_id)
        raise HTTPException(status_code=503, detail="Job queue is unavailable") from None
    except BaseException:
        _discard(store, storage, job.job_id)
        raise

    logger.info(
        "job created",
        extra={"job_id": job.job_id, "size_bytes": job.source.size_bytes},
    )
    return JobCreatedResponse(job_id=job.job_id, status=JobStatus.QUEUED)


def _discard(store: JobStoreDep, storage: JobStorageDep, job_id: str) -> None:
    """Remove a job that never made it into the queue — no orphan records or files."""
    shutil.rmtree(storage.job_dir(job_id), ignore_errors=True)
    try:
        store.delete(job_id)
    except RedisError:
        logger.warning("could not delete job record", extra={"job_id": job_id})


@router.get("/{job_id}", response_model=Job)
def get_job(job_id: str, store: JobStoreDep) -> Job:
    return store.require(validate_job_id(job_id))


@router.get("/{job_id}/events")
async def get_job_events(
    job_id: str,
    store: JobStoreDep,
    async_redis: AsyncRedisDep,
    settings: SettingsDep,
) -> StreamingResponse:
    job_id = validate_job_id(job_id)
    store.require(job_id)  # fail fast with 404 before the stream opens
    return StreamingResponse(
        job_event_stream(job_id, store, async_redis, settings),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

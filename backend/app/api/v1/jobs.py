import logging
import shutil
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    Body,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import Field, TypeAdapter, ValidationError
from redis.exceptions import RedisError
from starlette.requests import ClientDisconnect

from app.api.deps import (
    AsyncRedisDep,
    JobStorageDep,
    JobStoreDep,
    RedisDep,
    SettingsDep,
)
from app.core.errors import AppError, ErrorCode
from app.schemas.job import Job, JobCreatedResponse, JobSource, JobStatus
from app.schemas.transcript import SpeakerNames, Transcript
from app.services.cleanup import delete_job
from app.services.events import job_event_stream
from app.services.media import content_disposition, media_type_for, range_response
from app.services.queue import enqueue_job
from app.services.results import read_transcript, rename_speakers, result_file
from app.services.storage import extract_extension, sanitize_filename, validate_job_id
from app.services.upload import (
    FieldError,
    FileHeaders,
    MalformedUpload,
    missing_file,
    receive_form,
    reject_oversized,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])

# The same rule `Form(ge=1, le=20)` enforced when FastAPI parsed the form itself.
SPEAKER_COUNT = TypeAdapter(Annotated[int, Field(ge=1, le=20)])

# The endpoint reads the body itself (T13.7), so FastAPI cannot infer the form for /docs.
CREATE_JOB_OPENAPI = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {
                        "file": {"type": "string", "format": "binary"},
                        "speaker_count": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 20,
                            "description": "Exact number of speakers; omit to detect it.",
                        },
                    },
                }
            }
        },
    }
}

# JSON is canonical; the rest are views generated from it (SPEC.md §6).
DOWNLOAD_FORMATS: tuple[str, ...] = ("json", "txt", "docx", "pdf")

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


def _speaker_count(raw: str | None) -> int | None:
    """Absent or empty means "detect it", as with the former `Form(ge=1, le=20)` field."""
    if raw is None or raw == "":
        return None
    try:
        return SPEAKER_COUNT.validate_python(raw)
    except ValidationError as exc:
        raise RequestValidationError(
            [{**error, "loc": ("body", "speaker_count")} for error in exc.errors()]
        ) from None


@router.post(
    "",
    response_model=JobCreatedResponse,
    status_code=201,
    responses={
        400: {"description": "Unsupported format, or not valid multipart/form-data"},
        413: {"description": "FILE_TOO_LARGE"},
        422: {"description": "The `file` part is missing or `speaker_count` is invalid"},
    },
    openapi_extra=CREATE_JOB_OPENAPI,
)
async def create_job(
    request: Request,
    settings: SettingsDep,
    redis: RedisDep,
    store: JobStoreDep,
    storage: JobStorageDep,
) -> JobCreatedResponse | Response:
    """multipart/form-data with `file` and an optional `speaker_count` (SPEC.md).

    The body is parsed here rather than through `UploadFile`, so the recording is written
    once, straight into the job folder, and an upload declared too large is refused before
    any of it is read (T13.7).
    """
    reject_oversized(request.headers.get("content-length"), settings.max_upload_bytes)
    job: Job | None = None

    def start_job(headers: FileHeaders) -> Path:
        # Called once the file part's headers arrive, before any of its bytes.
        nonlocal job
        filename = sanitize_filename(headers.filename)
        extension = extract_extension(filename, settings.allowed_extensions)
        _validate_content_type(headers.content_type)
        job = store.create(
            JobSource(filename=filename, size_bytes=0, content_type=headers.content_type)
        )
        storage.prepare_job_dirs(job.job_id)
        return storage.source_path(job.job_id, extension)

    try:
        form = await receive_form(
            request.stream(),
            request.headers.get("content-type"),
            max_file_bytes=settings.max_upload_bytes,
            open_file=start_job,
        )
        if job is None:
            raise missing_file()
        # Read only now: the browser sends this field after the file.
        job.speaker_count = _speaker_count(form.fields.get("speaker_count"))
        job.source.size_bytes = form.file_size
        store.replace(job)
        enqueue_job(redis, settings, job.job_id)
    except RedisError:
        logger.exception("failed to enqueue job", extra={"job_id": job and job.job_id})
        if job is not None:
            _discard(store, storage, job.job_id)
        raise HTTPException(status_code=503, detail="Job queue is unavailable") from None
    except ClientDisconnect:
        # The user closed the page mid-upload: nothing to answer, nothing to keep.
        logger.info("upload aborted by the client", extra={"job_id": job and job.job_id})
        if job is not None:
            _discard(store, storage, job.job_id)
        return Response(status_code=400)
    except MalformedUpload as exc:
        if job is not None:
            _discard(store, storage, job.job_id)
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except FieldError as exc:
        if job is not None:
            _discard(store, storage, job.job_id)
        raise RequestValidationError([exc.error]) from None
    except BaseException:
        if job is not None:
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


@router.get("", response_model=list[Job])
def list_jobs(
    store: JobStoreDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[Job]:
    """T11.1 — every job, newest first, so a result stays reachable after a page reload."""
    return store.list_jobs(limit)


@router.get("/{job_id}", response_model=Job)
def get_job(job_id: str, store: JobStoreDep) -> Job:
    return store.require(validate_job_id(job_id))


@router.delete("/{job_id}", status_code=204)
def remove_job(
    job_id: str,
    redis: RedisDep,
    store: JobStoreDep,
    storage: JobStorageDep,
) -> Response:
    """T8.3 — manual cleanup: the job record, its queue entry and its files all go away."""
    delete_job(redis, store, storage, validate_job_id(job_id))
    return Response(status_code=204)


@router.get("/{job_id}/transcript", response_model=Transcript)
def get_transcript(job_id: str, store: JobStoreDep, storage: JobStorageDep) -> Transcript:
    """T7.1 — the canonical result the viewer renders."""
    job_id = validate_job_id(job_id)
    store.require(job_id)
    return read_transcript(storage, job_id)


@router.patch("/{job_id}/speakers", response_model=Transcript)
def patch_speakers(
    job_id: str,
    store: JobStoreDep,
    storage: JobStorageDep,
    settings: SettingsDep,
    names: Annotated[SpeakerNames, Body(examples=[{"SPEAKER_00": "Анна"}])],
) -> Transcript:
    """T7.4 — rename speakers and regenerate the exports. Never re-runs the models."""
    job_id = validate_job_id(job_id)
    store.require(job_id)
    return rename_speakers(storage, job_id, dict(names.root), settings.pdf_font_path)


@router.get("/{job_id}/download/{export_format}")
def download_result(
    job_id: str,
    export_format: str,
    store: JobStoreDep,
    storage: JobStorageDep,
) -> FileResponse:
    """T7.5 — hand over one of the generated views."""
    job_id = validate_job_id(job_id)
    job = store.require(job_id)
    if export_format not in DOWNLOAD_FORMATS:
        raise AppError(
            ErrorCode.UNSUPPORTED_FORMAT,
            f"Unknown export format. Available: {', '.join(DOWNLOAD_FORMATS)}",
        )

    path = result_file(storage, job_id, f"transcript.{export_format}")
    return FileResponse(
        path,
        media_type=media_type_for(path),
        headers={
            "Content-Disposition": content_disposition(
                f"{Path(job.source.filename).stem}.{export_format}"
            )
        },
    )


@router.get("/{job_id}/audio")
def get_job_audio(
    job_id: str,
    request: Request,
    store: JobStoreDep,
    storage: JobStorageDep,
) -> Response:
    """T7.3 — the original recording, with Range support so the player can seek."""
    job_id = validate_job_id(job_id)
    job = store.require(job_id)

    source = storage.find_source(job_id)
    if source is None:
        raise AppError(
            ErrorCode.RESULT_NOT_READY,
            "The source recording is no longer on disk",
            status_code=409,
        )
    return range_response(source, request.headers.get("range"), job.source.filename)


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

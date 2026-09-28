from enum import StrEnum

from fastapi import Request
from fastapi.responses import JSONResponse


class ErrorCode(StrEnum):
    """Stable error codes from SPEC.md §12. Never renamed — the UI maps them to messages."""

    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    FFMPEG_FAILED = "FFMPEG_FAILED"
    MODEL_NOT_AVAILABLE = "MODEL_NOT_AVAILABLE"
    HF_TOKEN_REQUIRED = "HF_TOKEN_REQUIRED"
    DIARIZATION_FAILED = "DIARIZATION_FAILED"
    ASR_FAILED = "ASR_FAILED"
    EXPORT_FAILED = "EXPORT_FAILED"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    # The job exists but has not produced a result yet — distinct from "no such job",
    # because the UI keeps showing progress instead of an error.
    RESULT_NOT_READY = "RESULT_NOT_READY"


class AppError(Exception):
    """Error with a user-safe message. Tracebacks stay in the logs, never in the response."""

    def __init__(self, code: ErrorCode, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


async def app_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error_code": exc.code.value, "message": exc.message},
    )

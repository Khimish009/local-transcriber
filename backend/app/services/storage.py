import re
import unicodedata
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from app.core.errors import AppError, ErrorCode

_UNSAFE_CHARS = re.compile(r"[^\w.\- ]+", re.UNICODE)
_COLLAPSE_SEPARATORS = re.compile(r"[\s_]+")
MAX_FILENAME_LENGTH = 120


def sanitize_filename(filename: str | None) -> str:
    """Strip directories and unsafe characters. Never returns an empty name."""
    candidate = (filename or "").replace("\\", "/").split("/")[-1]
    candidate = unicodedata.normalize("NFKC", candidate).strip()
    candidate = _UNSAFE_CHARS.sub("", candidate)
    candidate = _COLLAPSE_SEPARATORS.sub("_", candidate).strip("._ ")
    if not candidate:
        return "upload"
    return candidate[:MAX_FILENAME_LENGTH]


def extract_extension(filename: str, allowed: list[str]) -> str:
    """Return the lowercase extension without a dot, or raise UNSUPPORTED_FORMAT."""
    suffix = Path(filename).suffix.lstrip(".").lower()
    if not suffix or suffix not in allowed:
        raise AppError(
            ErrorCode.UNSUPPORTED_FORMAT,
            f"Unsupported file format. Allowed: {', '.join(sorted(allowed))}",
        )
    return suffix


def validate_job_id(job_id: str) -> str:
    """Job ids are UUIDs — this is also the guard against path traversal."""
    try:
        return str(uuid.UUID(job_id))
    except ValueError as exc:
        raise AppError(ErrorCode.JOB_NOT_FOUND, "Job not found", status_code=404) from exc


class JobStorage:
    """Owns the on-disk layout described in SPEC.md §8."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir

    def job_dir(self, job_id: str) -> Path:
        return self._data_dir / "jobs" / validate_job_id(job_id)

    def source_dir(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "source"

    def work_dir(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "work"

    def result_dir(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "result"

    def prepare_job_dirs(self, job_id: str) -> None:
        for directory in (
            self.source_dir(job_id),
            self.work_dir(job_id),
            self.result_dir(job_id),
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def normalized_path(self, job_id: str) -> Path:
        return self.work_dir(job_id) / "normalized.wav"

    def diarization_path(self, job_id: str) -> Path:
        return self.work_dir(job_id) / "diarization.json"

    def asr_words_path(self, job_id: str) -> Path:
        return self.work_dir(job_id) / "asr_words.json"

    def transcript_path(self, job_id: str) -> Path:
        """The canonical result — TXT/DOCX/PDF are generated from this file."""
        return self.result_dir(job_id) / "transcript.json"

    def source_path(self, job_id: str, extension: str) -> Path:
        return self.source_dir(job_id) / f"original.{extension}"

    def find_source(self, job_id: str) -> Path | None:
        source_dir = self.source_dir(job_id)
        if not source_dir.is_dir():
            return None
        return next((path for path in sorted(source_dir.iterdir()) if path.is_file()), None)

    async def save_stream(
        self,
        chunks: AsyncIterator[bytes],
        destination: Path,
        max_bytes: int,
    ) -> int:
        """Stream an upload to disk without buffering it in memory.

        Raises FILE_TOO_LARGE as soon as the limit is exceeded and removes the partial file.
        """
        destination.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        try:
            with destination.open("wb") as handle:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > max_bytes:
                        raise AppError(
                            ErrorCode.FILE_TOO_LARGE,
                            f"File exceeds the {max_bytes // (1024 * 1024)} MB limit",
                            status_code=413,
                        )
                    handle.write(chunk)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        return written

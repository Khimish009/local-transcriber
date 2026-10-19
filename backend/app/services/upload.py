"""Multipart upload streamed straight into the job folder (TASKS.md T13.7).

FastAPI fills an `UploadFile` before the endpoint runs: Starlette spools the whole body
into a temporary file inside the container, and only then can the endpoint copy it to
/data. A 2 GB recording was written to disk twice, and FILE_TOO_LARGE could only be raised
once all of it had arrived.

Here the body is parsed as it arrives, with python-multipart (the parser Starlette itself
uses), and the file goes to its final place in a single pass. The request keeps its shape
— multipart/form-data with a `file` part and plain fields — so the API contract does not
change.
"""

import contextlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from python_multipart.exceptions import FormParserError
from python_multipart.multipart import MultipartParser, parse_options_header
from starlette.concurrency import run_in_threadpool

from app.core.errors import AppError, ErrorCode

FILE_FIELD = "file"
# Boundaries and part headers on top of the file itself. A declared body longer than the
# limit plus this cannot hold a file within the limit.
MULTIPART_OVERHEAD_BYTES = 64 * 1024
# Plain fields are tiny (`speaker_count`); a longer one is not a legitimate request.
MAX_FIELD_BYTES = 1024
# File data is written in pieces of about this size from a worker thread, so disk I/O never
# blocks the event loop that also serves the progress streams.
WRITE_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class FileHeaders:
    """What the client says about the file, known before any of its bytes arrive."""

    filename: str | None
    content_type: str | None


@dataclass(frozen=True)
class ReceivedForm:
    fields: dict[str, str]
    file_size: int


class MalformedUpload(Exception):
    """The body is not valid multipart/form-data."""


class FieldError(Exception):
    """A well-formed body that is not the form the endpoint expects.

    `error` is shaped like a FastAPI validation error, so the response matches the 422 the
    endpoint gave when FastAPI parsed the form itself.
    """

    def __init__(self, error: dict[str, Any]) -> None:
        super().__init__(error["msg"])
        self.error = error


def missing_file() -> FieldError:
    return FieldError(
        {"type": "missing", "loc": ("body", FILE_FIELD), "msg": "Field required", "input": None}
    )


def file_too_large(max_file_bytes: int) -> AppError:
    return AppError(
        ErrorCode.FILE_TOO_LARGE,
        f"File exceeds the {max_file_bytes // (1024 * 1024)} MB limit",
        status_code=413,
    )


def reject_oversized(content_length: str | None, max_file_bytes: int) -> None:
    """Refuse before reading the body when its declared length already exceeds the limit."""
    try:
        declared = int(content_length) if content_length else None
    except ValueError:
        return  # not ours to judge; the server rejects a malformed header itself
    if declared is not None and declared > max_file_bytes + MULTIPART_OVERHEAD_BYTES:
        raise file_too_large(max_file_bytes)


def _decode(raw: bytes | bytearray) -> str:
    # Browsers send UTF-8 names; latin-1 never fails, as in Starlette's own form parser.
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


class _Part:
    def __init__(self) -> None:
        self.name = ""
        self.filename: str | None = None
        self.content_type: str | None = None
        self.headers: dict[bytes, bytes] = {}
        self.data = bytearray()


class _PartReader:
    """Collects python-multipart callbacks as events.

    The callbacks are synchronous, so they only record what happened; the async loop in
    `receive_form` acts on the events between chunks, where it can await disk writes.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, _Part, bytes]] = []
        self._part = _Part()
        self._header_name = b""
        self._header_value = b""

    def callbacks(self) -> dict[str, Callable[..., None]]:
        return {
            "on_part_begin": self._on_part_begin,
            "on_header_field": self._on_header_field,
            "on_header_value": self._on_header_value,
            "on_header_end": self._on_header_end,
            "on_headers_finished": self._on_headers_finished,
            "on_part_data": self._on_part_data,
            "on_part_end": self._on_part_end,
        }

    def drain(self) -> list[tuple[str, _Part, bytes]]:
        events, self.events = self.events, []
        return events

    def _on_part_begin(self) -> None:
        self._part = _Part()

    def _on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._header_name += data[start:end]

    def _on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._header_value += data[start:end]

    def _on_header_end(self) -> None:
        self._part.headers[self._header_name.lower()] = self._header_value
        self._header_name = b""
        self._header_value = b""

    def _on_headers_finished(self) -> None:
        part = self._part
        _, options = parse_options_header(part.headers.get(b"content-disposition"))
        if b"name" not in options:
            raise MalformedUpload('A part has no "name" in its Content-Disposition')
        part.name = _decode(options[b"name"])
        if b"filename" in options:
            part.filename = _decode(options[b"filename"])
        content_type = part.headers.get(b"content-type")
        part.content_type = _decode(content_type) if content_type else None
        self.events.append(("headers", part, b""))

    def _on_part_data(self, data: bytes, start: int, end: int) -> None:
        self.events.append(("data", self._part, data[start:end]))

    def _on_part_end(self) -> None:
        self.events.append(("end", self._part, b""))


async def receive_form(
    chunks: AsyncIterator[bytes],
    content_type: str | None,
    *,
    max_file_bytes: int,
    open_file: Callable[[FileHeaders], Path],
) -> ReceivedForm:
    """Parse a multipart body as it arrives, writing the `file` part where `open_file` says.

    `open_file` is called once, when the file part's headers arrive and before any of its
    bytes are read: it validates them and returns the destination path. Raises
    FILE_TOO_LARGE as soon as the file outgrows `max_file_bytes`, `MalformedUpload` for a
    body that is not valid multipart, `FieldError` for a missing or duplicated file. Other
    file parts are ignored, as FastAPI ignored unknown fields. The partial file is removed
    on any failure, whatever raised it.
    """
    media_type, params = parse_options_header(content_type)
    if media_type.lower() != b"multipart/form-data":
        raise missing_file()
    boundary = params.get(b"boundary")
    if not boundary:
        raise MalformedUpload("Missing boundary in multipart/form-data")

    reader = _PartReader()
    try:
        parser = MultipartParser(boundary, reader.callbacks())
    except FormParserError as exc:  # e.g. a boundary longer than RFC 2046 allows
        raise MalformedUpload("Invalid multipart boundary") from exc
    fields: dict[str, str] = {}
    destination: Path | None = None
    handle = None
    pending = bytearray()
    size = 0
    file_complete = False

    async def flush() -> None:
        if pending:
            await run_in_threadpool(handle.write, bytes(pending))
            pending.clear()

    try:
        async for chunk in chunks:
            try:
                parser.write(chunk)
            except FormParserError as exc:
                raise MalformedUpload("Invalid multipart data") from exc

            for kind, part, data in reader.drain():
                if part.filename is None:
                    if kind == "headers" and part.name == FILE_FIELD:
                        raise FieldError(
                            {
                                "type": "value_error",
                                "loc": ("body", FILE_FIELD),
                                "msg": "Expected a file, received a plain field",
                                "input": None,
                            }
                        )
                    part.data += data
                    if len(part.data) > MAX_FIELD_BYTES:
                        raise MalformedUpload(f'Field "{part.name}" is too long')
                    if kind == "end":
                        fields[part.name] = _decode(part.data)
                elif part.name != FILE_FIELD:
                    continue
                elif kind == "headers":
                    if destination is not None:
                        raise FieldError(
                            {
                                "type": "value_error",
                                "loc": ("body", FILE_FIELD),
                                "msg": "Only one file can be uploaded per job",
                                "input": None,
                            }
                        )
                    destination = open_file(FileHeaders(part.filename, part.content_type))
                    handle = destination.open("wb")
                elif kind == "data":
                    size += len(data)
                    if size > max_file_bytes:
                        raise file_too_large(max_file_bytes)
                    pending += data
                    if len(pending) >= WRITE_CHUNK_BYTES:
                        await flush()
                else:
                    await flush()
                    file_complete = True
        parser.finalize()
        if destination is None:
            raise missing_file()
        if not file_complete:
            # python-multipart does not object to a body that stops mid-part.
            raise MalformedUpload("The multipart body ends before the file does")
    except BaseException:
        if handle is not None:
            # A failing close (a full disk) must not hide the original error or keep the
            # partial file.
            with contextlib.suppress(OSError):
                handle.close()
        if destination is not None:
            destination.unlink(missing_ok=True)
        raise

    handle.close()
    return ReceivedForm(fields=fields, file_size=size)

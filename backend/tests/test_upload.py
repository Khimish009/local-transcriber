"""T13.7 — a multipart body parsed as it arrives, the file written once into its final place."""

import asyncio
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from python_multipart.multipart import MAX_BOUNDARY_LENGTH
from starlette.requests import ClientDisconnect

from app.core.errors import AppError, ErrorCode
from app.services.upload import (
    MAX_FIELD_BYTES,
    MULTIPART_OVERHEAD_BYTES,
    WRITE_CHUNK_BYTES,
    FieldError,
    FileHeaders,
    MalformedUpload,
    ReceivedForm,
    receive_form,
    reject_oversized,
)

BOUNDARY = "test-boundary-7f3a9c"
CONTENT_TYPE = f"multipart/form-data; boundary={BOUNDARY}"

LIMIT = 1024 * 1024  # what the `settings` fixture allows (max_upload_mb=1)
# Roomy enough for the tests that are not about the limit.
MAX_FILE_BYTES = 4 * WRITE_CHUNK_BYTES
PIECE = 64 * 1024  # what an ASGI server hands over per read


@dataclass(frozen=True)
class Part:
    name: str
    data: bytes = b""
    filename: str | None = None
    content_type: str | None = None


def multipart_body(*parts: Part, boundary: str = BOUNDARY) -> bytes:
    """A multipart/form-data body laid out as a browser sends it, closing delimiter included."""
    body = bytearray()
    for part in parts:
        disposition = f'form-data; name="{part.name}"'
        if part.filename is not None:
            disposition += f'; filename="{part.filename}"'
        body += f"--{boundary}\r\nContent-Disposition: {disposition}\r\n".encode()
        if part.content_type is not None:
            body += f"Content-Type: {part.content_type}\r\n".encode()
        body += b"\r\n" + part.data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return bytes(body)


async def chunks(body: bytes, size: int | None = None) -> AsyncIterator[bytes]:
    """The body in pieces, then the empty chunk Starlette's `Request.stream()` ends with."""
    step = size or max(len(body), 1)
    for start in range(0, len(body), step):
        yield body[start : start + step]
    yield b""


async def cut_at(body: bytes, position: int) -> AsyncIterator[bytes]:
    """The body in exactly two pieces, split after `position` bytes."""
    yield body[:position]
    yield body[position:]
    yield b""


async def observing(
    stream: AsyncIterator[bytes], path: Path, sizes: list[int]
) -> AsyncIterator[bytes]:
    """Pass `stream` on, noting each time more is asked for how much of `path` is on disk.

    -1 means the file does not exist yet.
    """
    async for piece in stream:
        sizes.append(path.stat().st_size if path.exists() else -1)
        yield piece


class Opener:
    """Stands in for the endpoint's `start_job`: notes what it is told, names the destination."""

    def __init__(self, destination: Path, error: Exception | None = None) -> None:
        self.destination = destination
        self.error = error
        self.calls: list[FileHeaders] = []

    def __call__(self, headers: FileHeaders) -> Path:
        self.calls.append(headers)
        if self.error is not None:
            raise self.error
        return self.destination


@pytest.fixture
def opener(tmp_path: Path) -> Opener:
    return Opener(tmp_path / "original.wav")


async def receive(
    source: bytes | AsyncIterator[bytes],
    opener: Opener,
    *,
    content_type: str | None = CONTENT_TYPE,
    max_file_bytes: int = MAX_FILE_BYTES,
    chunk_size: int | None = None,
) -> ReceivedForm:
    stream = chunks(source, chunk_size) if isinstance(source, bytes) else source
    return await receive_form(stream, content_type, max_file_bytes=max_file_bytes, open_file=opener)


# CRLF and near-misses of the delimiter inside the data: the parser must not cut the file there.
TRICKY_AUDIO = b"RIFF\r\n--" + BOUNDARY.encode()[:-3] + b"xyz\r\n--\r\n\r\nWAVEfmt "
# 2.5 write chunks of pseudo-random bytes: a skipped, repeated or reordered piece shows up.
BIG_AUDIO = random.Random(2026).randbytes(5 * WRITE_CHUNK_BYTES // 2)

MEETING = Part("file", TRICKY_AUDIO, filename="meeting.wav", content_type="audio/wav")
FORM = multipart_body(
    Part("title", "Планёрка".encode()),
    MEETING,
    Part("speaker_count", b"3"),
)
FORM_RESULT = ReceivedForm(
    fields={"title": "Планёрка", "speaker_count": "3"}, file_size=len(TRICKY_AUDIO)
)
MEETING_HEADERS = FileHeaders("meeting.wav", "audio/wav")


# --- the happy path ---------------------------------------------------------------------


async def test_file_and_the_fields_around_it_are_received(opener) -> None:
    result = await receive(FORM, opener)

    assert result == FORM_RESULT
    assert opener.destination.read_bytes() == TRICKY_AUDIO
    assert opener.calls == [MEETING_HEADERS]


@pytest.mark.parametrize("size", [1, 2, 3, 7, 16])
async def test_chunking_does_not_change_the_result(opener, size) -> None:
    result = await receive(FORM, opener, chunk_size=size)

    assert result == FORM_RESULT
    assert opener.destination.read_bytes() == TRICKY_AUDIO
    assert opener.calls == [MEETING_HEADERS]


async def test_the_body_may_be_cut_anywhere_by_the_network(opener) -> None:
    """Part headers, the file and every delimiter can each be split at any byte."""
    for cut in range(1, len(FORM)):
        opener.calls.clear()

        result = await receive(cut_at(FORM, cut), opener)

        assert result == FORM_RESULT, f"cut at byte {cut}"
        assert opener.destination.read_bytes() == TRICKY_AUDIO, f"cut at byte {cut}"
        assert opener.calls == [MEETING_HEADERS], f"cut at byte {cut}"


async def test_empty_chunks_in_the_stream_are_harmless(opener) -> None:
    async def with_gaps() -> AsyncIterator[bytes]:
        async for piece in chunks(FORM, 5):
            yield piece
            yield b""

    assert await receive(with_gaps(), opener) == FORM_RESULT
    assert opener.destination.read_bytes() == TRICKY_AUDIO


@pytest.mark.parametrize("chunk_size", [None, PIECE, 100_003])
async def test_file_larger_than_the_write_chunk_is_written_intact(opener, chunk_size) -> None:
    body = multipart_body(Part("file", BIG_AUDIO, filename="meeting.wav"))

    result = await receive(body, opener, chunk_size=chunk_size)

    assert result.file_size == len(BIG_AUDIO)
    assert opener.destination.read_bytes() == BIG_AUDIO


async def test_file_reaches_the_disk_while_the_body_is_still_arriving(opener) -> None:
    """Memory stays bounded for a multi-gigabyte upload: data is flushed, not collected."""
    body = multipart_body(Part("file", BIG_AUDIO, filename="meeting.wav"))
    sizes: list[int] = []

    await receive(observing(chunks(body, PIECE), opener.destination, sizes), opener)

    assert max(sizes) >= WRITE_CHUNK_BYTES, "nothing was written before the body ended"
    for pulled, on_disk in enumerate(sizes):
        waiting_in_memory = pulled * PIECE - max(on_disk, 0)
        assert waiting_in_memory <= WRITE_CHUNK_BYTES + PIECE, f"after {pulled} pieces"


async def test_empty_file_is_received_as_an_empty_file(opener) -> None:
    body = multipart_body(Part("file", b"", filename="meeting.wav", content_type="audio/wav"))

    result = await receive(body, opener)

    assert result == ReceivedForm(fields={}, file_size=0)
    assert opener.destination.read_bytes() == b""


async def test_file_part_without_a_content_type_is_still_a_file(opener) -> None:
    await receive(multipart_body(Part("file", b"abc", filename="meeting.wav")), opener)

    assert opener.calls == [FileHeaders("meeting.wav", None)]


@pytest.mark.parametrize(
    ("raw_name", "expected"),
    [
        pytest.param("Запись встречи.wav".encode(), "Запись встречи.wav", id="utf8"),
        pytest.param(
            "Запись.wav".encode("cp1251"),
            "Запись.wav".encode("cp1251").decode("latin-1"),
            id="not-utf8-falls-back-to-latin-1",
        ),
        pytest.param(b"report; final.wav", "report; final.wav", id="semicolon-in-quotes"),
    ],
)
async def test_filename_is_decoded_without_failing(opener, raw_name, expected) -> None:
    body = multipart_body(Part("file", b"abc", filename="PLACEHOLDER"))

    await receive(body.replace(b"PLACEHOLDER", raw_name), opener)

    assert opener.calls[0].filename == expected


@pytest.mark.parametrize(
    "content_type",
    [
        f"Multipart/Form-Data; boundary={BOUNDARY}",
        f"multipart/form-data;boundary={BOUNDARY}",
        f"multipart/form-data; BOUNDARY={BOUNDARY}",
        f"multipart/form-data; charset=utf-8; boundary={BOUNDARY}",
        f'multipart/form-data; boundary="{BOUNDARY}"',
    ],
)
async def test_content_type_is_matched_loosely(opener, content_type) -> None:
    result = await receive(FORM, opener, content_type=content_type)

    assert result == FORM_RESULT


@pytest.mark.parametrize(
    "boundary",
    [
        pytest.param("----WebKitFormBoundary7MA4YWxkTrZu0gW", id="chrome-safari"),
        pytest.param("---------------------------314911788813839", id="firefox"),
        pytest.param("x", id="shortest"),
        pytest.param("a" * MAX_BOUNDARY_LENGTH, id="longest-the-parser-takes"),
    ],
)
async def test_boundaries_real_clients_use(opener, boundary) -> None:
    body = multipart_body(MEETING, Part("speaker_count", b"3"), boundary=boundary)

    result = await receive(
        body, opener, content_type=f"multipart/form-data; boundary={boundary}", chunk_size=7
    )

    assert result == ReceivedForm(fields={"speaker_count": "3"}, file_size=len(TRICKY_AUDIO))
    assert opener.destination.read_bytes() == TRICKY_AUDIO


async def test_file_part_under_another_name_is_ignored_before_the_real_file(opener) -> None:
    stray = Part(
        "attachment", b"NOT THE RECORDING", filename="notes.txt", content_type="text/plain"
    )

    result = await receive(multipart_body(stray, MEETING), opener)

    assert opener.calls == [MEETING_HEADERS], "open_file is for the `file` part only"
    assert opener.destination.read_bytes() == TRICKY_AUDIO
    assert result == ReceivedForm(fields={}, file_size=len(TRICKY_AUDIO))


async def test_file_part_under_another_name_is_ignored_after_the_real_file(opener) -> None:
    stray = Part(
        "attachment", b"NOT THE RECORDING", filename="notes.txt", content_type="text/plain"
    )

    result = await receive(multipart_body(MEETING, stray), opener, chunk_size=11)

    assert opener.calls == [MEETING_HEADERS]
    assert opener.destination.read_bytes() == TRICKY_AUDIO
    assert result == ReceivedForm(fields={}, file_size=len(TRICKY_AUDIO))


# --- the size limit ---------------------------------------------------------------------


async def test_file_over_the_limit_is_cut_off_and_the_partial_removed(opener) -> None:
    limit = WRITE_CHUNK_BYTES + 256 * 1024
    body = multipart_body(Part("file", BIG_AUDIO, filename="meeting.wav"))
    sizes: list[int] = []

    with pytest.raises(AppError) as exc:
        await receive(
            observing(chunks(body, PIECE), opener.destination, sizes),
            opener,
            max_file_bytes=limit,
        )

    assert exc.value.code is ErrorCode.FILE_TOO_LARGE
    assert exc.value.status_code == 413
    assert max(sizes) >= WRITE_CHUNK_BYTES // 2, "there was a real partial file to clean up"
    assert not opener.destination.exists()
    assert len(sizes) < len(body) // PIECE, "the rest of the body must not be read"


async def test_file_exactly_at_the_limit_is_accepted(opener) -> None:
    body = multipart_body(Part("file", b"x" * 1000, filename="meeting.wav"))

    result = await receive(body, opener, max_file_bytes=1000)

    assert result.file_size == 1000
    assert len(opener.destination.read_bytes()) == 1000


async def test_one_byte_over_the_limit_is_refused(opener) -> None:
    body = multipart_body(Part("file", b"x" * 1001, filename="meeting.wav"))

    with pytest.raises(AppError) as exc:
        await receive(body, opener, max_file_bytes=1000)

    assert exc.value.code is ErrorCode.FILE_TOO_LARGE
    assert not opener.destination.exists()


def test_reject_oversized_refuses_a_length_beyond_the_allowance() -> None:
    with pytest.raises(AppError) as exc:
        reject_oversized(str(LIMIT + MULTIPART_OVERHEAD_BYTES + 1), LIMIT)

    assert exc.value.code is ErrorCode.FILE_TOO_LARGE
    assert exc.value.status_code == 413
    assert "1 MB" in exc.value.message


def test_reject_oversized_refuses_an_absurd_length() -> None:
    with pytest.raises(AppError) as exc:
        reject_oversized(str(10**20), LIMIT)

    assert exc.value.code is ErrorCode.FILE_TOO_LARGE


@pytest.mark.parametrize(
    "declared",
    [
        pytest.param(str(LIMIT + MULTIPART_OVERHEAD_BYTES), id="at-the-allowance"),
        pytest.param(str(LIMIT), id="at-the-limit"),
        pytest.param("0", id="zero"),
        pytest.param("-1", id="negative"),
        pytest.param(None, id="absent"),
        pytest.param("", id="empty"),
        pytest.param("abc", id="not-a-number"),
        pytest.param("1.5", id="not-an-integer"),
    ],
)
def test_reject_oversized_lets_the_rest_through(declared) -> None:
    """A missing or unreadable header is not ours to judge; the byte count decides later."""
    reject_oversized(declared, LIMIT)


# --- a body that is not the form we expect -----------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(multipart_body(Part("speaker_count", b"2")), id="only-plain-fields"),
        pytest.param(multipart_body(), id="no-parts-at-all"),
        pytest.param(b"", id="empty-body"),
    ],
)
async def test_form_without_a_file_part_is_missing_the_file(opener, body) -> None:
    with pytest.raises(FieldError) as exc:
        await receive(body, opener)

    assert exc.value.error == {
        "type": "missing",
        "loc": ("body", "file"),
        "msg": "Field required",
        "input": None,
    }
    assert opener.calls == []
    assert not opener.destination.exists()


@pytest.mark.parametrize(
    "content_type",
    [
        None,
        "",
        "application/json",
        "application/x-www-form-urlencoded",
        "text/plain",
        "multipart/mixed; boundary=x",
    ],
)
async def test_body_that_is_not_multipart_form_data_has_no_file(opener, content_type) -> None:
    with pytest.raises(FieldError) as exc:
        await receive(b'{"file": "meeting.wav"}', opener, content_type=content_type)

    assert exc.value.error["type"] == "missing"
    assert exc.value.error["loc"] == ("body", "file")
    assert opener.calls == []


async def test_file_sent_as_a_plain_field_is_rejected(opener) -> None:
    with pytest.raises(FieldError) as exc:
        await receive(multipart_body(Part("file", b"just text")), opener)

    assert exc.value.error["loc"] == ("body", "file")
    # The field was sent, only not as a file: "Field required" would point the client elsewhere.
    assert exc.value.error["type"] != "missing"
    assert opener.calls == []
    assert not opener.destination.exists()


async def test_second_file_part_is_rejected_and_the_first_removed(opener) -> None:
    second = Part("file", b"another recording", filename="other.wav", content_type="audio/wav")
    body = multipart_body(MEETING, second)
    sizes: list[int] = []

    with pytest.raises(FieldError) as exc:
        await receive(observing(chunks(body, 16), opener.destination, sizes), opener)

    assert exc.value.error["loc"] == ("body", "file")
    assert opener.calls == [MEETING_HEADERS], "the second file never got a destination"
    assert max(sizes) >= 0, "the first file was on disk when the second showed up"
    assert not opener.destination.exists()


@pytest.mark.parametrize("content_type", ["multipart/form-data", "multipart/form-data; charset=x"])
async def test_missing_boundary_is_malformed(opener, content_type) -> None:
    with pytest.raises(MalformedUpload):
        await receive(FORM, opener, content_type=content_type)

    assert opener.calls == []


async def test_empty_boundary_is_malformed(opener) -> None:
    with pytest.raises(MalformedUpload):
        await receive(FORM, opener, content_type="multipart/form-data; boundary=")


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"this is not multipart at all", id="plain-text"),
        pytest.param(bytes(range(256)), id="binary"),
        pytest.param(multipart_body(MEETING, boundary="another-boundary"), id="other-boundary"),
        pytest.param(f"--{BOUNDARY}junk\r\n".encode() + b"x" * 50, id="junk-after-delimiter"),
        pytest.param(f"--{BOUNDARY}\r\nnot a header\r\n\r\ndata".encode(), id="header-no-colon"),
        pytest.param(
            f"--{BOUNDARY}\nContent-Disposition: form-data; name=file\n\ndata".encode(),
            id="bare-newlines",
        ),
    ],
)
async def test_garbage_body_is_malformed(opener, body) -> None:
    with pytest.raises(MalformedUpload):
        await receive(body, opener)

    assert opener.calls == []


@pytest.mark.parametrize(
    "disposition",
    [
        pytest.param(b"Content-Disposition: form-data\r\n", id="no-name"),
        pytest.param(b'Content-Disposition: form-data; filename="a.wav"\r\n', id="filename-only"),
        pytest.param(b"Content-Type: text/plain\r\n", id="no-disposition"),
    ],
)
async def test_part_without_a_name_is_malformed(opener, disposition) -> None:
    delimiter = f"--{BOUNDARY}".encode()
    body = delimiter + b"\r\n" + disposition + b"\r\nx\r\n" + delimiter + b"--\r\n"

    with pytest.raises(MalformedUpload):
        await receive(body, opener)


BIG_FORM = multipart_body(Part("file", BIG_AUDIO, filename="meeting.wav"))
CLOSING_DELIMITER = f"\r\n--{BOUNDARY}--\r\n".encode()


@pytest.mark.parametrize(
    "truncated",
    [
        pytest.param(BIG_FORM[: len(BIG_FORM) * 7 // 10], id="inside-the-file"),
        pytest.param(BIG_FORM[: -len(CLOSING_DELIMITER)], id="before-the-closing-delimiter"),
        pytest.param(BIG_FORM[: -len(CLOSING_DELIMITER) + 8], id="inside-the-closing-delimiter"),
    ],
)
async def test_body_truncated_inside_the_file_is_malformed_and_the_partial_removed(
    opener, truncated
) -> None:
    sizes: list[int] = []

    with pytest.raises(MalformedUpload):
        await receive(observing(chunks(truncated, PIECE), opener.destination, sizes), opener)

    assert max(sizes) >= WRITE_CHUNK_BYTES // 2, "there was a real partial file to clean up"
    assert not opener.destination.exists()


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(ClientDisconnect, id="client-disconnect"),
        pytest.param(OSError, id="read-error"),
        pytest.param(asyncio.CancelledError, id="cancelled"),
    ],
)
async def test_failure_mid_file_propagates_and_the_partial_is_removed(opener, error) -> None:
    body = multipart_body(Part("file", BIG_AUDIO, filename="meeting.wav"))
    on_disk: list[int] = []

    async def dropped_midway() -> AsyncIterator[bytes]:
        yield body[: WRITE_CHUNK_BYTES + 100_000]
        on_disk.append(opener.destination.stat().st_size)
        raise error

    with pytest.raises(error):
        await receive(dropped_midway(), opener)

    assert on_disk[0] >= WRITE_CHUNK_BYTES, "there was a real partial file to clean up"
    assert not opener.destination.exists()


# --- plain fields -----------------------------------------------------------------------


async def test_plain_field_longer_than_the_cap_is_malformed(opener) -> None:
    body = multipart_body(Part("speaker_count", b"1" * (MAX_FIELD_BYTES + 1)), MEETING)

    with pytest.raises(MalformedUpload):
        await receive(body, opener)

    assert opener.calls == []


async def test_plain_field_exactly_at_the_cap_is_accepted(opener) -> None:
    value = b"1" * MAX_FIELD_BYTES
    body = multipart_body(Part("note", value), MEETING)

    result = await receive(body, opener)

    assert result.fields == {"note": value.decode()}


async def test_oversized_plain_field_after_the_file_removes_the_file(opener) -> None:
    body = multipart_body(MEETING, Part("speaker_count", b"1" * (MAX_FIELD_BYTES + 1)))
    sizes: list[int] = []

    with pytest.raises(MalformedUpload):
        await receive(observing(chunks(body, 16), opener.destination, sizes), opener)

    assert max(sizes) >= 0, "the file was already on disk when the field arrived"
    assert not opener.destination.exists()


async def test_oversized_plain_field_is_refused_without_reading_it_all(opener) -> None:
    """A field is held in memory, so the cap must apply as the data arrives."""
    body = multipart_body(Part("note", b"1" * 100 * MAX_FIELD_BYTES), MEETING)
    pulled = 0

    async def counting() -> AsyncIterator[bytes]:
        nonlocal pulled
        async for piece in chunks(body, MAX_FIELD_BYTES):
            pulled += 1
            yield piece

    with pytest.raises(MalformedUpload):
        await receive(counting(), opener)

    assert pulled <= 3


# --- open_file refuses the file -----------------------------------------------------------


async def test_refusal_from_open_file_propagates_before_the_file_is_read(tmp_path) -> None:
    refusal = AppError(ErrorCode.UNSUPPORTED_FORMAT, "Unsupported file format")
    opener = Opener(tmp_path / "original.txt", error=refusal)
    body = multipart_body(
        Part("file", b"y" * 100_000, filename="notes.txt", content_type="text/plain")
    )
    pulled = 0

    async def counting() -> AsyncIterator[bytes]:
        nonlocal pulled
        async for piece in chunks(body, 1024):
            pulled += 1
            yield piece

    with pytest.raises(AppError) as exc:
        await receive(counting(), opener)

    assert exc.value is refusal
    assert list(tmp_path.iterdir()) == [], "nothing may be written for a refused file"
    assert pulled <= 2, "the file's bytes must not be read once open_file said no"


# --- failures around the parser itself ----------------------------------------------------


async def test_overlong_boundary_is_a_malformed_upload(opener) -> None:
    boundary = "b" * (MAX_BOUNDARY_LENGTH + 1)
    body = multipart_body(MEETING, boundary=boundary)

    with pytest.raises(MalformedUpload):
        await receive(body, opener, content_type=f"multipart/form-data; boundary={boundary}")


class _HandleThatFailsToClose:
    def __init__(self, inner) -> None:
        self._inner = inner

    def write(self, data: bytes) -> int:
        return self._inner.write(data)

    def close(self) -> None:
        self._inner.close()
        raise OSError(28, "No space left on device")


class _PathWithFailingClose(type(Path())):
    def open(self, *args, **kwargs):
        return _HandleThatFailsToClose(super().open(*args, **kwargs))


async def test_failing_close_neither_hides_the_error_nor_keeps_the_partial(tmp_path) -> None:
    """A full disk at close must not replace the real cause or leave half a recording."""
    destination = _PathWithFailingClose(tmp_path / "original.wav")
    body = multipart_body(MEETING)
    truncated = body[: body.index(MEETING.data) + len(MEETING.data) // 2]

    with pytest.raises(MalformedUpload):
        await receive(truncated, Opener(destination))

    assert not destination.exists()

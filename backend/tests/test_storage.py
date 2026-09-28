import pytest

from app.core.errors import AppError, ErrorCode
from app.services.storage import (
    extract_extension,
    sanitize_filename,
    validate_job_id,
)

ALLOWED = ["wav", "mp3", "m4a"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("meeting.m4a", "meeting.m4a"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\a\\запись 2.mp3", "запись_2.mp3"),
        ("   .hidden.wav  ", "hidden.wav"),
        ("", "upload"),
        (None, "upload"),
        ("a/b/../c.wav", "c.wav"),
    ],
)
def test_sanitize_filename(raw, expected) -> None:
    assert sanitize_filename(raw) == expected


def test_sanitize_filename_truncates_long_names() -> None:
    assert len(sanitize_filename("x" * 500 + ".wav")) == 120


def test_extract_extension_accepts_allowed() -> None:
    assert extract_extension("meeting.M4A", ALLOWED) == "m4a"


@pytest.mark.parametrize("filename", ["notes.txt", "noextension", "archive.zip"])
def test_extract_extension_rejects_others(filename) -> None:
    with pytest.raises(AppError) as exc:
        extract_extension(filename, ALLOWED)
    assert exc.value.code is ErrorCode.UNSUPPORTED_FORMAT


def test_validate_job_id_rejects_path_traversal() -> None:
    with pytest.raises(AppError) as exc:
        validate_job_id("../../etc")
    assert exc.value.code is ErrorCode.JOB_NOT_FOUND
    assert exc.value.status_code == 404


async def _chunks(*parts: bytes):
    for part in parts:
        yield part


@pytest.mark.asyncio
async def test_save_stream_writes_file(storage, tmp_path) -> None:
    destination = tmp_path / "out" / "original.wav"

    size = await storage.save_stream(_chunks(b"abc", b"de"), destination, max_bytes=100)

    assert size == 5
    assert destination.read_bytes() == b"abcde"


@pytest.mark.asyncio
async def test_save_stream_enforces_limit_and_removes_partial(storage, tmp_path) -> None:
    destination = tmp_path / "out" / "original.wav"

    with pytest.raises(AppError) as exc:
        await storage.save_stream(_chunks(b"a" * 10, b"b" * 10), destination, max_bytes=15)

    assert exc.value.code is ErrorCode.FILE_TOO_LARGE
    assert not destination.exists()


def test_job_dirs_layout(storage) -> None:
    job_id = "11111111-1111-1111-1111-111111111111"

    storage.prepare_job_dirs(job_id)

    assert storage.source_dir(job_id).is_dir()
    assert storage.work_dir(job_id).is_dir()
    assert storage.result_dir(job_id).is_dir()
    assert storage.source_path(job_id, "wav").name == "original.wav"

"""T7.3 — HTTP Range parsing. Seeking a long recording depends on it."""

from pathlib import Path

import pytest

from app.services.media import content_disposition, media_type_for, parse_range

SIZE = 1000


def test_no_header_means_the_whole_file() -> None:
    assert parse_range(None, SIZE) is None
    assert parse_range("", SIZE) is None


def test_open_ended_range_runs_to_the_last_byte() -> None:
    assert parse_range("bytes=0-", SIZE) == (0, 999)
    assert parse_range("bytes=500-", SIZE) == (500, 999)


def test_closed_range() -> None:
    assert parse_range("bytes=100-199", SIZE) == (100, 199)


def test_range_past_the_end_is_clamped() -> None:
    assert parse_range("bytes=900-5000", SIZE) == (900, 999)


def test_suffix_range_returns_the_tail() -> None:
    assert parse_range("bytes=-200", SIZE) == (800, 999)
    assert parse_range("bytes=-5000", SIZE) == (0, 999)


@pytest.mark.parametrize(
    "header",
    ["bytes=1000-", "bytes=1500-1600", "bytes=300-200", "bytes=-0", "bytes=", "items=0-10", "0-10"],
)
def test_unsatisfiable_ranges_are_rejected(header: str) -> None:
    with pytest.raises(ValueError):
        parse_range(header, SIZE)


def test_media_types_cover_the_allowed_uploads() -> None:
    assert media_type_for(Path("a.mp3")) == "audio/mpeg"
    assert media_type_for(Path("a.M4A")) == "audio/mp4"
    assert media_type_for(Path("a.pdf")) == "application/pdf"
    assert media_type_for(Path("a.unknown")) == "application/octet-stream"


def test_cyrillic_filename_survives_the_header() -> None:
    header = content_disposition("Планёрка.txt")

    assert header.startswith("attachment;")
    assert "filename*=UTF-8''" in header
    assert "Планёрка" not in header, "the raw name must be percent-encoded"

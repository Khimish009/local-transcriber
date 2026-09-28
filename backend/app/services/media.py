"""HTTP Range streaming for the source recording (TASKS.md T7.3).

The player seeks by timecode, and seeking in a multi-hour file only works if the server
answers partial requests. Everything here is plain HTTP — no audio decoding.
"""

import re
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

from fastapi import Response
from fastapi.responses import FileResponse, StreamingResponse

STREAM_CHUNK_SIZE = 256 * 1024

# Only single-range requests are supported, which is all browsers send for media.
_RANGE_PATTERN = re.compile(r"^bytes=(\d*)-(\d*)$")

MEDIA_TYPES: dict[str, str] = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "mp4": "video/mp4",
    "webm": "audio/webm",
    "ogg": "audio/ogg",
    "json": "application/json",
    "txt": "text/plain; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}
DEFAULT_MEDIA_TYPE = "application/octet-stream"


def media_type_for(path: Path) -> str:
    return MEDIA_TYPES.get(path.suffix.lstrip(".").lower(), DEFAULT_MEDIA_TYPE)


def content_disposition(filename: str, inline: bool = False) -> str:
    """RFC 6266 header that survives Cyrillic filenames in every browser."""
    ascii_fallback = filename.encode("ascii", "replace").decode("ascii").replace('"', "_")
    disposition = "inline" if inline else "attachment"
    return f"{disposition}; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Return the inclusive byte range, or None when the whole file is requested.

    Raises ValueError when the header is present but cannot be satisfied — the caller
    turns that into 416.
    """
    if not header:
        return None

    match = _RANGE_PATTERN.match(header.strip())
    if match is None:
        raise ValueError("Unsupported Range header")

    raw_start, raw_end = match.group(1), match.group(2)
    if not raw_start and not raw_end:
        raise ValueError("Empty Range header")

    if not raw_start:
        # `bytes=-500` means the last 500 bytes.
        length = int(raw_end)
        if length <= 0:
            raise ValueError("Empty suffix range")
        start, end = max(0, size - length), size - 1
    else:
        start = int(raw_start)
        end = int(raw_end) if raw_end else size - 1
        end = min(end, size - 1)

    if start >= size or start > end:
        raise ValueError("Range outside the file")
    return start, end


def iter_file(path: Path, start: int, end: int) -> Iterator[bytes]:
    remaining = end - start + 1
    with path.open("rb") as handle:
        handle.seek(start)
        while remaining > 0:
            chunk = handle.read(min(STREAM_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def range_response(path: Path, range_header: str | None, filename: str | None = None) -> Response:
    """Serve `path` whole (200) or partially (206), or refuse the range (416)."""
    size = path.stat().st_size
    media_type = media_type_for(path)
    headers = {"Accept-Ranges": "bytes"}
    if filename:
        headers["Content-Disposition"] = content_disposition(filename, inline=True)

    try:
        window = parse_range(range_header, size)
    except ValueError:
        return Response(
            status_code=416,
            headers={**headers, "Content-Range": f"bytes */{size}"},
        )

    if window is None:
        return FileResponse(path, media_type=media_type, headers=headers)

    start, end = window
    return StreamingResponse(
        iter_file(path, start, end),
        status_code=206,
        media_type=media_type,
        headers={
            **headers,
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(end - start + 1),
        },
    )

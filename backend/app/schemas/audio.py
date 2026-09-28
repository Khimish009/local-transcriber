from pydantic import BaseModel


class AudioMetadata(BaseModel):
    """What the source recording actually contains, as reported by ffprobe."""

    duration_seconds: float
    format_name: str | None = None
    codec_name: str | None = None
    sample_rate: int | None = None
    channels: int | None = None

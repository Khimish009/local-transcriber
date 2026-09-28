from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Everything comes from the environment, nothing is hardcoded."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    app_env: str = "local"
    app_version: str = "0.1.0"

    redis_url: str = "redis://redis:6379/0"

    data_dir: Path = Path("/data")
    models_dir: Path = Path("/models")

    asr_model: str = "v3_e2e_rnnt"
    device: str = "cpu"

    # repr=False keeps the token out of tracebacks and log lines that dump settings.
    hf_token: str | None = Field(default=None, repr=False)

    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"

    max_upload_mb: int = 2048
    max_asr_chunk_seconds: int = 20
    merge_silence_gap_ms: int = 400

    # Alignment thresholds (SPEC.md §3.4, §3.5). A word whose midpoint falls outside every
    # speaker interval may still be attached to one this close; otherwise speaker_id is null.
    speaker_match_tolerance_ms: int = 250
    # A pause at least this long starts a new transcript block.
    block_silence_gap_ms: int = 1500
    # Hard cap on a block, so a long monologue never becomes one giant paragraph.
    max_block_seconds: float = 40.0

    # TTF used for the PDF export. None means "discover a Cyrillic-capable font"
    # (app/services/exports.py) — the images ship DejaVu Sans.
    pdf_font_path: Path | None = None

    # NoDecode: comma-separated list, not JSON. Extensions are stored without the dot.
    allowed_extensions: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["wav", "mp3", "m4a", "mp4", "webm", "ogg"]
    )

    # How long an SSE connection waits before emitting a keep-alive comment.
    sse_keepalive_seconds: float = 15.0
    # Fallback poll interval for SSE, in case a pub/sub message is missed.
    sse_poll_seconds: float = 2.0

    queue_name: str = "transcription"
    # Upper bound for a single transcription job (long meetings on CPU are slow).
    job_timeout_seconds: int = 86_400
    # How often the RQ worker refreshes its registration in Redis. Kept low so that
    # /health and the container healthcheck recover quickly after a Redis restart.
    worker_heartbeat_seconds: int = 60

    # NoDecode: CORS_ORIGINS is a comma-separated string, not JSON.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @field_validator("pdf_font_path", mode="before")
    @classmethod
    def _empty_path_is_unset(cls, value: object) -> object:
        # docker-compose passes `PDF_FONT_PATH: ${PDF_FONT_PATH:-}`, and an empty string
        # would otherwise become Path("."), i.e. a font path that can never be opened.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("allowed_extensions", mode="before")
    @classmethod
    def _split_extensions(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.split(",")
        if isinstance(value, list):
            return [str(item).strip().lstrip(".").lower() for item in value if str(item).strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()

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

    max_upload_mb: int = 2048
    max_asr_chunk_seconds: int = 20
    merge_silence_gap_ms: int = 400

    queue_name: str = "transcription"
    # How often the RQ worker refreshes its registration in Redis. Kept low so that
    # /health and the container healthcheck recover quickly after a Redis restart.
    worker_heartbeat_seconds: int = 60

    # NoDecode: CORS_ORIGINS is a comma-separated string, not JSON.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()

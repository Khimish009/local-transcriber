from pathlib import Path

from app.core.config import Settings


def test_cors_origins_parsed_from_comma_separated_string() -> None:
    settings = Settings(cors_origins="http://localhost:3000, http://127.0.0.1:3000")

    assert settings.cors_origins == ["http://localhost:3000", "http://127.0.0.1:3000"]


def test_env_overrides_defaults(monkeypatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://example:6379/1")
    monkeypatch.setenv("DATA_DIR", "/tmp/data")
    monkeypatch.setenv("ASR_MODEL", "v3_rnnt")

    settings = Settings()

    assert settings.redis_url == "redis://example:6379/1"
    assert settings.data_dir == Path("/tmp/data")
    assert settings.asr_model == "v3_rnnt"


def test_cors_origins_from_env_not_parsed_as_json(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:3001")

    assert Settings().cors_origins == ["http://localhost:3000", "http://localhost:3001"]

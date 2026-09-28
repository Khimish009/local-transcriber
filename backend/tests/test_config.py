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


def test_alignment_thresholds_come_from_env(monkeypatch) -> None:
    """SPEC.md §3.3 — thresholds must never be hardcoded in business logic."""
    monkeypatch.setenv("SPEAKER_MATCH_TOLERANCE_MS", "500")
    monkeypatch.setenv("BLOCK_SILENCE_GAP_MS", "2000")
    monkeypatch.setenv("MAX_BLOCK_SECONDS", "25")

    settings = Settings()

    assert settings.speaker_match_tolerance_ms == 500
    assert settings.block_silence_gap_ms == 2000
    assert settings.max_block_seconds == 25.0


def test_cors_origins_from_env_not_parsed_as_json(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:3001")

    assert Settings().cors_origins == ["http://localhost:3000", "http://localhost:3001"]

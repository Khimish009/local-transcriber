"""T13.5 — the `models` service downloads what is missing and never blocks the stack."""

import logging

import pytest

from app.core.errors import AppError, ErrorCode
from app.services.model_cache import DIARIZATION_MODEL, asr_checkpoint_path, repo_cache_dir
from worker import fetch_models as service


def cache_everything(settings) -> None:
    snapshot = repo_cache_dir(settings, DIARIZATION_MODEL) / "snapshots" / "abc123"
    snapshot.mkdir(parents=True)
    (snapshot / "config.yaml").write_text("fake")
    checkpoint = asr_checkpoint_path(settings)
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"weights")


@pytest.fixture
def loads(monkeypatch) -> list[str]:
    """Records which models were loaded, that is downloaded; both succeed by default."""
    calls: list[str] = []
    monkeypatch.setattr(
        "worker.pipeline.models.load_diarization_pipeline",
        lambda settings, token: calls.append("diarization"),
    )
    monkeypatch.setattr(
        "worker.pipeline.models.load_asr_model", lambda settings: calls.append("asr")
    )
    return calls


def test_nothing_is_loaded_when_the_weights_are_there(settings, loads) -> None:
    cache_everything(settings)

    assert service.fetch_models(settings) == []
    assert loads == [], "a start with cached weights must not even touch the models"


def test_missing_weights_are_downloaded(settings, loads) -> None:
    assert service.fetch_models(settings) == []
    assert loads == ["diarization", "asr"]


def test_failed_download_is_reported_and_the_other_model_still_comes(
    settings, loads, monkeypatch, caplog
) -> None:
    def refuse(settings, token):
        raise AppError(ErrorCode.HF_TOKEN_REQUIRED, "HF_TOKEN is required")

    monkeypatch.setattr("worker.pipeline.models.load_diarization_pipeline", refuse)

    with caplog.at_level(logging.ERROR):
        assert service.fetch_models(settings) == ["diarization"]

    assert loads == ["asr"]
    assert any(getattr(r, "error_code", None) == "HF_TOKEN_REQUIRED" for r in caplog.records)


def test_unexpected_error_is_reported_as_missing(settings, loads, monkeypatch) -> None:
    def boom(settings):
        raise RuntimeError("connection reset")

    monkeypatch.setattr("worker.pipeline.models.load_asr_model", boom)

    assert service.fetch_models(settings) == ["asr"]


def test_service_exits_cleanly_even_when_a_model_is_missing(monkeypatch) -> None:
    """The worker must still start: its jobs then fail with the model's stable error code."""
    monkeypatch.setattr(service, "fetch_models", lambda settings: ["asr"])
    monkeypatch.setattr(service, "configure_logging", lambda: None)

    assert service.main() == 0

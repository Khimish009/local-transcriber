import logging

from app.core.errors import AppError, ErrorCode
from worker.main import warm_up_models


def test_warm_up_loads_the_model_once(settings, monkeypatch) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(
        "worker.pipeline.models.get_diarization_pipeline",
        lambda *args: calls.append(args) or object(),
    )

    warm_up_models(settings)

    assert len(calls) == 1
    assert calls[0][0] is settings


def test_missing_token_does_not_stop_the_worker(settings, monkeypatch, caplog) -> None:
    def refuse(*args):
        raise AppError(ErrorCode.HF_TOKEN_REQUIRED, "HF_TOKEN is required")

    monkeypatch.setattr("worker.pipeline.models.get_diarization_pipeline", refuse)

    # Must not raise: the worker still serves the queue and jobs fail with a clear code.
    with caplog.at_level(logging.WARNING):
        warm_up_models(settings)

    assert any(getattr(r, "error_code", None) == "HF_TOKEN_REQUIRED" for r in caplog.records)


def test_unexpected_error_does_not_stop_the_worker(settings, monkeypatch) -> None:
    def boom(*args):
        raise RuntimeError("out of memory")

    monkeypatch.setattr("worker.pipeline.models.get_diarization_pipeline", boom)

    warm_up_models(settings)

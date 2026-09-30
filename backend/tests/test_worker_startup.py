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


# --- registration keep-alive --------------------------------------------------


def test_beat_until_keeps_beating_until_stopped() -> None:
    """SimpleWorker runs jobs inline, so nothing else refreshes the registration."""
    import threading

    from worker.main import beat_until

    stop = threading.Event()

    class Worker:
        def __init__(self) -> None:
            self.beats = 0

        def heartbeat(self, timeout=None, pipeline=None) -> None:
            self.beats += 1
            if self.beats == 3:
                stop.set()

    worker = Worker()

    assert beat_until(worker, 0, stop) == 3
    assert worker.beats == 3


def test_beat_until_survives_a_redis_blip() -> None:
    import threading

    from worker.main import beat_until

    stop = threading.Event()

    class FlakyWorker:
        def __init__(self) -> None:
            self.calls = 0

        def heartbeat(self, timeout=None, pipeline=None) -> None:
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError("redis is briefly unreachable")
            stop.set()

    worker = FlakyWorker()

    # The failed beat is not counted, but the loop keeps running.
    assert beat_until(worker, 0, stop) == 1
    assert worker.calls == 2


def test_keepalive_thread_refreshes_the_registration(redis) -> None:
    import time

    from rq import Queue, SimpleWorker

    from worker.main import start_keepalive

    worker = SimpleWorker([Queue("transcription", connection=redis)], connection=redis)
    worker.register_birth()
    redis.expire(worker.key, 1)

    stop = start_keepalive(worker, 0.01)
    time.sleep(0.1)
    ttl = redis.ttl(worker.key)
    stop.set()

    assert ttl > 1, "the registration key must be pushed back before it can expire"

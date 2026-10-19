import logging
import types

import pytest

from app.core.errors import AppError, ErrorCode
from worker.main import warm_up_models


@pytest.fixture
def loads(monkeypatch) -> list[str]:
    """Records which models warm-up asked for; both loaders succeed by default."""
    calls: list[str] = []
    monkeypatch.setattr(
        "worker.pipeline.models.get_diarization_pipeline",
        lambda *args: calls.append("diarization") or object(),
    )
    monkeypatch.setattr(
        "worker.pipeline.models.get_asr_model", lambda *args: calls.append("asr") or object()
    )
    return calls


def test_warm_up_loads_both_models_once(settings, loads) -> None:
    warm_up_models(settings)

    assert loads == ["diarization", "asr"]


def test_missing_token_does_not_stop_the_worker(settings, monkeypatch, loads, caplog) -> None:
    def refuse(*args):
        raise AppError(ErrorCode.HF_TOKEN_REQUIRED, "HF_TOKEN is required")

    monkeypatch.setattr("worker.pipeline.models.get_diarization_pipeline", refuse)

    # Must not raise: the worker still serves the queue and jobs fail with a clear code.
    with caplog.at_level(logging.WARNING):
        warm_up_models(settings)

    assert any(getattr(r, "error_code", None) == "HF_TOKEN_REQUIRED" for r in caplog.records)
    assert loads == ["asr"], "a diarization failure must not leave GigaAM cold"


def test_unexpected_error_does_not_stop_the_worker(settings, monkeypatch, loads) -> None:
    def boom(*args):
        raise RuntimeError("out of memory")

    monkeypatch.setattr("worker.pipeline.models.get_asr_model", boom)

    warm_up_models(settings)

    assert loads == ["diarization"]


# --- registration keep-alive --------------------------------------------------


class IntactRegistration:
    """What `refresh_registration` reads from a worker whose registration is complete."""

    key = "rq:worker:fake"
    connection = types.SimpleNamespace(hexists=lambda key, field: True)

    def register_birth(self) -> None:
        raise AssertionError("an intact registration must not be rebuilt")


def test_beat_until_keeps_beating_until_stopped() -> None:
    """SimpleWorker runs jobs inline, so nothing else refreshes the registration."""
    import threading

    from worker.main import beat_until

    stop = threading.Event()

    class Worker(IntactRegistration):
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

    class FlakyWorker(IntactRegistration):
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


# --- registration lost while the host slept (T13.8) ------------------------------------


def _registered_worker(redis):
    from rq import Queue, SimpleWorker

    worker = SimpleWorker([Queue("transcription", connection=redis)], connection=redis)
    worker.register_birth()
    return worker


def _listening(redis) -> str:
    from app.services.health import check_workers

    return check_workers(redis, "transcription", heartbeat_seconds=60).status


def test_registration_expired_during_sleep_is_restored(redis) -> None:
    """A paused Docker VM wakes up to an expired key; RQ's own beat brings back a stub."""
    from worker.main import refresh_registration

    worker = _registered_worker(redis)
    redis.delete(worker.key)  # what Redis does on wake: the TTL ran out on the wall clock
    worker.heartbeat()  # RQ's beat between jobs: recreates the key with last_heartbeat only
    assert _listening(redis) == "down", "the stub alone does not match the queue"

    assert refresh_registration(worker) is True

    assert redis.hexists(worker.key, "queues")
    assert _listening(redis) == "ok"
    assert redis.ttl(worker.key) > 60


def test_registration_gone_entirely_is_restored(redis) -> None:
    from worker.main import refresh_registration

    worker = _registered_worker(redis)
    redis.delete(worker.key)

    assert refresh_registration(worker) is True
    assert _listening(redis) == "ok"


def test_intact_registration_is_only_refreshed(redis) -> None:
    from worker.main import refresh_registration

    worker = _registered_worker(redis)
    birth = redis.hget(worker.key, "birth")

    assert refresh_registration(worker) is False
    assert redis.hget(worker.key, "birth") == birth, "a live registration is not rebuilt"

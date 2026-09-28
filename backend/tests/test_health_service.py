from datetime import UTC, datetime, timedelta

from rq import Queue, SimpleWorker

from app.core.config import Settings
from app.services.health import (
    HEARTBEAT_SLACK_SECONDS,
    check_models,
    check_redis,
    check_workers,
)
from app.services.model_cache import DIARIZATION_MODEL, asr_checkpoint_path, repo_cache_dir


def cache_diarization(settings) -> None:
    snapshot = repo_cache_dir(settings, DIARIZATION_MODEL) / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (snapshot / "config.yaml").write_text("fake")


def cache_asr(settings) -> None:
    path = asr_checkpoint_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"ckpt")


def test_check_redis_ok(redis) -> None:
    assert check_redis(redis).status == "ok"


def test_check_redis_down(redis, redis_server) -> None:
    redis_server.connected = False

    result = check_redis(redis)

    assert result.status == "down"
    assert result.detail


def test_check_workers_reports_empty_queue(redis) -> None:
    result = check_workers(redis, "transcription", 60)

    assert result.status == "down"
    assert result.queue == "transcription"


def test_live_worker_counts(redis) -> None:
    worker = SimpleWorker([Queue("transcription", connection=redis)], connection=redis)
    worker.register_birth()

    result = check_workers(redis, "transcription", 60)

    assert result.status == "ok"
    assert result.count == 1


def test_worker_that_stopped_heartbeating_is_not_counted(redis) -> None:
    """T8.2 — a killed worker never deregisters, so a stale record must not read as `ok`."""
    worker = SimpleWorker([Queue("transcription", connection=redis)], connection=redis)
    worker.register_birth()
    stale = datetime.now(UTC) - timedelta(seconds=60 * 2 + HEARTBEAT_SLACK_SECONDS + 5)
    redis.hset(worker.key, "last_heartbeat", stale.strftime("%Y-%m-%dT%H:%M:%S.%fZ"))

    result = check_workers(redis, "transcription", 60)

    assert result.status == "down"
    assert result.count == 0


def test_check_models_missing_until_weights_are_downloaded(tmp_path) -> None:
    models_dir = tmp_path / "models"
    models_dir.mkdir()

    result = check_models(Settings(models_dir=models_dir))

    assert result.status == "missing"
    assert "HF_TOKEN" in result.detail


def test_check_models_ready_when_weights_are_cached(tmp_path) -> None:
    settings = Settings(models_dir=tmp_path / "models")
    cache_diarization(settings)
    cache_asr(settings)

    assert check_models(settings).status == "ready"


def test_check_models_missing_while_the_asr_model_is_absent(tmp_path) -> None:
    settings = Settings(models_dir=tmp_path / "models")
    cache_diarization(settings)

    result = check_models(settings)

    assert result.status == "missing"
    assert settings.asr_model in result.detail


def test_check_models_missing_directory(tmp_path) -> None:
    result = check_models(Settings(models_dir=tmp_path / "nope"))

    assert result.status == "missing"

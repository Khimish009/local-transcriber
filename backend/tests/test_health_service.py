from app.core.config import Settings
from app.services.health import check_models, check_redis, check_workers
from app.services.model_cache import DIARIZATION_MODEL, repo_cache_dir


def test_check_redis_ok(redis) -> None:
    assert check_redis(redis).status == "ok"


def test_check_redis_down(redis, redis_server) -> None:
    redis_server.connected = False

    result = check_redis(redis)

    assert result.status == "down"
    assert result.detail


def test_check_workers_reports_empty_queue(redis) -> None:
    result = check_workers(redis, "transcription")

    assert result.status == "down"
    assert result.queue == "transcription"


def test_check_models_missing_until_weights_are_downloaded(tmp_path) -> None:
    models_dir = tmp_path / "models"
    models_dir.mkdir()

    result = check_models(Settings(models_dir=models_dir))

    assert result.status == "missing"
    assert "HF_TOKEN" in result.detail


def test_check_models_ready_when_weights_are_cached(tmp_path) -> None:
    models_dir = tmp_path / "models"
    settings = Settings(models_dir=models_dir)
    snapshot = repo_cache_dir(settings, DIARIZATION_MODEL) / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (snapshot / "config.yaml").write_text("fake")

    assert check_models(settings).status == "ready"


def test_check_models_missing_directory(tmp_path) -> None:
    result = check_models(Settings(models_dir=tmp_path / "nope"))

    assert result.status == "missing"

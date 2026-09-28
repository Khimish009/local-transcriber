from app.core.config import Settings


def test_health_returns_200_and_full_payload(client) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "status",
        "version",
        "app_env",
        "redis",
        "workers",
        "models",
        "limits",
    }
    assert body["redis"]["status"] == "ok"


def test_health_is_degraded_without_workers(client) -> None:
    body = client.get("/api/v1/health").json()

    # Phase 0 test environment has no RQ worker process running.
    assert body["workers"]["status"] == "down"
    assert body["workers"]["count"] == 0
    assert body["status"] == "degraded"


def test_health_reports_redis_down(client, redis_server) -> None:
    redis_server.connected = False

    body = client.get("/api/v1/health").json()

    assert body["redis"]["status"] == "down"
    assert body["status"] == "down"


def test_models_missing_when_directory_absent(client, settings: Settings) -> None:
    body = client.get("/api/v1/health").json()

    assert body["models"]["status"] == "missing"
    assert body["models"]["asr_model"] == settings.asr_model


def test_health_exposes_upload_limits(client, settings: Settings) -> None:
    limits = client.get("/api/v1/health").json()["limits"]

    assert limits["max_upload_mb"] == settings.max_upload_mb
    assert "wav" in limits["allowed_extensions"]

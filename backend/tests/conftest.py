import fakeredis
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.redis import get_async_redis, get_redis
from app.main import create_app
from app.services.job_store import JobStore
from app.services.storage import JobStorage


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        redis_url="redis://localhost:6379/0",
        data_dir=tmp_path / "data",
        models_dir=tmp_path / "models",
        max_upload_mb=1,
        sse_poll_seconds=0.05,
        sse_keepalive_seconds=0.2,
    )


@pytest.fixture
def redis_server() -> fakeredis.FakeServer:
    """Separate handle so tests can simulate an unreachable Redis."""
    return fakeredis.FakeServer()


@pytest.fixture
def redis(redis_server: fakeredis.FakeServer) -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(server=redis_server)


@pytest.fixture
def async_redis(redis_server: fakeredis.FakeServer) -> fakeredis.FakeAsyncRedis:
    """Async client backed by the same fake server, so pub/sub reaches the SSE stream."""
    return fakeredis.FakeAsyncRedis(server=redis_server)


@pytest.fixture
def store(redis: fakeredis.FakeRedis) -> JobStore:
    return JobStore(redis)


@pytest.fixture
def storage(settings: Settings) -> JobStorage:
    return JobStorage(settings.data_dir)


@pytest.fixture
def client(
    settings: Settings,
    redis: fakeredis.FakeRedis,
    async_redis: fakeredis.FakeAsyncRedis,
) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_async_redis] = lambda: async_redis
    return TestClient(app)

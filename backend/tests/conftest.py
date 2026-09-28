import fakeredis
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.redis import get_redis
from app.main import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        redis_url="redis://localhost:6379/0",
        data_dir=tmp_path / "data",
        models_dir=tmp_path / "models",
    )


@pytest.fixture
def redis_server() -> fakeredis.FakeServer:
    """Separate handle so tests can simulate an unreachable Redis."""
    return fakeredis.FakeServer()


@pytest.fixture
def redis(redis_server: fakeredis.FakeServer) -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(server=redis_server)


@pytest.fixture
def client(settings: Settings, redis: fakeredis.FakeRedis) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_redis] = lambda: redis
    return TestClient(app)

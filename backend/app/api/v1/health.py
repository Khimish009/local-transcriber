from typing import Annotated

from fastapi import APIRouter, Depends
from redis import Redis

from app.core.config import Settings, get_settings
from app.core.redis import get_redis
from app.schemas.health import HealthResponse
from app.services.health import build_health

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(
    redis: Annotated[Redis, Depends(get_redis)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HealthResponse:
    """Liveness + dependency status. Always HTTP 200 so probes can read the details."""
    return build_health(redis, settings)

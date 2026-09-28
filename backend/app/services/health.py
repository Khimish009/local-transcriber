import logging

from redis import Redis
from redis.exceptions import RedisError
from rq import Worker

from app.core.config import Settings
from app.schemas.health import (
    HealthResponse,
    ModelsHealth,
    RedisHealth,
    WorkersHealth,
)

logger = logging.getLogger(__name__)


def check_redis(redis: Redis) -> RedisHealth:
    try:
        redis.ping()
    except RedisError as exc:
        logger.warning("redis ping failed", extra={"stage": "health", "error": str(exc)})
        return RedisHealth(status="down", detail="Redis is not reachable")
    return RedisHealth(status="ok")


def check_workers(redis: Redis, queue_name: str) -> WorkersHealth:
    try:
        workers = Worker.all(connection=redis)
    except RedisError as exc:
        logger.warning("worker lookup failed", extra={"stage": "health", "error": str(exc)})
        return WorkersHealth(
            status="down", count=0, queue=queue_name, detail="Redis is not reachable"
        )

    listening = [w for w in workers if queue_name in w.queue_names()]
    if not listening:
        return WorkersHealth(
            status="down",
            count=0,
            queue=queue_name,
            detail=f"No RQ worker is listening on '{queue_name}'",
        )
    return WorkersHealth(status="ok", count=len(listening), queue=queue_name)


def check_models(settings: Settings) -> ModelsHealth:
    """Phase 0 does not load any ML model yet — only the persistent directory is verified."""
    if not settings.models_dir.is_dir():
        return ModelsHealth(
            status="missing",
            asr_model=settings.asr_model,
            device=settings.device,
            detail=f"Models directory {settings.models_dir} does not exist",
        )
    return ModelsHealth(
        status="not_required",
        asr_model=settings.asr_model,
        device=settings.device,
        detail="ML models are not loaded yet (Phase 0)",
    )


def build_health(redis: Redis, settings: Settings) -> HealthResponse:
    redis_health = check_redis(redis)
    workers_health = check_workers(redis, settings.queue_name)
    models_health = check_models(settings)

    if redis_health.status == "down":
        status = "down"
    elif workers_health.status != "ok" or models_health.status == "missing":
        status = "degraded"
    else:
        status = "ok"

    return HealthResponse(
        status=status,
        version=settings.app_version,
        app_env=settings.app_env,
        redis=redis_health,
        workers=workers_health,
        models=models_health,
    )

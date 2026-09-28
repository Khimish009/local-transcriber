import logging

from redis import Redis
from redis.exceptions import RedisError
from rq import Worker

from app.core.config import Settings
from app.schemas.health import (
    HealthResponse,
    ModelsHealth,
    RedisHealth,
    UploadLimits,
    WorkersHealth,
)
from app.services.model_cache import DIARIZATION_MODEL, is_asr_model_cached, is_model_cached

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
    """Report weight availability. The API never loads a model — it only looks at disk."""
    if not settings.models_dir.is_dir():
        return ModelsHealth(
            status="missing",
            asr_model=settings.asr_model,
            device=settings.device,
            detail=f"Models directory {settings.models_dir} does not exist",
        )
    missing: list[str] = []
    if not is_model_cached(settings, DIARIZATION_MODEL):
        missing.append(
            f"{DIARIZATION_MODEL} is not downloaded yet — set HF_TOKEN and accept the "
            "model terms on Hugging Face"
        )
    if not is_asr_model_cached(settings):
        missing.append(
            f"GigaAM '{settings.asr_model}' is not downloaded yet — it is fetched on the "
            "first transcription job"
        )

    if missing:
        return ModelsHealth(
            status="missing",
            asr_model=settings.asr_model,
            device=settings.device,
            detail="; ".join(missing),
        )
    return ModelsHealth(
        status="ready",
        asr_model=settings.asr_model,
        device=settings.device,
        detail=f"{DIARIZATION_MODEL} and GigaAM '{settings.asr_model}' are cached locally",
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
        limits=UploadLimits(
            max_upload_mb=settings.max_upload_mb,
            allowed_extensions=settings.allowed_extensions,
        ),
    )

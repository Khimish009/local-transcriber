from functools import lru_cache

from redis import Redis

from app.core.config import get_settings


@lru_cache
def get_redis() -> Redis:
    """Process-wide Redis client.

    `decode_responses` stays False on purpose: RQ refuses connections that decode
    responses, and API and worker must share the same client configuration.
    """
    settings = get_settings()
    return Redis.from_url(settings.redis_url)

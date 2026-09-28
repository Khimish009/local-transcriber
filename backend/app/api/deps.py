from typing import Annotated

from fastapi import Depends
from redis import Redis
from redis.asyncio import Redis as AsyncRedis

from app.core.config import Settings, get_settings
from app.core.redis import get_async_redis, get_redis
from app.services.job_store import JobStore
from app.services.storage import JobStorage

SettingsDep = Annotated[Settings, Depends(get_settings)]
RedisDep = Annotated[Redis, Depends(get_redis)]
AsyncRedisDep = Annotated[AsyncRedis, Depends(get_async_redis)]


def get_job_store(redis: RedisDep) -> JobStore:
    return JobStore(redis)


def get_job_storage(settings: SettingsDep) -> JobStorage:
    return JobStorage(settings.data_dir)


JobStoreDep = Annotated[JobStore, Depends(get_job_store)]
JobStorageDep = Annotated[JobStorage, Depends(get_job_storage)]

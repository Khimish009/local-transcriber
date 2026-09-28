from typing import Literal

from pydantic import BaseModel

ComponentStatus = Literal["ok", "degraded", "down"]
ModelsStatus = Literal["ready", "missing", "not_required"]


class RedisHealth(BaseModel):
    status: ComponentStatus
    detail: str | None = None


class WorkersHealth(BaseModel):
    status: ComponentStatus
    count: int
    queue: str
    detail: str | None = None


class ModelsHealth(BaseModel):
    status: ModelsStatus
    asr_model: str
    device: str
    detail: str | None = None


class HealthResponse(BaseModel):
    status: ComponentStatus
    version: str
    app_env: str
    redis: RedisHealth
    workers: WorkersHealth
    models: ModelsHealth

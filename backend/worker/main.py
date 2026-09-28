"""RQ worker entrypoint.

Phase 0: the worker only proves connectivity — it connects to Redis and listens on the
transcription queue. Pipeline stages arrive in later phases.
"""

import logging

from rq import Queue, Worker

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.redis import get_redis

logger = logging.getLogger(__name__)


def main() -> None:
    configure_logging()
    settings = get_settings()
    redis = get_redis()

    redis.ping()
    logger.info(
        "worker starting",
        extra={"stage": "startup", "queue": settings.queue_name, "device": settings.device},
    )

    queue = Queue(settings.queue_name, connection=redis)
    worker = Worker(
        [queue],
        connection=redis,
        default_worker_ttl=settings.worker_heartbeat_seconds,
    )
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()

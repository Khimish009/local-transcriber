"""Container healthcheck for the worker service.

Exits 0 only when at least one RQ worker on this host has a fresh heartbeat on the
configured queue.
"""

import socket
import sys

from rq import Worker

from app.core.config import get_settings
from app.core.redis import get_redis


def _as_str(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value or ""


def main() -> int:
    settings = get_settings()
    redis = get_redis()
    hostname = socket.gethostname()

    for worker in Worker.all(connection=redis):
        if _as_str(worker.hostname) == hostname and settings.queue_name in worker.queue_names():
            return 0

    print(f"no live RQ worker for queue '{settings.queue_name}' on {hostname}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

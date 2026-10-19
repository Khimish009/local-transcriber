"""Download model weights before the worker starts (TASKS.md T13.5).

The worker has no route to the internet (the `internal` network in docker-compose.yml), so
it can only use weights that are already in MODELS_DIR. This one-shot `models` service runs
first, on a network that does reach the internet, and downloads whatever is missing. When
everything is on disk it exits at once, without even importing torch.

A failed download does not stop the stack: the exit code stays 0 and the worker starts as
usual. Its warm-up and every job that needs the missing model then fail with the stable
error codes they always had, the UI shows them, and the reason is in this service's logs.
"""

import logging
import signal
import sys
from collections.abc import Callable
from types import FrameType

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.services.model_cache import is_asr_model_cached, is_model_cached

logger = logging.getLogger(__name__)


def fetch_models(settings: Settings) -> list[str]:
    """Download the missing weights. Returns the models that are still missing."""
    # Imported here: when the weights are already there, nothing loads torch at all.
    from worker.pipeline.models import load_asr_model, load_diarization_pipeline

    steps: tuple[tuple[str, Callable[[], bool], Callable[[], object]], ...] = (
        (
            "diarization",
            lambda: is_model_cached(settings),
            lambda: load_diarization_pipeline(settings, settings.hf_token),
        ),
        ("asr", lambda: is_asr_model_cached(settings), lambda: load_asr_model(settings)),
    )
    missing: list[str] = []
    for model, cached, load in steps:
        if cached():
            logger.info("model already downloaded", extra={"stage": "models", "model": model})
            continue
        try:
            # Loading is what downloads, and it also proves the weights are usable.
            load()
        except AppError as exc:
            missing.append(model)
            logger.error(
                "model download failed",
                extra={
                    "stage": "models",
                    "model": model,
                    "error_code": exc.code.value,
                    "detail": exc.message,
                },
            )
        except Exception:
            missing.append(model)
            logger.exception("model download failed", extra={"stage": "models", "model": model})
        else:
            logger.info("model downloaded", extra={"stage": "models", "model": model})
    return missing


def _exit_on_sigterm(signum: int, frame: FrameType | None) -> None:
    """`docker compose stop` during a download must not leave half a checkpoint behind.

    As PID 1 the process ignores SIGTERM unless it handles it, and Docker kills it ten
    seconds later, mid-file: gigaam writes straight to the final path, and a truncated
    checkpoint would pass for a downloaded one. Raised as SystemExit, the signal lets gigaam
    delete the partial file on its way out. Hugging Face downloads into `.incomplete` files
    and resumes them on the next run.
    """
    raise SystemExit(128 + signum)


def main() -> int:
    signal.signal(signal.SIGTERM, _exit_on_sigterm)
    configure_logging()
    missing = fetch_models(get_settings())
    if missing:
        logger.warning(
            "the worker starts without these models; their jobs will fail until they are "
            "downloaded — fix the cause above and run `docker compose up` again",
            extra={"stage": "models", "missing": missing},
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

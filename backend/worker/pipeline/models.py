"""Centralized model loading (TASKS.md T3.1-T3.3, AGENTS.md "ML integration rules").

Models are loaded once per worker process and reused across jobs. Weights live in
MODELS_DIR so a container restart never re-downloads them. The HF token is read from the
environment only and is never logged or returned through the API.
"""

import logging
import os
from typing import Any

from app.core.config import Settings
from app.core.errors import AppError, ErrorCode
from app.services.model_cache import DIARIZATION_MODEL, hf_cache_dir, is_model_cached

logger = logging.getLogger(__name__)

_pipeline_cache: dict[str, Any] = {}


def _prepare_hf_environment(settings: Settings, cached: bool) -> None:
    cache_dir = hf_cache_dir(settings)
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(settings.models_dir)
    os.environ["HF_HUB_CACHE"] = str(cache_dir)
    # No telemetry about what is being processed locally.
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    if cached:
        # Weights are present: never reach out to the network during inference.
        os.environ["HF_HUB_OFFLINE"] = "1"
    else:
        os.environ.pop("HF_HUB_OFFLINE", None)


def _resolve_device(settings: Settings):
    try:
        import torch
    except ImportError as exc:
        raise AppError(
            ErrorCode.MODEL_NOT_AVAILABLE, "torch is not installed in this image"
        ) from exc

    requested = settings.device.strip().lower()
    if requested.startswith("cuda") and not torch.cuda.is_available():
        # Never silently fall back to CPU — the user asked for GPU (TASKS.md T9.3).
        raise AppError(
            ErrorCode.MODEL_NOT_AVAILABLE,
            "DEVICE=cuda was requested but CUDA is not available in this container",
        )
    return torch.device(requested)


def load_diarization_pipeline(settings: Settings, hf_token: str | None) -> Any:
    """Load the pyannote pipeline. Raises with a stable error code on failure."""
    cached = is_model_cached(settings)
    if not cached and not hf_token:
        raise AppError(
            ErrorCode.HF_TOKEN_REQUIRED,
            "HF_TOKEN is required to download the diarization model on first run",
        )

    _prepare_hf_environment(settings, cached)

    try:
        from pyannote.audio import Pipeline
    except ImportError as exc:
        raise AppError(
            ErrorCode.MODEL_NOT_AVAILABLE,
            "pyannote.audio is not installed in this image",
        ) from exc

    logger.info(
        "loading diarization model",
        extra={"stage": "diarization", "model": DIARIZATION_MODEL, "offline": cached},
    )

    try:
        pipeline = _from_pretrained(Pipeline, hf_token if not cached else None)
    except AppError:
        raise
    except Exception as exc:
        # The exception text can embed the token, so only the type is logged.
        logger.error(
            "could not load diarization model",
            extra={
                "stage": "diarization",
                "model": DIARIZATION_MODEL,
                "error_type": type(exc).__name__,
            },
        )
        raise AppError(
            ErrorCode.MODEL_NOT_AVAILABLE,
            "Could not load the diarization model. Check HF_TOKEN and that the model "
            "terms are accepted on Hugging Face.",
        ) from None

    if pipeline is None:
        raise AppError(
            ErrorCode.MODEL_NOT_AVAILABLE,
            "Hugging Face returned no pipeline — the model terms are probably not accepted",
        )

    pipeline.to(_resolve_device(settings))
    logger.info("diarization model ready", extra={"stage": "diarization"})
    return pipeline


def _from_pretrained(pipeline_cls: Any, hf_token: str | None) -> Any:
    """`token` is the pyannote.audio 4.x argument; 3.x still calls it `use_auth_token`."""
    try:
        return pipeline_cls.from_pretrained(DIARIZATION_MODEL, token=hf_token)
    except TypeError:
        return pipeline_cls.from_pretrained(DIARIZATION_MODEL, use_auth_token=hf_token)


def get_diarization_pipeline(settings: Settings, hf_token: str | None) -> Any:
    """Process-wide singleton — the model is never reloaded per job or per chunk."""
    if DIARIZATION_MODEL not in _pipeline_cache:
        _pipeline_cache[DIARIZATION_MODEL] = load_diarization_pipeline(settings, hf_token)
    return _pipeline_cache[DIARIZATION_MODEL]


def reset_pipeline_cache() -> None:
    _pipeline_cache.clear()

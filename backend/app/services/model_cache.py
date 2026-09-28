"""Where model weights live on disk, and whether they are already there.

Pure filesystem logic with no ML imports, so the API image can report model status
without depending on torch or pyannote.
"""

from pathlib import Path

from app.core.config import Settings

DIARIZATION_MODEL = "pyannote/speaker-diarization-community-1"

# Directory layout inside MODELS_DIR (SPEC.md §8).
HF_CACHE_SUBDIR = "huggingface-cache"


def hf_cache_dir(settings: Settings) -> Path:
    return settings.models_dir / HF_CACHE_SUBDIR


def repo_cache_dir(settings: Settings, repo_id: str) -> Path:
    """Path huggingface_hub uses for a repo inside the cache."""
    return hf_cache_dir(settings) / f"models--{repo_id.replace('/', '--')}"


def is_model_cached(settings: Settings, repo_id: str = DIARIZATION_MODEL) -> bool:
    """True when weights are already on disk, so inference can run fully offline."""
    snapshots = repo_cache_dir(settings, repo_id) / "snapshots"
    return snapshots.is_dir() and any(snapshots.iterdir())

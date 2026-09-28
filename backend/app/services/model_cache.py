"""Where model weights live on disk, and whether they are already there.

Pure filesystem logic with no ML imports, so the API image can report model status
without depending on torch or pyannote.
"""

from pathlib import Path

from app.core.config import Settings

DIARIZATION_MODEL = "pyannote/speaker-diarization-community-1"
ASR_ENGINE = "gigaam"

# Directory layout inside MODELS_DIR (SPEC.md §8).
HF_CACHE_SUBDIR = "huggingface-cache"
GIGAAM_SUBDIR = "gigaam"

# GigaAM resolves these shorthands to their v3 checkpoint before downloading
# (see `_download_model` in the gigaam package). Mirrored here so the API image can tell
# whether the weights are on disk without importing gigaam or torch.
ASR_SHORT_NAMES = frozenset({"ctc", "rnnt", "e2e_ctc", "e2e_rnnt", "ssl"})


def hf_cache_dir(settings: Settings) -> Path:
    return settings.models_dir / HF_CACHE_SUBDIR


def gigaam_dir(settings: Settings) -> Path:
    """Where GigaAM checkpoints are downloaded (`download_root`)."""
    return settings.models_dir / GIGAAM_SUBDIR


def asr_checkpoint_name(model_name: str) -> str:
    name = model_name.strip()
    return f"v3_{name}" if name in ASR_SHORT_NAMES else name


def asr_checkpoint_path(settings: Settings, model_name: str | None = None) -> Path:
    name = asr_checkpoint_name(model_name or settings.asr_model)
    return gigaam_dir(settings) / f"{name}.ckpt"


def is_asr_model_cached(settings: Settings, model_name: str | None = None) -> bool:
    """True when the GigaAM checkpoint is already on disk, so inference needs no network."""
    name = (model_name or settings.asr_model).strip()
    # ASR_MODEL may also point at a local fine-tuned checkpoint — gigaam accepts a path.
    local = Path(name).expanduser()
    if local.is_file():
        return True
    path = asr_checkpoint_path(settings, name)
    return path.is_file() and path.stat().st_size > 0


def repo_cache_dir(settings: Settings, repo_id: str) -> Path:
    """Path huggingface_hub uses for a repo inside the cache."""
    return hf_cache_dir(settings) / f"models--{repo_id.replace('/', '--')}"


def is_model_cached(settings: Settings, repo_id: str = DIARIZATION_MODEL) -> bool:
    """True when weights are already on disk, so inference can run fully offline."""
    snapshots = repo_cache_dir(settings, repo_id) / "snapshots"
    return snapshots.is_dir() and any(snapshots.iterdir())

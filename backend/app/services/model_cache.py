"""Where model weights live on disk, and whether they are already there.

Pure filesystem logic with no ML imports, so the API image can report model status
without depending on torch or pyannote.
"""

from pathlib import Path

from app.core.config import Settings

DIARIZATION_MODEL = "pyannote/speaker-diarization-community-1"
# Exact commit of the model repository. Like the lock files (T13.3), it keeps a fresh install
# on the weights the results were checked with; `$model/...` sub-models inherit it. Bump it
# together with a `benchmark.equivalence` run.
DIARIZATION_REVISION = "3533c8cf8e369892e6b79ff1bf80f7b0286a54ee"
# Every file the pipeline loads from that revision. A download cut short leaves some of them
# missing, and the weights must then count as absent: the worker has no internet access to
# fetch the rest (T13.5), so the `models` service has to finish the job.
DIARIZATION_FILES = (
    "config.yaml",
    "segmentation/pytorch_model.bin",
    "embedding/pytorch_model.bin",
    "plda/plda.npz",
    "plda/xvec_transform.npz",
)
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


def asr_needs_tokenizer(checkpoint_name: str) -> bool:
    """Mirrors `_download_tokenizer` in gigaam: these models need a second file."""
    return checkpoint_name == "v1_rnnt" or "e2e" in checkpoint_name


def asr_tokenizer_path(settings: Settings, model_name: str | None = None) -> Path:
    name = asr_checkpoint_name(model_name or settings.asr_model)
    return gigaam_dir(settings) / f"{name}_tokenizer.model"


def is_asr_model_cached(settings: Settings, model_name: str | None = None) -> bool:
    """True when every GigaAM file is already on disk, so inference needs no network.

    The checkpoint alone is not enough: e2e models also need their tokenizer, which gigaam
    downloads as a second file. Interrupted between the two, the checkpoint looked complete,
    and the worker, with no internet access, failed every job on the missing tokenizer.
    """
    name = (model_name or settings.asr_model).strip()
    # ASR_MODEL may also point at a local fine-tuned checkpoint — gigaam accepts a path.
    local = Path(name).expanduser()
    if local.is_file():
        return True
    files = [asr_checkpoint_path(settings, name)]
    if asr_needs_tokenizer(asr_checkpoint_name(name)):
        files.append(asr_tokenizer_path(settings, name))
    return all(path.is_file() and path.stat().st_size > 0 for path in files)


def repo_cache_dir(settings: Settings, repo_id: str) -> Path:
    """Path huggingface_hub uses for a repo inside the cache."""
    return hf_cache_dir(settings) / f"models--{repo_id.replace('/', '--')}"


def diarization_snapshot_dir(settings: Settings) -> Path:
    """Where huggingface_hub keeps the pinned revision of the diarization model."""
    return repo_cache_dir(settings, DIARIZATION_MODEL) / "snapshots" / DIARIZATION_REVISION


def is_model_cached(settings: Settings) -> bool:
    """True when every file of the pinned diarization model is on disk, so inference can run
    fully offline. A snapshot holding only some of them is a download cut short."""
    snapshot = diarization_snapshot_dir(settings)
    return all((snapshot / name).is_file() for name in DIARIZATION_FILES)

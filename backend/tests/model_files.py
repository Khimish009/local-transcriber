"""Fake model weights on disk, laid out the way the cache checks expect them."""

from app.services.model_cache import (
    DIARIZATION_FILES,
    asr_checkpoint_name,
    asr_checkpoint_path,
    asr_needs_tokenizer,
    asr_tokenizer_path,
    diarization_snapshot_dir,
)


def cache_diarization(settings, missing: tuple[str, ...] = ()) -> None:
    """Every file of the pinned pyannote revision, except the `missing` ones."""
    snapshot = diarization_snapshot_dir(settings)
    for name in DIARIZATION_FILES:
        if name in missing:
            continue
        path = snapshot / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fake")


def cache_asr(settings, *, tokenizer: bool = True) -> None:
    """The GigaAM checkpoint, and its tokenizer when the model needs one."""
    checkpoint = asr_checkpoint_path(settings)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_bytes(b"ckpt")
    if tokenizer and asr_needs_tokenizer(asr_checkpoint_name(settings.asr_model)):
        asr_tokenizer_path(settings).write_bytes(b"tokenizer")

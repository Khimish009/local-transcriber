"""Speaker embeddings with one ResNet pass per window (TASKS.md T12.1).

pyannote's `SpeakerDiarization.get_embeddings` embeds every (window, local speaker) pair on
its own, so each 10-second window goes through the convolutional trunk of the WeSpeaker
ResNet three times — once per local speaker. The speaker mask only matters afterwards, in
statistics pooling, so the three passes compute the same thing. The model already accepts
`(batch, speakers, frames)` weights and pools every speaker from a single trunk pass; the
diarization pipeline just never asks for it. On CPU this step was ~70% of a whole job.

`shared_trunk_embeddings` is a drop-in replacement for that method: same masks, same output
layout, a third of the convolutions. Values differ from stock only by float32 rounding
(~1.5e-7): the fbank FFT rounds slightly differently depending on what else is in the batch,
and stock pyannote moves by as much when only `embedding_batch_size` changes. The resulting
diarization is identical — `python -m benchmark.equivalence` checks both on a real recording.
It overrides a pyannote internal, which is why `pyannote.audio` is pinned to an exact version
in requirements-worker.txt.

numpy and torch are imported lazily: the module is imported by code that the torch-free
test environment loads.
"""

import logging
import math
from types import MethodType
from typing import Any

logger = logging.getLogger(__name__)


def shared_trunk_embeddings(
    self: Any,
    file: Any,
    binary_segmentations: Any,
    exclude_overlap: bool = False,
    hook: Any = None,
) -> Any:
    """`SpeakerDiarization.get_embeddings` with one trunk pass per window.

    Returns a `(num_chunks, num_speakers, dimension)` array, exactly like the original.
    """
    if self.training:
        # Hyper-parameter tuning caches embeddings between trials; leave that path alone.
        return type(self).get_embeddings(self, file, binary_segmentations, exclude_overlap, hook)

    import numpy as np
    import torch

    embedding = self._embedding
    data = binary_segmentations.data
    num_chunks, num_frames, _num_speakers = data.shape

    if exclude_overlap:
        # Same rule as pyannote: drop overlapping frames from the mask, unless what is left
        # is too short to embed — then fall back to the whole speech of that speaker.
        num_samples = binary_segmentations.sliding_window.duration * embedding.sample_rate
        min_num_frames = math.ceil(num_frames * embedding.min_num_samples / num_samples)
        clean = data * (np.sum(data, axis=2, keepdims=True) < 2)
    else:
        min_num_frames = -1
        clean = data

    # Masks may contain NaN where partial windows were stitched.
    masks = np.nan_to_num(data, nan=0.0).astype(np.float32)
    clean = np.nan_to_num(clean, nan=0.0).astype(np.float32)
    use_clean = np.sum(clean, axis=1, keepdims=True) > min_num_frames
    # (chunks, frames, speakers) -> (chunks, speakers, frames): the model's weights layout.
    weights = np.ascontiguousarray(np.where(use_clean, clean, masks).transpose(0, 2, 1))

    batch_size = self.embedding_batch_size
    batch_count = math.ceil(num_chunks / batch_size)
    if hook is not None:
        hook("embeddings", None, total=batch_count, completed=0)

    batches: list[Any] = []
    waveforms: list[Any] = []
    first = 0
    for index, (chunk, _masks) in enumerate(binary_segmentations):
        waveform, _ = self._audio.crop(file, chunk, mode="pad")
        waveforms.append(waveform[None])
        if len(waveforms) < batch_size and index < num_chunks - 1:
            continue

        batch = embedding(
            torch.vstack(waveforms),
            masks=torch.from_numpy(weights[first : index + 1]),
        )
        batches.append(batch)
        waveforms.clear()
        first = index + 1
        if hook is not None:
            hook("embeddings", batch, total=batch_count, completed=len(batches))

    return np.concatenate(batches)


def install_shared_trunk_embeddings(pipeline: Any) -> bool:
    """Swap the pipeline's embedding step for `shared_trunk_embeddings`.

    Returns False and leaves the pipeline untouched when its embedding model is not the
    WeSpeaker ResNet this was written and verified for.
    """
    model = getattr(getattr(pipeline, "_embedding", None), "model_", None)
    resnet = getattr(model, "resnet", None)
    if resnet is None or not hasattr(pipeline, "get_embeddings"):
        logger.warning(
            "embedding model is not WeSpeaker ResNet, keeping pyannote's embedding step",
            extra={"stage": "diarization"},
        )
        return False

    import torch

    # Measured 1.4x on top of the shared trunk on CPU; stock pyannote with channels_last
    # produced exactly the same embeddings as without it.
    resnet.to(memory_format=torch.channels_last)
    pipeline.get_embeddings = MethodType(shared_trunk_embeddings, pipeline)
    logger.info("speaker embeddings: one ResNet pass per window", extra={"stage": "diarization"})
    return True

"""Speaker diarization stage (TASKS.md T3.4, T3.5).

Produces both the regular and the exclusive diarization. Speaker labels stay anonymous
(`SPEAKER_00`, ...) — identities are never invented (AGENTS.md rule 7).
"""

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.core.errors import AppError, ErrorCode
from app.schemas.diarization import DiarizationResult, SpeakerSegment
from app.services.model_cache import DIARIZATION_MODEL

logger = logging.getLogger(__name__)

# Segments shorter than this carry no usable speech and only add noise downstream.
MIN_SEGMENT_SECONDS = 0.05

# Where pyannote's own steps end within the stage, as a share of its time (T12.4). Measured
# on CPU after T12.1, on a 37-min recording (339 s): segmentation ~40%, speaker embeddings
# ~56%, clustering and reconstruction the rest.
SEGMENTATION_END = 0.40
EMBEDDINGS_END = 0.96

# Fraction of the diarization done, 0..1.
DiarizationProgress = Callable[[float], None]


class _ProgressFailed(Exception):
    """Carries an error raised by the progress callback out of pyannote, untouched.

    Without it a Redis blip or a deleted job inside the callback would surface as
    DIARIZATION_FAILED — a code the recording did not earn.
    """

    def __init__(self, error: Exception) -> None:
        super().__init__(str(error))
        self.error = error


def progress_hook(on_progress: DiarizationProgress) -> Callable[..., None]:
    """Turn pyannote's `hook` calls into a single fraction of the whole diarization.

    pyannote reports `completed`/`total` while segmenting and while embedding; other calls
    (step results, no counters) carry no progress. `completed` may overshoot `total` on
    the last segmentation batch.
    """

    def hook(
        step_name: str,
        step_artifact: Any,
        file: Any = None,
        total: int | None = None,
        completed: int | None = None,
    ) -> None:
        if not total or completed is None:
            return
        done = min(1.0, completed / total)
        if step_name == "segmentation":
            fraction = SEGMENTATION_END * done
        elif step_name == "embeddings":
            fraction = SEGMENTATION_END + (EMBEDDINGS_END - SEGMENTATION_END) * done
        else:
            return
        try:
            on_progress(fraction)
        except Exception as exc:
            raise _ProgressFailed(exc) from exc

    return hook


def annotation_to_segments(annotation: Any) -> list[SpeakerSegment]:
    """Convert a pyannote `Annotation` into plain, sorted, JSON-serializable segments."""
    if annotation is None:
        return []

    segments: list[SpeakerSegment] = []
    for segment, _track, label in annotation.itertracks(yield_label=True):
        start = float(segment.start)
        end = float(segment.end)
        if end - start < MIN_SEGMENT_SECONDS:
            continue
        segments.append(SpeakerSegment(start=round(start, 3), end=round(end, 3), speaker_id=label))

    segments.sort(key=lambda item: (item.start, item.end, item.speaker_id))
    return segments


def _extract_annotations(output: Any) -> tuple[Any, Any]:
    """community-1 returns an object with both diarizations; older pipelines return one."""
    regular = getattr(output, "speaker_diarization", output)
    exclusive = getattr(output, "exclusive_speaker_diarization", None)
    return regular, exclusive


def run_diarization(
    audio_path: Path,
    pipeline: Any,
    speaker_count: int | None = None,
    on_progress: DiarizationProgress | None = None,
) -> DiarizationResult:
    """Run the pipeline over the normalized WAV.

    `speaker_count` is the exact number of speakers when the user knows it, otherwise the
    pipeline decides on its own (SPEC.md §3.2). `on_progress` is called from inside the
    pipeline; whatever it raises (JOB_NOT_FOUND for a deleted job, a Redis error) stops the
    pipeline and passes through unchanged.
    """
    if not audio_path.is_file():
        raise AppError(ErrorCode.DIARIZATION_FAILED, "Normalized audio is missing")

    kwargs: dict[str, Any] = {}
    if speaker_count is not None:
        kwargs["num_speakers"] = speaker_count
    if on_progress is not None:
        kwargs["hook"] = progress_hook(on_progress)

    try:
        output = pipeline(str(audio_path), **kwargs)
    except _ProgressFailed as failure:
        raise failure.error from None
    except Exception as exc:
        logger.exception("diarization failed", extra={"stage": "diarization"})
        raise AppError(
            ErrorCode.DIARIZATION_FAILED,
            "Could not split the recording into speakers",
        ) from exc

    regular_annotation, exclusive_annotation = _extract_annotations(output)
    segments = annotation_to_segments(regular_annotation)
    exclusive = annotation_to_segments(exclusive_annotation) or segments

    if not segments:
        raise AppError(ErrorCode.DIARIZATION_FAILED, "No speech was detected in the recording")

    speakers = sorted({segment.speaker_id for segment in segments})
    logger.info(
        "diarization finished",
        extra={
            "stage": "diarization",
            "speakers": len(speakers),
            "segments": len(segments),
        },
    )

    return DiarizationResult(
        model=DIARIZATION_MODEL,
        speakers=speakers,
        segments=segments,
        exclusive=exclusive,
    )

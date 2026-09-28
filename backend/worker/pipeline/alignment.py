"""Word-to-speaker alignment and transcript assembly (TASKS.md T5.1-T5.4).

Pure, deterministic logic — no ML imports, no I/O. Everything here works on the global
audio timeline in seconds (AGENTS.md "ML integration rules").
"""

import logging
from bisect import bisect_right
from datetime import UTC, datetime

from app.schemas.asr import AsrResult, AsrWord
from app.schemas.diarization import DiarizationResult, SpeakerSegment
from app.schemas.transcript import (
    EngineInfo,
    Transcript,
    TranscriptSegment,
    TranscriptSource,
    TranscriptSpeaker,
    TranscriptWord,
)

logger = logging.getLogger(__name__)

SPEAKER_DISPLAY_PREFIX = "Спикер"


def default_display_name(position: int) -> str:
    """`SPEAKER_00` -> "Спикер 1". The user may rename it later (SPEC.md §6)."""
    return f"{SPEAKER_DISPLAY_PREFIX} {position + 1}"


def _distance(midpoint: float, segment: SpeakerSegment) -> float:
    """0 when the midpoint is inside the segment, otherwise the gap to its nearest edge."""
    return max(segment.start - midpoint, midpoint - segment.end, 0.0)


def speaker_at(
    midpoint: float,
    segments: list[SpeakerSegment],
    starts: list[float],
    tolerance_seconds: float,
) -> str | None:
    """SPEC.md §3.4 — the exclusive speaker interval containing the word midpoint.

    With no exact match, the nearest interval within `tolerance_seconds` wins; beyond that
    the word stays unattributed. A speaker is never invented.
    """
    if not segments:
        return None

    # The exclusive diarization does not overlap, so the answer is always a direct
    # neighbour of the insertion point.
    index = bisect_right(starts, midpoint) - 1
    candidates = [
        segments[position]
        for position in range(index - 1, index + 2)
        if 0 <= position < len(segments)
    ]

    for segment in candidates:
        # Half-open: touching intervals hand the shared instant to the later speaker.
        if segment.start <= midpoint < segment.end:
            return segment.speaker_id

    nearest = min(candidates, key=lambda segment: _distance(midpoint, segment), default=None)
    if nearest is None or _distance(midpoint, nearest) > tolerance_seconds:
        return None
    return nearest.speaker_id


def assign_speakers(
    words: list[AsrWord],
    segments: list[SpeakerSegment],
    tolerance_seconds: float,
) -> list[TranscriptWord]:
    """T5.1/T5.2 — attach a speaker to every recognized word."""
    ordered = sorted(segments, key=lambda segment: segment.start)
    starts = [segment.start for segment in ordered]

    assigned = [
        TranscriptWord(
            start=word.start,
            end=word.end,
            text=word.text,
            speaker_id=speaker_at((word.start + word.end) / 2, ordered, starts, tolerance_seconds),
        )
        for word in words
    ]

    unattributed = sum(1 for word in assigned if word.speaker_id is None)
    if unattributed:
        logger.info(
            "words without a speaker",
            extra={"stage": "alignment", "words": len(assigned), "unattributed": unattributed},
        )
    return assigned


def build_segments(
    words: list[TranscriptWord],
    max_block_seconds: float,
    block_gap_seconds: float,
) -> list[TranscriptSegment]:
    """T5.3 — group words into readable blocks (SPEC.md §3.5).

    A new block starts on a speaker change, on a long pause, or when the block would grow
    past `max_block_seconds` — long monologues must not collapse into one paragraph.
    """
    if max_block_seconds <= 0:
        raise ValueError("max_block_seconds must be positive")

    blocks: list[list[TranscriptWord]] = []
    for word in words:
        if blocks:
            current = blocks[-1]
            previous = current[-1]
            same_speaker = word.speaker_id == previous.speaker_id
            short_pause = word.start - previous.end < block_gap_seconds
            fits = word.end - current[0].start <= max_block_seconds
            if same_speaker and short_pause and fits:
                current.append(word)
                continue
        blocks.append([word])

    return [
        TranscriptSegment(
            id=f"seg-{index + 1:03d}",
            start=block[0].start,
            end=max(word.end for word in block),
            speaker_id=block[0].speaker_id,
            text=" ".join(word.text for word in block),
        )
        for index, block in enumerate(blocks)
    ]


def build_speakers(
    diarization: DiarizationResult,
    words: list[TranscriptWord],
) -> list[TranscriptSpeaker]:
    """Every diarized speaker, plus anything that somehow only shows up on a word."""
    known = set(diarization.speakers)
    known.update(word.speaker_id for word in words if word.speaker_id is not None)
    return [
        TranscriptSpeaker(id=speaker_id, display_name=default_display_name(position))
        for position, speaker_id in enumerate(sorted(known))
    ]


def build_transcript(
    job_id: str,
    filename: str,
    duration_seconds: float | None,
    asr: AsrResult,
    diarization: DiarizationResult,
    tolerance_seconds: float,
    max_block_seconds: float,
    block_gap_seconds: float,
) -> Transcript:
    """T5.4 — assemble the canonical result."""
    words = assign_speakers(
        asr.words,
        diarization.exclusive or diarization.segments,
        tolerance_seconds,
    )
    segments = build_segments(words, max_block_seconds, block_gap_seconds)

    logger.info(
        "alignment finished",
        extra={"stage": "alignment", "words": len(words), "segments": len(segments)},
    )

    return Transcript(
        job_id=job_id,
        source=TranscriptSource(filename=filename, duration_seconds=duration_seconds),
        asr=EngineInfo(engine=asr.engine, model=asr.model),
        diarization=EngineInfo(engine=diarization.engine, model=diarization.model),
        speakers=build_speakers(diarization, words),
        words=words,
        segments=segments,
        created_at=datetime.now(UTC),
    )

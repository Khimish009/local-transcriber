"""T4.2 — chunk geometry. Pure logic, no model involved."""

import pytest

from app.schemas.diarization import SpeakerSegment
from worker.pipeline.asr import build_asr_chunks, merge_speech_intervals

GAP = 0.4  # MERGE_SILENCE_GAP_MS=400
MAX = 20.0  # MAX_ASR_CHUNK_SECONDS=20


def seg(start: float, end: float, speaker: str = "SPEAKER_00") -> SpeakerSegment:
    return SpeakerSegment(start=start, end=end, speaker_id=speaker)


def build(segments, max_seconds=MAX, gap=GAP, total=None):
    return build_asr_chunks(segments, max_seconds, gap, total_duration=total)


def test_no_speech_produces_no_chunks() -> None:
    assert build([]) == []


def test_overlapping_speakers_collapse_into_one_interval() -> None:
    merged = merge_speech_intervals([seg(1.0, 5.0, "SPEAKER_00"), seg(4.0, 7.0, "SPEAKER_01")])

    assert merged == [[1.0, 7.0]]


def test_short_pauses_are_merged_into_a_single_chunk() -> None:
    # 0.2 s of silence is below the 0.4 s merge gap.
    chunks = build([seg(1.0, 3.0), seg(3.2, 5.0)])

    assert len(chunks) == 1
    assert chunks[0].start == pytest.approx(0.8)
    assert chunks[0].end == pytest.approx(5.2)


def test_long_pauses_split_chunks() -> None:
    chunks = build([seg(1.0, 3.0), seg(10.0, 12.0)])

    assert len(chunks) == 2
    assert chunks[0].end < chunks[1].start, "padding must never make chunks overlap"


def test_speaker_turns_do_not_split_a_chunk() -> None:
    """SPEC.md §3.3: ASR must not be tied to speaker-turn boundaries."""
    chunks = build([seg(0.0, 2.0, "SPEAKER_00"), seg(2.0, 4.0, "SPEAKER_01")])

    assert len(chunks) == 1


def test_long_speech_is_capped_at_the_configured_maximum() -> None:
    chunks = build([seg(0.0, 95.0)], max_seconds=MAX)

    assert len(chunks) == 5
    assert all(chunk.duration <= MAX + 1e-6 for chunk in chunks)
    assert all(chunk.duration > 1.0 for chunk in chunks), "no sliver parts"


def test_split_parts_are_contiguous_and_keep_the_global_offset() -> None:
    chunks = build([seg(30.0, 90.0)], max_seconds=MAX)

    assert chunks[0].start == pytest.approx(29.8)
    assert chunks[-1].end == pytest.approx(90.2)
    for previous, current in zip(chunks, chunks[1:], strict=False):
        assert current.start == pytest.approx(previous.end)


def test_indexes_are_sequential() -> None:
    chunks = build([seg(0.0, 50.0), seg(80.0, 100.0)])

    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))


def test_padding_is_clamped_to_the_recording() -> None:
    chunks = build([seg(0.0, 10.0)], total=10.1)

    assert chunks[0].start == 0.0
    assert chunks[0].end == pytest.approx(10.1)


def test_micro_segments_do_not_become_chunks() -> None:
    assert build([seg(1.0, 1.02)], gap=0.0) == []


def test_thousands_of_micro_segments_collapse_into_few_chunks() -> None:
    """A minute of speech cut into 0.3 s pieces must not become hundreds of chunks."""
    segments = [seg(i * 0.5, i * 0.5 + 0.3) for i in range(120)]

    chunks = build(segments)

    assert len(chunks) == 3


def test_invalid_maximum_is_rejected() -> None:
    with pytest.raises(ValueError):
        build([seg(0.0, 5.0)], max_seconds=0)

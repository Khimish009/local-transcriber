"""T5.1-T5.4 — speaker assignment, block grouping and the canonical transcript."""

import pytest

from app.schemas.asr import AsrResult, AsrWord
from app.schemas.diarization import DiarizationResult, SpeakerSegment
from app.schemas.transcript import TranscriptWord
from worker.pipeline.alignment import (
    assign_speakers,
    build_segments,
    build_transcript,
    default_display_name,
    speaker_at,
)

TOLERANCE = 0.25
MAX_BLOCK = 40.0
BLOCK_GAP = 1.5

# SPEAKER_00 talks 0-10, SPEAKER_01 talks 10-20, silence 20-30, SPEAKER_00 again 30-40.
EXCLUSIVE = [
    SpeakerSegment(start=0.0, end=10.0, speaker_id="SPEAKER_00"),
    SpeakerSegment(start=10.0, end=20.0, speaker_id="SPEAKER_01"),
    SpeakerSegment(start=30.0, end=40.0, speaker_id="SPEAKER_00"),
]


def word(start: float, end: float, text: str = "слово") -> AsrWord:
    return AsrWord(start=start, end=end, text=text)


def tword(start: float, end: float, speaker: str | None, text: str = "слово") -> TranscriptWord:
    return TranscriptWord(start=start, end=end, text=text, speaker_id=speaker)


def at(midpoint: float, tolerance: float = TOLERANCE) -> str | None:
    starts = [segment.start for segment in EXCLUSIVE]
    return speaker_at(midpoint, EXCLUSIVE, starts, tolerance)


# --- T5.1 midpoint assignment --------------------------------------------------------


def test_midpoint_inside_an_interval_picks_that_speaker() -> None:
    assert at(5.0) == "SPEAKER_00"
    assert at(15.0) == "SPEAKER_01"
    assert at(35.0) == "SPEAKER_00"


def test_assignment_uses_the_midpoint_not_the_start() -> None:
    """A word starting just before a turn change belongs to whoever owns its midpoint."""
    words = assign_speakers([word(9.8, 10.6)], EXCLUSIVE, TOLERANCE)

    assert words[0].speaker_id == "SPEAKER_01"


def test_interval_boundary_belongs_to_the_later_speaker() -> None:
    assert at(10.0) == "SPEAKER_01"


def test_first_and_last_interval_edges_are_covered() -> None:
    assert at(0.0) == "SPEAKER_00"
    assert at(39.9) == "SPEAKER_00"


# --- T5.2 tolerance ------------------------------------------------------------------


def test_word_just_outside_an_interval_is_pulled_in_by_tolerance() -> None:
    assert at(20.1) == "SPEAKER_01"
    assert at(29.9) == "SPEAKER_00"


def test_word_deep_in_silence_stays_unattributed() -> None:
    assert at(25.0) is None


def test_tolerance_is_configurable() -> None:
    assert at(20.4, tolerance=0.25) is None
    assert at(20.4, tolerance=1.0) == "SPEAKER_01"


def test_zero_tolerance_keeps_only_exact_matches() -> None:
    assert at(20.05, tolerance=0.0) is None
    assert at(19.0, tolerance=0.0) == "SPEAKER_01"


def test_no_diarization_means_no_speaker() -> None:
    assert speaker_at(5.0, [], [], TOLERANCE) is None


def test_speaker_is_never_invented() -> None:
    words = assign_speakers([word(24.0, 26.0)], EXCLUSIVE, TOLERANCE)

    assert words[0].speaker_id is None


# --- T5.3 blocks ---------------------------------------------------------------------


def build(words, max_block=MAX_BLOCK, gap=BLOCK_GAP):
    return build_segments(words, max_block, gap)


def test_speaker_change_starts_a_new_block() -> None:
    segments = build(
        [
            tword(0.0, 1.0, "SPEAKER_00", "привет"),
            tword(1.1, 2.0, "SPEAKER_01", "здравствуйте"),
        ]
    )

    assert [segment.speaker_id for segment in segments] == ["SPEAKER_00", "SPEAKER_01"]
    assert segments[0].text == "привет"


def test_long_pause_starts_a_new_block() -> None:
    segments = build([tword(0.0, 1.0, "SPEAKER_00"), tword(5.0, 6.0, "SPEAKER_00")])

    assert len(segments) == 2


def test_short_pause_keeps_one_block() -> None:
    segments = build([tword(0.0, 1.0, "SPEAKER_00"), tword(1.4, 2.0, "SPEAKER_00")])

    assert len(segments) == 1
    assert segments[0].start == 0.0
    assert segments[0].end == 2.0


def test_long_monologue_is_split_by_max_block_size() -> None:
    words = [tword(i * 0.5, i * 0.5 + 0.4, "SPEAKER_00") for i in range(200)]

    segments = build(words, max_block=40.0)

    assert len(segments) > 2
    assert all(segment.end - segment.start <= 40.0 for segment in segments)


def test_unattributed_words_group_together() -> None:
    segments = build([tword(0.0, 1.0, None), tword(1.2, 2.0, None)])

    assert len(segments) == 1
    assert segments[0].speaker_id is None


def test_block_ids_are_sequential() -> None:
    words = [tword(i * 3.0, i * 3.0 + 1.0, "SPEAKER_00") for i in range(3)]

    assert [segment.id for segment in build(words)] == ["seg-001", "seg-002", "seg-003"]


def test_block_text_joins_the_words() -> None:
    segments = build(
        [
            tword(0.0, 0.5, "SPEAKER_00", "Добрый"),
            tword(0.6, 1.0, "SPEAKER_00", "день."),
        ]
    )

    assert segments[0].text == "Добрый день."


def test_no_words_means_no_blocks() -> None:
    assert build([]) == []


def test_invalid_block_size_is_rejected() -> None:
    with pytest.raises(ValueError):
        build([tword(0.0, 1.0, "SPEAKER_00")], max_block=0)


# --- T5.4 canonical transcript -------------------------------------------------------


@pytest.fixture
def transcript():
    asr = AsrResult(
        engine="gigaam",
        model="v3_e2e_rnnt",
        chunks=[],
        words=[
            word(1.0, 1.4, "Добрый"),
            word(1.5, 2.0, "день."),
            word(12.0, 12.6, "Слушаю."),
            word(25.0, 25.5, "Шум"),
        ],
    )
    diarization = DiarizationResult(
        engine="pyannote",
        model="pyannote/speaker-diarization-community-1",
        speakers=["SPEAKER_00", "SPEAKER_01"],
        segments=[],
        exclusive=EXCLUSIVE,
    )
    return build_transcript(
        job_id="11111111-1111-1111-1111-111111111111",
        filename="meeting.m4a",
        duration_seconds=3522.41,
        asr=asr,
        diarization=diarization,
        tolerance_seconds=TOLERANCE,
        max_block_seconds=MAX_BLOCK,
        block_gap_seconds=BLOCK_GAP,
    )


def test_transcript_carries_source_metadata(transcript) -> None:
    assert transcript.version == 1
    assert transcript.language == "ru"
    assert transcript.source.filename == "meeting.m4a"
    assert transcript.source.duration_seconds == 3522.41


def test_transcript_records_both_models(transcript) -> None:
    assert transcript.asr.engine == "gigaam"
    assert transcript.asr.model == "v3_e2e_rnnt"
    assert transcript.diarization.engine == "pyannote"
    assert "speaker-diarization-community-1" in transcript.diarization.model


def test_transcript_speakers_get_default_display_names(transcript) -> None:
    assert [speaker.id for speaker in transcript.speakers] == ["SPEAKER_00", "SPEAKER_01"]
    assert [speaker.display_name for speaker in transcript.speakers] == ["Спикер 1", "Спикер 2"]


def test_transcript_words_carry_speakers_and_nulls(transcript) -> None:
    assert [w.speaker_id for w in transcript.words] == [
        "SPEAKER_00",
        "SPEAKER_00",
        "SPEAKER_01",
        None,
    ]


def test_transcript_segments_follow_speaker_changes(transcript) -> None:
    assert [segment.speaker_id for segment in transcript.segments] == [
        "SPEAKER_00",
        "SPEAKER_01",
        None,
    ]
    assert transcript.segments[0].text == "Добрый день."


def test_display_name_numbering_is_one_based() -> None:
    assert default_display_name(0) == "Спикер 1"
    assert default_display_name(4) == "Спикер 5"

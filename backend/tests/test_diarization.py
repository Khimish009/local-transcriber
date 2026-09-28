import json
from dataclasses import dataclass

import pytest

from app.core.errors import AppError, ErrorCode
from app.schemas.diarization import DiarizationResult
from worker.pipeline.diarization import annotation_to_segments, run_diarization


@dataclass
class FakeSegment:
    start: float
    end: float


class FakeAnnotation:
    """Mimics the part of pyannote's `Annotation` API that the pipeline stage uses."""

    def __init__(self, tracks: list[tuple[float, float, str]]) -> None:
        self._tracks = tracks

    def itertracks(self, yield_label: bool = False):
        for index, (start, end, label) in enumerate(self._tracks):
            yield FakeSegment(start, end), f"track{index}", label


class FakeOutput:
    def __init__(self, regular: FakeAnnotation, exclusive: FakeAnnotation | None) -> None:
        self.speaker_diarization = regular
        self.exclusive_speaker_diarization = exclusive


class FakePipeline:
    def __init__(self, output, raises: Exception | None = None) -> None:
        self.output = output
        self.raises = raises
        self.calls: list[dict] = []

    def __call__(self, path, **kwargs):
        self.calls.append({"path": path, **kwargs})
        if self.raises is not None:
            raise self.raises
        return self.output


REGULAR = [(0.0, 3.4, "SPEAKER_00"), (3.0, 6.2, "SPEAKER_01"), (6.5, 9.0, "SPEAKER_00")]
EXCLUSIVE = [(0.0, 3.2, "SPEAKER_00"), (3.2, 6.2, "SPEAKER_01"), (6.5, 9.0, "SPEAKER_00")]


@pytest.fixture
def normalized_wav(tmp_path):
    path = tmp_path / "normalized.wav"
    path.write_bytes(b"RIFF fake wav")
    return path


def test_annotation_to_segments_sorts_and_rounds() -> None:
    annotation = FakeAnnotation([(3.00049, 6.2, "SPEAKER_01"), (0.0, 3.4, "SPEAKER_00")])

    segments = annotation_to_segments(annotation)

    assert [s.speaker_id for s in segments] == ["SPEAKER_00", "SPEAKER_01"]
    assert segments[1].start == 3.0


def test_annotation_to_segments_drops_zero_length_tracks() -> None:
    annotation = FakeAnnotation([(1.0, 1.0, "SPEAKER_00"), (2.0, 4.0, "SPEAKER_01")])

    assert [s.speaker_id for s in annotation_to_segments(annotation)] == ["SPEAKER_01"]


def test_annotation_to_segments_handles_missing_annotation() -> None:
    assert annotation_to_segments(None) == []


def test_run_diarization_keeps_both_annotations(normalized_wav) -> None:
    pipeline = FakePipeline(FakeOutput(FakeAnnotation(REGULAR), FakeAnnotation(EXCLUSIVE)))

    result = run_diarization(normalized_wav, pipeline)

    assert isinstance(result, DiarizationResult)
    assert result.speakers == ["SPEAKER_00", "SPEAKER_01"]
    assert len(result.segments) == 3
    assert result.exclusive[0].end == 3.2, "exclusive diarization must not be the regular one"
    assert result.engine == "pyannote"
    assert "community-1" in result.model


def test_exclusive_has_no_overlaps(normalized_wav) -> None:
    pipeline = FakePipeline(FakeOutput(FakeAnnotation(REGULAR), FakeAnnotation(EXCLUSIVE)))

    exclusive = run_diarization(normalized_wav, pipeline).exclusive

    for earlier, later in zip(exclusive, exclusive[1:], strict=False):
        assert earlier.end <= later.start


def test_falls_back_to_regular_when_exclusive_is_absent(normalized_wav) -> None:
    pipeline = FakePipeline(FakeOutput(FakeAnnotation(REGULAR), None))

    result = run_diarization(normalized_wav, pipeline)

    assert result.exclusive == result.segments


def test_speaker_count_is_passed_through(normalized_wav) -> None:
    pipeline = FakePipeline(FakeOutput(FakeAnnotation(REGULAR), FakeAnnotation(EXCLUSIVE)))

    run_diarization(normalized_wav, pipeline, speaker_count=3)

    assert pipeline.calls[0]["num_speakers"] == 3


def test_auto_speaker_count_leaves_the_pipeline_to_decide(normalized_wav) -> None:
    pipeline = FakePipeline(FakeOutput(FakeAnnotation(REGULAR), FakeAnnotation(EXCLUSIVE)))

    run_diarization(normalized_wav, pipeline, speaker_count=None)

    assert "num_speakers" not in pipeline.calls[0]


def test_silent_recording_fails_with_diarization_code(normalized_wav) -> None:
    pipeline = FakePipeline(FakeOutput(FakeAnnotation([]), FakeAnnotation([])))

    with pytest.raises(AppError) as exc:
        run_diarization(normalized_wav, pipeline)

    assert exc.value.code is ErrorCode.DIARIZATION_FAILED


def test_pipeline_error_is_wrapped_not_swallowed(normalized_wav) -> None:
    pipeline = FakePipeline(None, raises=RuntimeError("cuda oom"))

    with pytest.raises(AppError) as exc:
        run_diarization(normalized_wav, pipeline)

    assert exc.value.code is ErrorCode.DIARIZATION_FAILED
    assert "cuda oom" not in exc.value.message


def test_missing_normalized_audio_fails(tmp_path) -> None:
    pipeline = FakePipeline(FakeOutput(FakeAnnotation(REGULAR), None))

    with pytest.raises(AppError) as exc:
        run_diarization(tmp_path / "nope.wav", pipeline)

    assert exc.value.code is ErrorCode.DIARIZATION_FAILED


def test_result_serializes_to_json(normalized_wav) -> None:
    pipeline = FakePipeline(FakeOutput(FakeAnnotation(REGULAR), FakeAnnotation(EXCLUSIVE)))

    payload = json.loads(run_diarization(normalized_wav, pipeline).model_dump_json())

    assert set(payload) == {"engine", "model", "speakers", "segments", "exclusive"}
    assert payload["segments"][0]["speaker_id"] == "SPEAKER_00"

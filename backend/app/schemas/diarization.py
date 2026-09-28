from pydantic import BaseModel, Field


class SpeakerSegment(BaseModel):
    start: float
    end: float
    speaker_id: str

    @property
    def duration(self) -> float:
        return self.end - self.start


class DiarizationResult(BaseModel):
    """Intermediate artifact stored as work/diarization.json.

    `exclusive` has exactly one active speaker at any instant and is what word-level
    speaker assignment uses (SPEC.md §3.2). `segments` keeps the regular diarization,
    which may overlap, for debugging and later analysis.
    """

    engine: str = "pyannote"
    model: str
    speakers: list[str] = Field(default_factory=list)
    segments: list[SpeakerSegment] = Field(default_factory=list)
    exclusive: list[SpeakerSegment] = Field(default_factory=list)

    @property
    def speech_duration(self) -> float:
        return sum(segment.duration for segment in self.exclusive)

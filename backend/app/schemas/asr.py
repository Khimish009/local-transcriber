from pydantic import BaseModel, Field


class AsrChunk(BaseModel):
    """One slice of the recording handed to the ASR model.

    Boundaries are on the global audio timeline in seconds, so word timestamps coming back
    from the model only need `+ start` to become global (SPEC.md §3.3).
    """

    index: int
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


class AsrWord(BaseModel):
    start: float
    end: float
    text: str


class AsrResult(BaseModel):
    """Intermediate artifact stored as work/asr_words.json.

    Speaker assignment happens later (Phase 5) — words here carry no speaker yet.
    """

    engine: str = "gigaam"
    model: str
    chunks: list[AsrChunk] = Field(default_factory=list)
    words: list[AsrWord] = Field(default_factory=list)

    @property
    def transcribed_duration(self) -> float:
        return sum(chunk.duration for chunk in self.chunks)

"""The canonical result model (SPEC.md §4).

`result/transcript.json` is the source of truth. TXT/DOCX/PDF are views generated from it
(AGENTS.md rule 5), and renaming a speaker only touches this file — it never re-runs
diarization or ASR.

Lives in `app/` rather than `worker/` because both the API image and the worker read it.
"""

from datetime import datetime

from pydantic import BaseModel, Field

TRANSCRIPT_VERSION = 1


class TranscriptSource(BaseModel):
    filename: str
    duration_seconds: float | None = None


class EngineInfo(BaseModel):
    """Which engine and weights produced a part of the result."""

    engine: str
    model: str


class TranscriptSpeaker(BaseModel):
    """`id` stays the anonymous diarization label; only `display_name` is user-editable."""

    id: str
    display_name: str


class TranscriptWord(BaseModel):
    start: float
    end: float
    text: str
    # None when the word could not be attributed confidently — never guessed
    # (AGENTS.md rule 7, SPEC.md §3.4).
    speaker_id: str | None = None


class TranscriptSegment(BaseModel):
    """A readable block: one speaker, one continuous stretch of speech."""

    id: str
    start: float
    end: float
    speaker_id: str | None
    text: str


class Transcript(BaseModel):
    version: int = TRANSCRIPT_VERSION
    job_id: str
    source: TranscriptSource
    language: str = "ru"
    asr: EngineInfo
    diarization: EngineInfo
    speakers: list[TranscriptSpeaker] = Field(default_factory=list)
    words: list[TranscriptWord] = Field(default_factory=list)
    segments: list[TranscriptSegment] = Field(default_factory=list)
    created_at: datetime

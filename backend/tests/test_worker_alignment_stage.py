import json

import pytest

from app.core.errors import AppError, ErrorCode
from app.schemas.asr import AsrResult, AsrWord
from app.schemas.audio import AudioMetadata
from app.schemas.diarization import DiarizationResult, SpeakerSegment
from app.schemas.job import STAGE_PROGRESS_RANGE, JobSource, JobStatus
from worker.jobs import align, process_job

EXCLUSIVE = [
    SpeakerSegment(start=0.0, end=10.0, speaker_id="SPEAKER_00"),
    SpeakerSegment(start=10.0, end=20.0, speaker_id="SPEAKER_01"),
]
WORDS = [
    AsrWord(start=1.0, end=1.4, text="Добрый"),
    AsrWord(start=1.5, end=2.0, text="день."),
    AsrWord(start=12.0, end=12.6, text="Слушаю."),
]


def write_artifacts(storage, job_id, words=None, exclusive=None) -> None:
    storage.prepare_job_dirs(job_id)
    diarization = DiarizationResult(
        model="pyannote/speaker-diarization-community-1",
        speakers=["SPEAKER_00", "SPEAKER_01"],
        segments=[],
        exclusive=EXCLUSIVE if exclusive is None else exclusive,
    )
    storage.diarization_path(job_id).write_text(diarization.model_dump_json(), encoding="utf-8")
    asr = AsrResult(model="v3_e2e_rnnt", chunks=[], words=WORDS if words is None else words)
    storage.asr_words_path(job_id).write_text(asr.model_dump_json(), encoding="utf-8")


@pytest.fixture
def worker_env(monkeypatch, redis, settings, storage):
    """Real alignment stage; the ML stages only drop their artifacts on disk."""
    monkeypatch.setattr("worker.jobs.get_redis", lambda: redis)
    monkeypatch.setattr("worker.jobs.get_settings", lambda: settings)
    monkeypatch.setattr("worker.jobs.prepare_audio", lambda *args, **kwargs: None)
    monkeypatch.setattr("worker.jobs.diarize", lambda *args, **kwargs: None)

    def fake_transcribe(job_id, store, storage_, settings_):
        write_artifacts(storage_, job_id)

    monkeypatch.setattr("worker.jobs.transcribe", fake_transcribe)
    monkeypatch.setattr("worker.jobs.generate_exports", lambda *args, **kwargs: None)


def make_job(store, filename="meeting.m4a") -> str:
    job = store.create(JobSource(filename=filename, size_bytes=10))
    record = store.require(job.job_id)
    record.audio = AudioMetadata(duration_seconds=3522.41)
    store.replace(record)
    return job.job_id


def read_transcript(storage, job_id) -> dict:
    return json.loads(storage.transcript_path(job_id).read_text(encoding="utf-8"))


def test_stage_writes_canonical_transcript(worker_env, store, storage) -> None:
    job_id = make_job(store)

    process_job(job_id)

    payload = read_transcript(storage, job_id)
    assert payload["version"] == 1
    assert payload["job_id"] == job_id
    assert payload["language"] == "ru"
    assert payload["source"] == {"filename": "meeting.m4a", "duration_seconds": 3522.41}
    assert payload["asr"] == {"engine": "gigaam", "model": "v3_e2e_rnnt"}
    assert payload["diarization"]["engine"] == "pyannote"
    assert payload["speakers"] == [
        {"id": "SPEAKER_00", "display_name": "Спикер 1"},
        {"id": "SPEAKER_01", "display_name": "Спикер 2"},
    ]
    assert store.get(job_id).status is JobStatus.COMPLETED


def test_transcript_lands_in_the_result_directory(worker_env, store, storage) -> None:
    job_id = make_job(store)

    process_job(job_id)

    path = storage.transcript_path(job_id)
    assert path.parent == storage.result_dir(job_id)
    assert path.name == "transcript.json"


def test_words_and_segments_carry_speakers(worker_env, store, storage) -> None:
    job_id = make_job(store)

    process_job(job_id)

    payload = read_transcript(storage, job_id)
    assert [word["speaker_id"] for word in payload["words"]] == [
        "SPEAKER_00",
        "SPEAKER_00",
        "SPEAKER_01",
    ]
    assert [segment["text"] for segment in payload["segments"]] == ["Добрый день.", "Слушаю."]
    assert payload["segments"][0]["id"] == "seg-001"


def test_intermediate_artifacts_are_kept(worker_env, store, storage) -> None:
    """Debugging must not require rerunning expensive inference (AGENTS.md)."""
    job_id = make_job(store)

    process_job(job_id)

    assert storage.diarization_path(job_id).is_file()
    assert storage.asr_words_path(job_id).is_file()


def test_tolerance_setting_reaches_the_stage(store, storage, settings) -> None:
    job_id = make_job(store)
    # Midpoint 20.3 s sits 0.3 s past the last speaker interval.
    write_artifacts(storage, job_id, words=[AsrWord(start=20.0, end=20.6, text="ага")])

    align(job_id, store, storage, settings.model_copy(update={"speaker_match_tolerance_ms": 50}))
    assert read_transcript(storage, job_id)["words"][0]["speaker_id"] is None

    align(job_id, store, storage, settings.model_copy(update={"speaker_match_tolerance_ms": 500}))
    assert read_transcript(storage, job_id)["words"][0]["speaker_id"] == "SPEAKER_01"


def test_block_gap_setting_reaches_the_stage(store, storage, settings) -> None:
    job_id = make_job(store)
    write_artifacts(
        storage,
        job_id,
        words=[
            AsrWord(start=1.0, end=1.4, text="раз"),
            AsrWord(start=3.0, end=3.4, text="два"),
        ],
    )

    align(job_id, store, storage, settings.model_copy(update={"block_silence_gap_ms": 5000}))
    assert len(read_transcript(storage, job_id)["segments"]) == 1

    align(job_id, store, storage, settings.model_copy(update={"block_silence_gap_ms": 500}))
    assert len(read_transcript(storage, job_id)["segments"]) == 2


def test_progress_stays_inside_the_aligning_window(worker_env, store, monkeypatch) -> None:
    job_id = make_job(store)
    start, end = STAGE_PROGRESS_RANGE[JobStatus.ALIGNING]
    seen: list[int] = []
    original = store.__class__.update

    def record(self, job_id_, **kwargs):
        job = original(self, job_id_, **kwargs)
        if job.status is JobStatus.ALIGNING:
            seen.append(job.progress)
        return job

    monkeypatch.setattr(store.__class__, "update", record)

    process_job(job_id)

    assert seen[0] == start
    assert seen[-1] == end
    assert seen == sorted(seen)


def test_missing_recognition_artifact_fails_the_stage(store, storage, settings) -> None:
    job_id = make_job(store)
    write_artifacts(storage, job_id)
    storage.asr_words_path(job_id).unlink()

    with pytest.raises(AppError) as exc:
        align(job_id, store, storage, settings)

    assert exc.value.code is ErrorCode.ASR_FAILED


def test_missing_diarization_artifact_fails_the_stage(store, storage, settings) -> None:
    job_id = make_job(store)
    write_artifacts(storage, job_id)
    storage.diarization_path(job_id).unlink()

    with pytest.raises(AppError) as exc:
        align(job_id, store, storage, settings)

    assert exc.value.code is ErrorCode.ASR_FAILED


def test_transcript_round_trips_through_the_schema(worker_env, store, storage) -> None:
    from app.schemas.transcript import Transcript

    job_id = make_job(store)
    process_job(job_id)

    reloaded = Transcript.model_validate_json(
        storage.transcript_path(job_id).read_text(encoding="utf-8")
    )

    assert reloaded.job_id == job_id
    assert reloaded.segments

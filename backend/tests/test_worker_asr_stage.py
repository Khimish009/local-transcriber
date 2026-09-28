import json

import pytest

from app.core.errors import AppError, ErrorCode
from app.schemas.audio import AudioMetadata
from app.schemas.diarization import DiarizationResult, SpeakerSegment
from app.schemas.job import STAGE_PROGRESS_RANGE, JobSource, JobStatus
from tests.test_asr import FakeAsrModel, write_wav
from worker.jobs import process_job, transcribe

DURATION = 40.0


@pytest.fixture
def worker_env(monkeypatch, redis, settings, storage):
    """Real ASR stage, fake model — the earlier stages have their own tests."""
    monkeypatch.setattr("worker.jobs.get_redis", lambda: redis)
    monkeypatch.setattr("worker.jobs.get_settings", lambda: settings)
    monkeypatch.setattr("worker.jobs.prepare_audio", lambda *args, **kwargs: None)

    def fake_diarize(job_id, store, storage_, settings_):
        storage_.prepare_job_dirs(job_id)
        write_wav(storage_.normalized_path(job_id), DURATION)
        result = DiarizationResult(
            model="fake",
            speakers=["SPEAKER_00", "SPEAKER_01"],
            segments=[],
            exclusive=[
                SpeakerSegment(start=1.0, end=12.0, speaker_id="SPEAKER_00"),
                SpeakerSegment(start=12.2, end=18.0, speaker_id="SPEAKER_01"),
                SpeakerSegment(start=30.0, end=36.0, speaker_id="SPEAKER_00"),
            ],
        )
        storage_.diarization_path(job_id).write_text(result.model_dump_json(), encoding="utf-8")

    monkeypatch.setattr("worker.jobs.diarize", fake_diarize)
    monkeypatch.setattr("worker.jobs.generate_exports", lambda *args, **kwargs: None)


def install_model(monkeypatch, model) -> None:
    monkeypatch.setattr("worker.jobs.get_asr_model", lambda settings: model)


def make_job(store) -> str:
    job = store.create(JobSource(filename="meeting.wav", size_bytes=10))
    record = store.require(job.job_id)
    record.audio = AudioMetadata(duration_seconds=DURATION)
    store.replace(record)
    return job.job_id


def test_stage_writes_asr_words_json(worker_env, store, storage, monkeypatch) -> None:
    install_model(monkeypatch, FakeAsrModel())
    job_id = make_job(store)

    process_job(job_id)

    payload = json.loads(storage.asr_words_path(job_id).read_text(encoding="utf-8"))
    assert payload["engine"] == "gigaam"
    assert payload["model"] == "v3_e2e_rnnt"
    assert payload["words"], "words must be present"
    assert store.get(job_id).status is JobStatus.COMPLETED


def test_words_are_on_the_global_timeline(worker_env, store, storage, monkeypatch) -> None:
    install_model(monkeypatch, FakeAsrModel())
    job_id = make_job(store)

    process_job(job_id)

    payload = json.loads(storage.asr_words_path(job_id).read_text(encoding="utf-8"))
    starts = [word["start"] for word in payload["words"]]
    assert starts == sorted(starts)
    assert starts[0] >= 0.0
    assert max(word["end"] for word in payload["words"]) <= DURATION + 1.0
    # The 30-36 s region must be reached — a chunk offset bug would keep everything near 0.
    assert any(start > 25.0 for start in starts)


def test_short_pause_does_not_split_a_chunk(worker_env, store, storage, monkeypatch) -> None:
    install_model(monkeypatch, FakeAsrModel())
    job_id = make_job(store)

    process_job(job_id)

    payload = json.loads(storage.asr_words_path(job_id).read_text(encoding="utf-8"))
    # 1.0-12.0 and 12.2-18.0 merge (0.2 s gap) into one 17.2 s region, capped at 20 s.
    assert len(payload["chunks"]) == 2


def test_progress_grows_inside_the_transcribing_window(worker_env, store, monkeypatch) -> None:
    install_model(monkeypatch, FakeAsrModel())
    job_id = make_job(store)
    start, end = STAGE_PROGRESS_RANGE[JobStatus.TRANSCRIBING]
    seen: list[int] = []
    original = store.__class__.update

    def record(self, job_id_, **kwargs):
        job = original(self, job_id_, **kwargs)
        if job.status is JobStatus.TRANSCRIBING:
            seen.append(job.progress)
        return job

    monkeypatch.setattr(store.__class__, "update", record)

    process_job(job_id)

    assert seen[0] == start
    assert seen[-1] == end
    assert seen == sorted(seen)
    assert len(set(seen)) > 2, "progress must reflect processed duration, not just the edges"


def test_asr_failure_marks_the_job(worker_env, store, monkeypatch) -> None:
    install_model(monkeypatch, FakeAsrModel(raises=RuntimeError("boom")))
    job_id = make_job(store)

    with pytest.raises(AppError):
        process_job(job_id)

    job = store.get(job_id)
    assert job.status is JobStatus.FAILED
    assert job.error_code is ErrorCode.ASR_FAILED
    assert 30 <= job.progress < 85, "failure happened inside the ASR progress window"


def test_model_load_failure_marks_the_job(worker_env, store, monkeypatch) -> None:
    def refuse(settings):
        raise AppError(ErrorCode.MODEL_NOT_AVAILABLE, "gigaam is not installed in this image")

    monkeypatch.setattr("worker.jobs.get_asr_model", refuse)
    job_id = make_job(store)

    with pytest.raises(AppError):
        process_job(job_id)

    assert store.get(job_id).error_code is ErrorCode.MODEL_NOT_AVAILABLE


def test_missing_diarization_fails_the_stage(store, storage, settings, monkeypatch) -> None:
    install_model(monkeypatch, FakeAsrModel())
    job_id = make_job(store)
    storage.prepare_job_dirs(job_id)

    with pytest.raises(AppError) as exc:
        transcribe(job_id, store, storage, settings)

    assert exc.value.code is ErrorCode.ASR_FAILED


def test_silence_only_diarization_fails_the_stage(store, storage, settings, monkeypatch) -> None:
    install_model(monkeypatch, FakeAsrModel())
    job_id = make_job(store)
    storage.prepare_job_dirs(job_id)
    empty = DiarizationResult(model="fake", speakers=[], segments=[], exclusive=[])
    storage.diarization_path(job_id).write_text(empty.model_dump_json(), encoding="utf-8")

    with pytest.raises(AppError) as exc:
        transcribe(job_id, store, storage, settings)

    assert exc.value.code is ErrorCode.ASR_FAILED

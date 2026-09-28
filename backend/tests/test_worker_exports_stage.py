import pytest

from app.core.errors import AppError, ErrorCode
from app.schemas.job import STAGE_PROGRESS_RANGE, JobSource, JobStatus
from tests.test_exports import font_or_skip, make_transcript
from worker.jobs import generate_exports, process_job


@pytest.fixture
def worker_env(monkeypatch, redis, settings, storage):
    """Real export stage; the earlier stages only drop the canonical JSON on disk."""
    monkeypatch.setattr("worker.jobs.get_redis", lambda: redis)
    monkeypatch.setattr("worker.jobs.get_settings", lambda: settings)
    for name in ("prepare_audio", "diarize", "transcribe"):
        monkeypatch.setattr(f"worker.jobs.{name}", lambda *args, **kwargs: None)

    def fake_align(job_id, store, storage_, settings_):
        write_transcript(storage_, job_id)

    monkeypatch.setattr("worker.jobs.align", fake_align)


def write_transcript(storage, job_id) -> None:
    storage.prepare_job_dirs(job_id)
    storage.transcript_path(job_id).write_text(
        make_transcript().model_dump_json(), encoding="utf-8"
    )


def make_job(store) -> str:
    return store.create(JobSource(filename="meeting.m4a", size_bytes=10)).job_id


def test_stage_writes_all_export_formats(worker_env, store, storage) -> None:
    font_or_skip()
    job_id = make_job(store)

    process_job(job_id)

    result_dir = storage.result_dir(job_id)
    assert sorted(path.name for path in result_dir.iterdir()) == [
        "transcript.docx",
        "transcript.json",
        "transcript.pdf",
        "transcript.txt",
    ]
    assert store.get(job_id).status is JobStatus.COMPLETED
    assert store.get(job_id).progress == 100


def test_canonical_json_is_never_replaced_by_a_view(worker_env, store, storage) -> None:
    """AGENTS.md rule 5 — DOCX/PDF must never be the only copy of a result."""
    font_or_skip()
    job_id = make_job(store)

    process_job(job_id)

    assert storage.transcript_path(job_id).is_file()
    assert "Добрый день" in storage.result_dir(job_id).joinpath("transcript.txt").read_text(
        encoding="utf-8"
    )


def test_progress_covers_the_exports_window(worker_env, store, monkeypatch) -> None:
    font_or_skip()
    job_id = make_job(store)
    start, end = STAGE_PROGRESS_RANGE[JobStatus.GENERATING_EXPORTS]
    seen: list[int] = []
    original = store.__class__.update

    def record(self, job_id_, **kwargs):
        job = original(self, job_id_, **kwargs)
        if job.status is JobStatus.GENERATING_EXPORTS:
            seen.append(job.progress)
        return job

    monkeypatch.setattr(store.__class__, "update", record)

    process_job(job_id)

    assert seen[0] == start
    assert seen[-1] == end


def test_missing_transcript_fails_the_stage(store, storage, settings) -> None:
    job_id = make_job(store)
    storage.prepare_job_dirs(job_id)

    with pytest.raises(AppError) as exc:
        generate_exports(job_id, store, storage, settings)

    assert exc.value.code is ErrorCode.EXPORT_FAILED


def test_export_failure_marks_the_job(worker_env, store, monkeypatch) -> None:
    job_id = make_job(store)
    monkeypatch.setattr(
        "worker.jobs.regenerate_exports",
        lambda *args: (_ for _ in ()).throw(AppError(ErrorCode.EXPORT_FAILED, "no font")),
    )

    with pytest.raises(AppError):
        process_job(job_id)

    job = store.get(job_id)
    assert job.status is JobStatus.FAILED
    assert job.error_code is ErrorCode.EXPORT_FAILED
    assert job.progress >= 92, "failure happened inside the exports window"


def test_configured_font_reaches_the_stage(
    worker_env, store, storage, settings, monkeypatch
) -> None:
    job_id = make_job(store)
    write_transcript(storage, job_id)
    seen: list = []
    monkeypatch.setattr(
        "worker.jobs.regenerate_exports",
        lambda path, result_dir, font: seen.append(font) or [],
    )

    generate_exports(
        job_id, store, storage, settings.model_copy(update={"pdf_font_path": "/f.ttf"})
    )

    assert str(seen[0]) == "/f.ttf"

"""T7.1, T7.3-T7.5 — result API: transcript, audio streaming, rename, downloads."""

import json

import pytest

from app.schemas.job import JobSource, JobStatus
from app.services import exports
from tests.test_exports import font_or_skip, make_transcript

AUDIO = b"RIFF" + bytes(range(256)) * 8  # 2052 bytes of stand-in audio


@pytest.fixture
def completed_job(store, storage):
    """A finished job with its source recording and every result file on disk."""
    font_or_skip()
    job = store.create(JobSource(filename="Планёрка.m4a", size_bytes=len(AUDIO)))
    storage.prepare_job_dirs(job.job_id)
    storage.source_path(job.job_id, "m4a").write_bytes(AUDIO)

    transcript = make_transcript()
    storage.transcript_path(job.job_id).write_text(
        transcript.model_dump_json(indent=2), encoding="utf-8"
    )
    exports.regenerate_exports(storage.transcript_path(job.job_id), storage.result_dir(job.job_id))
    store.update(job.job_id, status=JobStatus.COMPLETED, progress=100)
    return job.job_id


@pytest.fixture
def queued_job(store):
    return store.create(JobSource(filename="meeting.wav", size_bytes=1)).job_id


# --- T7.1 transcript -----------------------------------------------------------------


def test_transcript_is_served(client, completed_job) -> None:
    response = client.get(f"/api/v1/jobs/{completed_job}/transcript")

    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == 1
    assert payload["speakers"][0]["display_name"] == "Спикер 1"
    assert payload["segments"][0]["text"].startswith("Добрый день")


def test_transcript_of_an_unfinished_job_is_not_an_error_state(client, queued_job) -> None:
    response = client.get(f"/api/v1/jobs/{queued_job}/transcript")

    assert response.status_code == 409
    assert response.json()["error_code"] == "RESULT_NOT_READY"


def test_transcript_of_an_unknown_job_is_404(client) -> None:
    response = client.get("/api/v1/jobs/11111111-2222-3333-4444-555555555555/transcript")

    assert response.status_code == 404
    assert response.json()["error_code"] == "JOB_NOT_FOUND"


# --- T7.3 audio ----------------------------------------------------------------------


def test_audio_is_served_whole_when_no_range_is_asked(client, completed_job) -> None:
    response = client.get(f"/api/v1/jobs/{completed_job}/audio")

    assert response.status_code == 200
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-type"].startswith("audio/mp4")
    assert response.content == AUDIO


def test_audio_range_request_returns_206(client, completed_job) -> None:
    response = client.get(f"/api/v1/jobs/{completed_job}/audio", headers={"Range": "bytes=100-199"})

    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes 100-199/{len(AUDIO)}"
    assert response.headers["content-length"] == "100"
    assert response.content == AUDIO[100:200]


def test_audio_seek_to_the_tail(client, completed_job) -> None:
    response = client.get(f"/api/v1/jobs/{completed_job}/audio", headers={"Range": "bytes=-50"})

    assert response.status_code == 206
    assert response.content == AUDIO[-50:]


def test_unsatisfiable_range_returns_416(client, completed_job) -> None:
    response = client.get(f"/api/v1/jobs/{completed_job}/audio", headers={"Range": "bytes=99999-"})

    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{len(AUDIO)}"


def test_audio_of_a_job_without_a_source_is_reported(client, store) -> None:
    job = store.create(JobSource(filename="gone.wav", size_bytes=1))

    response = client.get(f"/api/v1/jobs/{job.job_id}/audio")

    assert response.status_code == 409
    assert response.json()["error_code"] == "RESULT_NOT_READY"


# --- T7.4 rename ---------------------------------------------------------------------


def test_rename_updates_json_and_regenerates_exports(client, storage, completed_job) -> None:
    response = client.patch(f"/api/v1/jobs/{completed_job}/speakers", json={"SPEAKER_00": "Сергей"})

    assert response.status_code == 200
    assert response.json()["speakers"][0]["display_name"] == "Сергей"

    stored = json.loads(storage.transcript_path(completed_job).read_text(encoding="utf-8"))
    assert stored["speakers"][0]["display_name"] == "Сергей"

    txt = (storage.result_dir(completed_job) / "transcript.txt").read_text(encoding="utf-8")
    assert "Сергей" in txt
    assert "Спикер 1" not in txt


def test_rename_leaves_words_and_timestamps_untouched(client, storage, completed_job) -> None:
    before = json.loads(storage.transcript_path(completed_job).read_text(encoding="utf-8"))

    client.patch(f"/api/v1/jobs/{completed_job}/speakers", json={"SPEAKER_00": "Сергей"})

    after = json.loads(storage.transcript_path(completed_job).read_text(encoding="utf-8"))
    assert after["words"] == before["words"]
    assert after["segments"] == before["segments"]
    assert after["asr"] == before["asr"]


def test_rename_accepts_several_speakers_at_once(client, completed_job) -> None:
    response = client.patch(
        f"/api/v1/jobs/{completed_job}/speakers",
        json={"SPEAKER_00": "Анна", "SPEAKER_01": "Сергей"},
    )

    names = [speaker["display_name"] for speaker in response.json()["speakers"]]
    assert names == ["Анна", "Сергей"]


def test_rename_trims_whitespace(client, completed_job) -> None:
    response = client.patch(
        f"/api/v1/jobs/{completed_job}/speakers", json={"SPEAKER_00": "  Анна  "}
    )

    assert response.json()["speakers"][0]["display_name"] == "Анна"


def test_rename_rejects_an_empty_name(client, completed_job) -> None:
    response = client.patch(f"/api/v1/jobs/{completed_job}/speakers", json={"SPEAKER_00": "   "})

    assert response.status_code == 422


def test_rename_rejects_an_empty_body(client, completed_job) -> None:
    response = client.patch(f"/api/v1/jobs/{completed_job}/speakers", json={})

    assert response.status_code == 422


def test_rename_of_an_unknown_speaker_is_reported(client, completed_job) -> None:
    response = client.patch(f"/api/v1/jobs/{completed_job}/speakers", json={"SPEAKER_99": "Кто"})

    assert response.status_code == 404
    assert "SPEAKER_99" in response.json()["message"]


def test_rename_before_the_result_exists_is_not_an_error_state(client, queued_job) -> None:
    response = client.patch(f"/api/v1/jobs/{queued_job}/speakers", json={"SPEAKER_00": "Анна"})

    assert response.status_code == 409
    assert response.json()["error_code"] == "RESULT_NOT_READY"


# --- T7.5 downloads ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("export_format", "media_type"),
    [
        ("json", "application/json"),
        ("txt", "text/plain"),
        ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("pdf", "application/pdf"),
    ],
)
def test_every_format_downloads(client, completed_job, export_format, media_type) -> None:
    response = client.get(f"/api/v1/jobs/{completed_job}/download/{export_format}")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(media_type)
    assert len(response.content) > 0


def test_download_is_named_after_the_recording(client, completed_job) -> None:
    response = client.get(f"/api/v1/jobs/{completed_job}/download/txt")

    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "filename*=UTF-8''" in disposition


def test_downloaded_json_is_the_canonical_transcript(client, completed_job) -> None:
    served = client.get(f"/api/v1/jobs/{completed_job}/transcript").json()
    downloaded = json.loads(client.get(f"/api/v1/jobs/{completed_job}/download/json").content)

    assert downloaded == served


def test_unknown_download_format_is_rejected(client, completed_job) -> None:
    response = client.get(f"/api/v1/jobs/{completed_job}/download/csv")

    assert response.status_code == 400
    assert response.json()["error_code"] == "UNSUPPORTED_FORMAT"


def test_download_before_the_result_exists(client, queued_job) -> None:
    response = client.get(f"/api/v1/jobs/{queued_job}/download/pdf")

    assert response.status_code == 409
    assert response.json()["error_code"] == "RESULT_NOT_READY"

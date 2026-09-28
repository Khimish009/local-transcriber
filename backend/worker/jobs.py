"""Job entrypoint executed by the RQ worker.

The pipeline is complete end to end: audio preparation (Phase 2), speaker diarization
(Phase 3), GigaAM speech recognition (Phase 4), alignment into the canonical transcript
(Phase 5) and the TXT/DOCX/PDF exports generated from it (Phase 6).
"""

import logging
import time

from app.core.config import Settings, get_settings
from app.core.errors import AppError, ErrorCode
from app.core.redis import get_redis
from app.schemas.asr import AsrResult
from app.schemas.diarization import DiarizationResult
from app.schemas.job import STAGE_PROGRESS_RANGE, JobStatus
from app.services.exports import regenerate_exports
from app.services.job_store import JobStore
from app.services.storage import JobStorage
from worker.pipeline.alignment import build_transcript
from worker.pipeline.asr import build_asr_chunks, transcribe_chunks
from worker.pipeline.audio import normalize_audio, probe_audio
from worker.pipeline.diarization import run_diarization
from worker.pipeline.models import get_asr_model, get_diarization_pipeline

logger = logging.getLogger(__name__)

# Every stage of SPEC.md §5 now has a real implementation — no placeholder walk is left.
PROCESSING_STAGES: tuple[JobStatus, ...] = (
    JobStatus.PREPARING_AUDIO,
    JobStatus.DIARIZING,
    JobStatus.TRANSCRIBING,
    JobStatus.ALIGNING,
    JobStatus.GENERATING_EXPORTS,
)

# Share of the TRANSCRIBING window spent loading the model before the first chunk runs.
ASR_MODEL_LOAD_SHARE = 0.03


def stage_progress(stage: JobStatus, step: float, steps: float) -> int:
    """Map a step inside a stage onto the global 0..100 progress scale."""
    start, end = STAGE_PROGRESS_RANGE[stage]
    return start + round((end - start) * step / steps)


def prepare_audio(job_id: str, store: JobStore, storage: JobStorage, settings: Settings) -> None:
    """T2.1/T2.2 — read source metadata and produce work/normalized.wav."""
    stage = JobStatus.PREPARING_AUDIO
    store.update(job_id, status=stage, progress=stage_progress(stage, 0, 2))

    source = storage.find_source(job_id)
    if source is None:
        raise AppError(ErrorCode.FFMPEG_FAILED, "Source recording is missing on disk")

    metadata = probe_audio(source, settings.ffprobe_bin)
    job = store.require(job_id)
    job.audio = metadata
    store.replace(job)
    store.update(job_id, progress=stage_progress(stage, 1, 2))

    normalize_audio(source, storage.normalized_path(job_id), settings.ffmpeg_bin)
    store.update(job_id, progress=stage_progress(stage, 2, 2))


def diarize(job_id: str, store: JobStore, storage: JobStorage, settings: Settings) -> None:
    """T3.4/T3.5 — split the recording into anonymous speakers, save work/diarization.json."""
    stage = JobStatus.DIARIZING
    store.update(
        job_id,
        status=stage,
        progress=stage_progress(stage, 0, 3),
        message="Загрузка модели диаризации",
    )

    # Loaded once per worker process and reused by every later job.
    pipeline = get_diarization_pipeline(settings, settings.hf_token)
    store.update(job_id, progress=stage_progress(stage, 1, 3), message="Разделение по спикерам")

    job = store.require(job_id)
    result = run_diarization(
        storage.normalized_path(job_id),
        pipeline,
        speaker_count=job.speaker_count,
    )

    storage.diarization_path(job_id).write_text(result.model_dump_json(indent=2), encoding="utf-8")
    store.update(
        job_id,
        progress=stage_progress(stage, 3, 3),
        message=f"Найдено спикеров: {len(result.speakers)}",
    )


def read_diarization(storage: JobStorage, job_id: str, code: ErrorCode) -> DiarizationResult:
    """Intermediate artifacts are re-read from disk, so a stage can be debugged in isolation."""
    path = storage.diarization_path(job_id)
    if not path.is_file():
        raise AppError(code, "Diarization result is missing")
    return DiarizationResult.model_validate_json(path.read_text(encoding="utf-8"))


def transcribe(job_id: str, store: JobStore, storage: JobStorage, settings: Settings) -> None:
    """T4.2-T4.4 — recognize speech with GigaAM, save work/asr_words.json."""
    stage = JobStatus.TRANSCRIBING
    store.update(
        job_id,
        status=stage,
        progress=stage_progress(stage, 0, 1),
        message="Загрузка модели распознавания",
    )

    # Loaded once per worker process and reused by every later job.
    model = get_asr_model(settings)
    store.update(
        job_id,
        progress=stage_progress(stage, ASR_MODEL_LOAD_SHARE, 1),
        message="Распознавание речи",
    )

    diarization = read_diarization(storage, job_id, ErrorCode.ASR_FAILED)

    job = store.require(job_id)
    chunks = build_asr_chunks(
        diarization.exclusive or diarization.segments,
        max_chunk_seconds=settings.max_asr_chunk_seconds,
        merge_gap_seconds=settings.merge_silence_gap_ms / 1000,
        total_duration=job.audio.duration_seconds if job.audio else None,
    )
    if not chunks:
        raise AppError(ErrorCode.ASR_FAILED, "No speech regions to transcribe")

    def report(processed: float, total: float) -> None:
        done = processed / total if total > 0 else 1.0
        share = ASR_MODEL_LOAD_SHARE + (1 - ASR_MODEL_LOAD_SHARE) * done
        store.update(
            job_id,
            progress=stage_progress(stage, share, 1),
            message=f"Распознавание речи: {round(done * 100)}%",
        )

    result = transcribe_chunks(
        storage.normalized_path(job_id),
        chunks,
        model,
        settings.asr_model,
        on_progress=report,
    )

    if not result.words:
        # Diarization found speech but nothing was recognized — do not hand the user an
        # empty transcript as if it were a success.
        raise AppError(ErrorCode.ASR_FAILED, "No speech was recognized in the recording")

    storage.asr_words_path(job_id).write_text(result.model_dump_json(indent=2), encoding="utf-8")
    store.update(
        job_id,
        progress=stage_progress(stage, 1, 1),
        message=f"Распознано слов: {len(result.words)}",
    )


def align(job_id: str, store: JobStore, storage: JobStorage, settings: Settings) -> None:
    """T5.1-T5.4 — attach speakers to words, group them into blocks, save transcript.json."""
    stage = JobStatus.ALIGNING
    store.update(
        job_id,
        status=stage,
        progress=stage_progress(stage, 0, 3),
        message="Сопоставление слов и спикеров",
    )

    diarization = read_diarization(storage, job_id, ErrorCode.ASR_FAILED)
    asr_path = storage.asr_words_path(job_id)
    if not asr_path.is_file():
        raise AppError(ErrorCode.ASR_FAILED, "Recognition result is missing")
    asr = AsrResult.model_validate_json(asr_path.read_text(encoding="utf-8"))
    store.update(job_id, progress=stage_progress(stage, 1, 3))

    job = store.require(job_id)
    transcript = build_transcript(
        job_id=job_id,
        filename=job.source.filename,
        duration_seconds=job.audio.duration_seconds if job.audio else None,
        asr=asr,
        diarization=diarization,
        tolerance_seconds=settings.speaker_match_tolerance_ms / 1000,
        max_block_seconds=settings.max_block_seconds,
        block_gap_seconds=settings.block_silence_gap_ms / 1000,
    )
    store.update(job_id, progress=stage_progress(stage, 2, 3))

    storage.transcript_path(job_id).parent.mkdir(parents=True, exist_ok=True)
    storage.transcript_path(job_id).write_text(
        transcript.model_dump_json(indent=2), encoding="utf-8"
    )
    store.update(
        job_id,
        progress=stage_progress(stage, 3, 3),
        message=f"Готово блоков: {len(transcript.segments)}",
    )


def generate_exports(job_id: str, store: JobStore, storage: JobStorage, settings: Settings) -> None:
    """T6.1-T6.4 — write TXT/DOCX/PDF next to the canonical transcript.json."""
    stage = JobStatus.GENERATING_EXPORTS
    store.update(
        job_id,
        status=stage,
        progress=stage_progress(stage, 0, 1),
        message="Генерация TXT, DOCX и PDF",
    )

    # Reads the canonical JSON back from disk on purpose: exports are a view of that file
    # and of nothing else, exactly as a later speaker rename will regenerate them.
    written = regenerate_exports(
        storage.transcript_path(job_id),
        storage.result_dir(job_id),
        settings.pdf_font_path,
    )

    store.update(
        job_id,
        progress=stage_progress(stage, 1, 1),
        message=f"Форматы готовы: {', '.join(path.suffix.lstrip('.').upper() for path in written)}",
    )


def record_failure(
    store: JobStore, job_id: str, message: str, code: ErrorCode | None = None
) -> bool:
    """Mark the job failed. Returns False if the record was deleted mid-flight (T8.3).

    A delete request removes the Redis record while the worker is still inside a stage;
    there is then nothing left to fail, and that is not an error worth masking the original
    one with.
    """
    try:
        store.fail(job_id, message=message, error_code=code)
    except AppError as exc:
        if exc.code is not ErrorCode.JOB_NOT_FOUND:
            raise
        logger.info("job record already deleted", extra={"job_id": job_id})
        return False
    return True


def process_job(job_id: str) -> None:
    settings = get_settings()
    store = JobStore(get_redis())
    storage = JobStorage(settings.data_dir)

    if store.get(job_id) is None:
        # Deleted while it sat in the queue (T8.3) — nothing to process, nothing to fail.
        logger.info("job record is gone, skipping", extra={"job_id": job_id})
        return

    logger.info("job started", extra={"job_id": job_id, "device": settings.device})
    started = time.monotonic()

    try:
        prepare_audio(job_id, store, storage, settings)
        diarize(job_id, store, storage, settings)
        transcribe(job_id, store, storage, settings)
        align(job_id, store, storage, settings)
        generate_exports(job_id, store, storage, settings)

        store.update(
            job_id,
            status=JobStatus.COMPLETED,
            progress=100,
            message="Транскрипт готов",
        )
    except AppError as exc:
        if exc.code is ErrorCode.JOB_NOT_FOUND:
            # The user deleted the job while it was running — an expected outcome, not a
            # pipeline failure. Nothing is left to update.
            logger.info("job deleted while running", extra={"job_id": job_id})
            return
        # Expected pipeline failure with a stable error code (SPEC.md §12).
        logger.error("job failed", extra={"job_id": job_id, "error_code": exc.code.value})
        if record_failure(store, job_id, exc.message, exc.code):
            raise
        # The stage only failed because the deletion pulled its files away — do not hand RQ
        # a traceback for a job the user asked to forget.
    except Exception:
        logger.exception("job crashed", extra={"job_id": job_id})
        if record_failure(store, job_id, "Internal error while processing the job"):
            raise
    finally:
        logger.info(
            "job finished",
            extra={"job_id": job_id, "duration": round(time.monotonic() - started, 3)},
        )

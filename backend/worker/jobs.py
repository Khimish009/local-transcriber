"""Job entrypoint executed by the RQ worker.

Implemented so far: audio preparation (Phase 2), speaker diarization (Phase 3), GigaAM
speech recognition (Phase 4) and alignment into the canonical transcript (Phase 5). Export
generation is still a placeholder that only walks the state machine — it lands in Phase 6.
"""

import logging
import time

from app.core.config import Settings, get_settings
from app.core.errors import AppError, ErrorCode
from app.core.redis import get_redis
from app.schemas.asr import AsrResult
from app.schemas.diarization import DiarizationResult
from app.schemas.job import STAGE_PROGRESS_RANGE, JobStatus
from app.services.job_store import JobStore
from app.services.storage import JobStorage
from worker.pipeline.alignment import build_transcript
from worker.pipeline.asr import build_asr_chunks, transcribe_chunks
from worker.pipeline.audio import normalize_audio, probe_audio
from worker.pipeline.diarization import run_diarization
from worker.pipeline.models import get_asr_model, get_diarization_pipeline

logger = logging.getLogger(__name__)

# Stages that still have no real implementation, in pipeline order (SPEC.md §5).
PLACEHOLDER_STAGES: tuple[JobStatus, ...] = (JobStatus.GENERATING_EXPORTS,)
STAGE_STEPS = 4
DEFAULT_STAGE_SECONDS = 1.5

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


def process_job(job_id: str, stage_seconds: float | None = None) -> None:
    settings = get_settings()
    store = JobStore(get_redis())
    storage = JobStorage(settings.data_dir)
    delay = DEFAULT_STAGE_SECONDS if stage_seconds is None else stage_seconds

    store.require(job_id)
    logger.info("job started", extra={"job_id": job_id, "device": settings.device})
    started = time.monotonic()

    try:
        prepare_audio(job_id, store, storage, settings)
        diarize(job_id, store, storage, settings)
        transcribe(job_id, store, storage, settings)
        align(job_id, store, storage, settings)

        for stage in PLACEHOLDER_STAGES:
            store.update(job_id, status=stage, progress=stage_progress(stage, 0, STAGE_STEPS))
            for step in range(1, STAGE_STEPS + 1):
                if delay:
                    time.sleep(delay / STAGE_STEPS)
                store.update(job_id, progress=stage_progress(stage, step, STAGE_STEPS))

        store.update(
            job_id,
            status=JobStatus.COMPLETED,
            progress=100,
            message="Транскрипт готов, экспорт в TXT/DOCX/PDF ещё не реализован",
        )
    except AppError as exc:
        # Expected pipeline failure with a stable error code (SPEC.md §12).
        logger.error("job failed", extra={"job_id": job_id, "error_code": exc.code.value})
        store.fail(job_id, message=exc.message, error_code=exc.code)
        raise
    except Exception:
        logger.exception("job crashed", extra={"job_id": job_id})
        store.fail(job_id, message="Internal error while processing the job")
        raise
    finally:
        logger.info(
            "job finished",
            extra={"job_id": job_id, "duration": round(time.monotonic() - started, 3)},
        )

# AGENTS.md — Instructions for Coding Agents

## Project

You are working on `Local Transcriber`, a privacy-first local application for Russian meeting transcription.

Read before making changes:
1. `README.md`
2. `SPEC.md`
3. `TASKS.md`

Treat `SPEC.md` as the product/technical contract.

---

## Core rules

### 1. Local-only processing

Never introduce:
- cloud ASR;
- cloud diarization;
- cloud LLM calls;
- uploading user audio/transcripts to third parties.

Network access is permitted only for:
- package installation;
- initial model download.

Runtime inference must be local.

### 2. Quality over speed

Do not replace GigaAM/pyannote with a faster but lower-quality solution merely for performance.

Optimizations must not silently reduce transcription quality.

### 3. Portability

The reference implementation must run in CPU mode through:

```bash
docker compose up --build
```

on:
- macOS;
- Windows Docker Desktop;
- Linux.

CUDA is an optional override, not a requirement for correctness.

### 4. Do not commit models or user data

Never commit:
- `.env`;
- Hugging Face tokens;
- model weights;
- uploaded audio;
- generated transcripts.

Keep:
- `/models`
- `/data`

persistent and gitignored.

### 5. Canonical JSON

`transcript.json` is the source of truth.

TXT/DOCX/PDF are views generated from JSON.

Never make DOCX/PDF the only copy of a result.

### 6. Preserve timestamps

Do not perform text transformations that make word/segment timestamps unreliable.

If adding normalization later, keep:
- raw recognition;
- normalized/readable representation
separate.

### 7. Speaker attribution

Do not invent speaker identities.

Diarization produces anonymous IDs:
- `SPEAKER_00`
- `SPEAKER_01`

User may later rename them.

### 8. Errors

Never silently swallow ML/FFmpeg errors.

Use stable error codes defined in `SPEC.md`.

Backend logs may contain technical tracebacks.
Frontend must show concise user-facing messages.

### 9. Incremental development

Implement only the current task/phase plus directly required supporting code.

Do not build speculative post-MVP features.

### 10. Keep the project runnable

At the end of every task:
- run relevant tests;
- verify Docker build when Docker-related files changed;
- update docs if behavior/config changed.

---

## Architecture constraints

Frontend:
- Next.js
- TypeScript

Backend:
- FastAPI

Jobs:
- Redis
- RQ

ML:
- GigaAM v3
- pyannote speaker-diarization-community-1

Audio:
- FFmpeg

Exports:
- JSON
- TXT
- DOCX
- PDF

No PostgreSQL/Kafka/Kubernetes in MVP.

---

## Code quality

Python:
- type hints for public functions;
- Pydantic models at API boundaries;
- separate adapters/services from API routes;
- avoid global mutable job state;
- dependency injection where it improves testability;
- unit-test deterministic pipeline logic.

TypeScript:
- strict mode;
- typed API contracts;
- components should not contain business logic that belongs in services/hooks.

General:
- small cohesive modules;
- descriptive names;
- no premature abstraction;
- no hardcoded host paths;
- config comes from env/settings;
- no secrets in logs.

---

## ML integration rules

Model loading must be centralized.

Models should be initialized once per worker process and reused across jobs.

Do not reload GigaAM or pyannote per chunk.

All intermediate timestamps must use seconds as floating-point values on the global audio timeline.

Store intermediate artifacts during development:
- diarization output;
- ASR words;
- canonical result.

This is important for debugging without rerunning expensive inference.

---

## Docker rules

Use multi-stage builds where helpful.

Base CPU compose is authoritative.

Do not duplicate the entire base compose for CUDA; use override file.

Persistent mounts must cover:
- `/data`;
- `/models`;
- model caches if applicable.

Containers must not depend on host-specific absolute paths.

Add healthchecks for core services.

---

## Workflow for every task

Before editing:
1. identify the current task from `TASKS.md`;
2. inspect existing code;
3. state a short implementation plan.

Then:
1. implement the smallest complete increment;
2. add/update tests;
3. run tests;
4. run formatter/linter;
5. if Docker changed, validate compose/build;
6. summarize files changed;
7. report commands run and their results;
8. note remaining limitations.

Do not claim something works if it was not tested.

---

## Уже принятые решения (Phases 0–4)

Не переоткрывать без причины — за каждым стоит проблема, которая уже была поймана.

### Разделение образов
`backend/Dockerfile` имеет таргеты `runtime` (API, без ML) и `worker` (torch + pyannote).
API-образ ~604 МБ, worker ~1.93 ГБ. API никогда не импортирует код из `worker/`.
Следствие: всё, что нужно и API, и воркеру (пути к моделям, схемы), живёт в `app/`.

### Только CPU-колёса torch
`backend/requirements-worker.txt` пинит `torch==2.11.0+cpu`, `torchaudio==2.11.0+cpu`,
`torchcodec==0.16.0+cpu`. Обычные PyPI-колёса тянут CUDA-библиотеки (~2–3 ГБ) **и на
amd64, и на arm64**. Суффикс `+cpu` существует только на индексе PyTorch, поэтому
Dockerfile ставит их с `--index-url https://download.pytorch.org/whl/cpu`.
Версии обновлять только тройкой: torchcodec 0.16 требует torch >= 2.11, torchaudio 2.11 —
последний релиз своей линии.

### SimpleWorker вместо Worker
`worker/main.py` использует `rq.SimpleWorker` и грузит модели при старте процесса.
Обычный `Worker` форкает процесс на каждую задачу, и fork после загрузки torch-модели
приводит к зависанию инференса (наследуется память модели, но не пул потоков OpenMP).
Модель обязана грузиться один раз на процесс и переиспользоваться между задачами.
Плата: крах инференса роняет worker, задача остаётся незавершённой (чинится в Phase 8).

### Модель диаризации публичная
`pyannote/speaker-diarization-community-1` скачивается без `HF_TOKEN`. Код всё равно
поддерживает токен и отдаёт `HF_TOKEN_REQUIRED`, если весов нет и токена нет, — на случай
gated-моделей в будущем. Токен читается только из env, помечен `repr=False`, никогда не
логируется и не возвращается через API.

### Кэш моделей и offline
Веса лежат в `MODELS_DIR/huggingface-cache/`. Если веса уже на диске, выставляется
`HF_HUB_OFFLINE=1` — инференс не ходит в сеть. `app/services/model_cache.py` — чистая
работа с путями, без ML-импортов, чтобы health-эндпоинт мог отвечать из API-образа.

### Ошибки
`AppError(ErrorCode, message)` + обработчик в `app/main.py` дают `{error_code, message}`.
Технические детали (stderr ffmpeg, трейсбеки pyannote) остаются в логах. `process_job`
различает `AppError` (есть стабильный код) и внутренние сбои (код не выдумывается).

### Redis
Запущен с `appendonly yes` и volume `redis-data`: без персистентности перезапуск Redis
терял регистрацию RQ-воркера, и он навсегда переставал считаться живым.

### Тесты
ML-зависимостей в локальном venv нет — тесты подменяют `pyannote.audio` и `torch`
фейковыми модулями, а ffmpeg-тесты помечены `skipif` по наличию бинарника (на dev-машине
ffmpeg есть, и они реально выполняются). Тесты бэкенда должны проходить без torch.

### GigaAM ставится из git, а не с PyPI
На PyPI последний релиз — `gigaam 0.1.0`: без моделей v3, без word-level таймкодов и с
пином `torch<=2.5.1`, который конфликтует с нашим `torch==2.11.0+cpu`. Поэтому
`requirements-worker.txt` тянет пакет с GitHub по конкретному коммиту. `torch` там —
опциональный extra, поэтому наши `+cpu`-пины не перетираются. `git` ставится и удаляется
в одном слое Dockerfile, чтобы не остаться в образе.

### ASR-чанки строятся не по speaker-turn
`worker/pipeline/asr.py` берёт объединение речевых интервалов из `exclusive`-диаризации,
склеивает паузы короче `MERGE_SILENCE_GAP_MS`, расширяет края на половину этого зазора
(диаризация обрезает начала слов; половина — максимум, при котором соседние чанки не
пересекутся) и режет длинные куски на равные части не длиннее `MAX_ASR_CHUNK_SECONDS`.
Привязка к границам реплик дала бы тысячи коротких фрагментов и просадку качества
(SPEC.md §3.3). `build_asr_chunks` — чистая функция без ML-импортов, она покрыта тестами
отдельно от инференса.

### Нарезка аудио — stdlib `wave`, а не ffmpeg на каждый чанк
`work/normalized.wav` гарантированно PCM s16le / mono / 16 kHz, поэтому чанк вырезается
точным срезом кадров, а не вызовом ffmpeg. GigaAM `transcribe()` принимает только путь к
файлу, так что чанк пишется во временный WAV и переиспользует один и тот же файл.

### Word-level таймстемпы обязательны
`model.transcribe(path, word_timestamps=True)` возвращает слова, локальные для чанка; к ним
прибавляется `chunk.start`. Если модель вернула непустой текст без таймкодов — это
`ASR_FAILED`, а не молчаливая деградация: без таймкодов дальше нечего выравнивать.

### Smoke-тесты
`scripts/smoke-test.{sh,ps1}` поднимают полный цикл задачи на синтетическом тоне. Тон —
не речь, поэтому `DIARIZATION_FAILED` там считается допустимым исходом; любая другая
ошибка означает поломку pipeline.

---

## Current assignment

Phases 0–4 готовы (см. `TASKS.md` → Progress). Следующий шаг — `Phase 5 — Alignment`.

Заменить заглушку стадии `ALIGNING` в `backend/worker/jobs.py`: назначить каждому слову
из `work/asr_words.json` спикера по midpoint и `exclusive`-диаризации с configurable
tolerance (если надёжно нельзя — `speaker_id = null`, спикера не выдумывать), собрать
слова в transcript blocks и сохранить канонический `result/transcript.json`.

Не переходить к Phase 6 автоматически.

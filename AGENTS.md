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

## Уже принятые решения (Phases 0–8)

Не переоткрывать без причины — за каждым стоит проблема, которая уже была поймана.

### Разделение образов
`backend/Dockerfile` имеет таргеты `runtime` (API, без ML) и `worker` (torch + pyannote).
API-образ ~653 МБ, worker ~2.06 ГБ. API никогда не импортирует код из `worker/`.
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
Плата: крах инференса роняет worker; задачу подхватывает `recover_orphaned_jobs` при
следующем старте (Phase 8).

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

### Слова привязываются к спикерам по midpoint, а не по перекрытию
`worker/pipeline/alignment.py` берёт середину слова и ищет содержащий её интервал
`exclusive`-диаризации (полуинтервал: на стыке слово достаётся более позднему спикеру).
Если попадания нет — ближайший интервал в пределах `SPEAKER_MATCH_TOLERANCE_MS`, иначе
`speaker_id = null`. Спикер не выдумывается никогда (SPEC.md §3.4). Поиск идёт через
`bisect` по отсортированным началам: интервалы не пересекаются, поэтому ответ — всегда
сосед точки вставки.

### Канонический JSON живёт в `app/`, а не в `worker/`
`app/schemas/transcript.py` читают и воркер, и API: Phase 7 переименовывает спикеров из
API-образа, где нет ни torch, ни кода `worker/`. Сам `alignment.py` — чистая логика без
ML-импортов и без I/O, стадия в `jobs.py` отвечает за чтение и запись файлов.

### Пустой транскрипт — это ошибка, а не успех
Если диаризация нашла речь, а ASR не вернул ни одного слова, стадия падает с `ASR_FAILED`.
Отдавать пользователю пустой транскрипт как готовый результат нельзя (AGENTS.md rule 8).

### Экспортёры живут в `app/services/`, а не в `worker/`
`app/services/exports.py` импортируют и воркер, и API: переименование спикера (Phase 7)
регенерирует TXT/DOCX/PDF из API-образа, который не имеет права импортировать `worker/`.
Поэтому `python-docx` и `reportlab` лежат в базовом `requirements.txt`, а не в
`requirements-worker.txt`. Это не ML-зависимости, и правило «ML только в worker» их не
касается.

### Экспорт читает `transcript.json` с диска, а не объект в памяти
Стадия `generate_exports` в `jobs.py` вызывает `regenerate_exports(path, result_dir)`,
хотя транскрипт только что был у неё в руках. Это намеренно: экспорт обязан быть функцией
одного лишь канонического JSON, и тот же самый путь кода выполнится при переименовании
спикера. Если бы стадия рендерила из памяти, регрессия «экспорт разошёлся с JSON» была бы
незаметна до Phase 7.

### Кириллический шрифт для PDF
Встроенные шрифты PDF — Latin-only, поэтому русский текст превратился бы в пустые
квадраты. Образы ставят `fonts-dejavu-core` (в базовом слое, он нужен обоим образам), а
`resolve_pdf_font()` ищет TTF по списку кандидатов и разрешает переопределение через
`PDF_FONT_PATH`. В список включены и типичные пути dev-машин, чтобы `pytest` реально
проверял генерацию PDF вне Docker, а не пропускал её. Шрифта нет — `EXPORT_FAILED` с
внятным текстом, а не молчаливый Latin-only фолбэк.

### Пустой `PDF_FONT_PATH`
`docker-compose.yml` пробрасывает `${PDF_FONT_PATH:-}`, а пустая строка в поле
`Path | None` превратилась бы в `Path(".")`. Валидатор в `config.py` приводит пустую
строку к `None`. Для строковых полей вроде `HF_TOKEN` такой проблемы нет.

### `RESULT_NOT_READY` — отдельный код, а не 404
Job существует, но результата ещё нет — это не «не найдено» и не ошибка. Эндпоинты
результата отдают 409 `RESULT_NOT_READY`, и UI продолжает показывать прогресс вместо
экрана ошибки. Код добавлен в SPEC.md §12.

### Range-стриминг написан руками
`app/services/media.py` парсит `Range` сам и отдаёт 206/416. `FileResponse` из Starlette
умеет Range не во всех версиях и не даёт контроля над 416, а без корректного 416 браузер
залипает на перемотке в конец файла. Поддерживается только одиночный диапазон — больше
браузеры для медиа и не присылают. Отдаётся оригинал записи, а не `normalized.wav`:
пользователь слушает то, что загрузил.

### Переименование не трогает ничего, кроме имён
`app/services/results.py` меняет только `display_name`, пишет `transcript.json` атомарно
(`tmp` + `os.replace`, канонический файл нельзя оставить обрезанным) и зовёт
`regenerate_exports`. Слова, таймкоды и `speaker_id` остаются нетронутыми — это закреплено
тестом. Неизвестный `speaker_id` — 404, а не молчаливое игнорирование.

### Скачивание: `download` в браузере не работает cross-origin
UI на :3000, API на :8000, поэтому атрибут `download` у ссылки игнорируется. Сохранение
форсирует заголовок `Content-Disposition: attachment` от API, он же несёт имя файла
(RFC 6266 с `filename*=UTF-8''`, иначе кириллица в имени ломается).

### Осиротевшие задачи чинятся при старте worker, а не по таймеру
`app/services/recovery.py` вызывается из `worker/main.py` до `worker.work()`: каждая
незавершённая job, которой нет в очереди в ожидающем статусе, помечается `FAILED` с кодом
`WORKER_CRASHED`. Именно старт worker — единственный момент, когда точно известно, что
исполнителя у неё не осталось. Проверка делается по RQ-статусу, а не по нашему: наш
статус — это и есть то, что зависло.

Задача не перезапускается автоматически. Крах, вызванный самой записью (OOM на длинном
файле, падение нативной библиотеки на кодеке), повторился бы при каждом рестарте и дал бы
бесконечный цикл. Код предполагает один worker-процесс (SPEC.md §2); при нескольких
воркерах проверку пришлось бы ограничить задачами умершего.

### Мёртвый worker не считается живым по регистрации в Redis
Убитый контейнер не успевает сняться с регистрации, и `Worker.all()` отдаёт его ещё
~2 минуты, пока ключ не истечёт. `/health` из-за этого показывал `ok` при мёртвом воркере,
а UI прятал предупреждение. `check_workers` теперь дополнительно смотрит на
`last_heartbeat`: допускаются два пропущенных удара плюс небольшой запас.

### Удаление задачи не пытается убить выполняющуюся
`DELETE /api/v1/jobs/{job_id}` снимает задачу с очереди, удаляет запись и папку.
Прервать `SimpleWorker` в середине инференса нельзя, поэтому worker просто обнаруживает
пропажу записи на ближайшем `store.update` и выходит без записи `FAILED` — воскрешать
удалённую задачу нельзя. Стадия, упавшая только потому, что у неё забрали файлы, не
пробрасывает исключение в RQ: пользователь сам попросил забыть эту задачу.
SSE отдаёт `event: gone` и закрывает поток, иначе клиент вечно висел бы на keep-alive.

### Smoke-тесты
`scripts/smoke-test.{sh,ps1}` поднимают полный цикл задачи на синтетическом тоне. Тон —
не речь, поэтому `DIARIZATION_FAILED` там считается допустимым исходом; любая другая
ошибка означает поломку pipeline.

---

## Current assignment

Phases 0–8 готовы (см. `TASKS.md` → Progress). Пользовательский сценарий работает
end-to-end и проверен вживую. Следующий шаг — `Phase 9 — Windows/CUDA`.

T9.1: smoke-тест CPU-режима на Docker Desktop Windows. T9.2: `docker-compose.cuda.yml`
как override, без дублирования базового compose. T9.3: `DEVICE=cpu|cuda` с явной ошибкой,
если CUDA запрошена, но недоступна (silent fallback запрещён). Заготовка уже есть:
`_resolve_device` в `worker/pipeline/models.py` отдаёт `MODEL_NOT_AVAILABLE`.

### Что проверено вживую в Phase 8
- полный pipeline на реальной русской записи (3-минутный фрагмент судебного заседания,
  m4a): 2 спикера, 369 слов, 9 блоков, ни одного слова без спикера, ~2.5 минуты на CPU
  (Apple Silicon, 8 потоков). Веса GigaAM (428 МБ) скачались при первой задаче;
- UI результата в реальном браузере (Chromium/Playwright): плеер грузит аудио cross-origin
  с :8000, клик по таймкоду перематывает и запускает воспроизведение, переименование
  спикера обновляет блоки, все четыре формата скачиваются, ошибок в консоли нет;
- `docker compose down` → `up`: результаты на месте, веса не перекачиваются;
- сбои: неподдерживаемый формат и content-type, битый WAV, обрезанный m4a, отсутствующий
  `HF_TOKEN`, неверный токен, несуществующий `ASR_MODEL`, отсутствующий бинарник FFmpeg,
  убийство worker в середине задачи, удаление выполняющейся задачи.

### Что всё ещё не проверено вживую
- качество на длинной записи: прогонялся только 3-минутный фрагмент. Пороги
  `SPEAKER_MATCH_TOLERANCE_MS` / `BLOCK_SILENCE_GAP_MS` / `MAX_BLOCK_SECONDS` по-прежнему
  подобраны умозрительно, WER не измерялся, `v3_rnnt` не сравнивался (это Phase 10);
- вёрстка DOCX и PDF не просматривалась глазами — проверены структура через `python-docx`
  и байты PDF (шрифт, кириллические глифы);
- Windows и CUDA — это Phase 9.

Не переходить к Phase 10 автоматически.

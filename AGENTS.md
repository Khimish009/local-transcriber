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

## Документация

- `docs/` — подробные описания решений, которым тесно в этом файле. Ниже на них есть ссылки
  из соответствующих разделов:
  - [docs/processing-time.md](docs/processing-time.md) — как считается и показывается время
    обработки задачи: таблица состояний, часы браузера, recovery.
- `CHANGELOG.md` — заметные изменения поведения. Каждый коммит с такими изменениями
  дописывает запись в `[Unreleased]`.

---

## Уже принятые решения (Phases 0–10)

Не переоткрывать без причины — за каждым стоит проблема, которая уже была поймана.

### Разделение образов
`backend/Dockerfile` имеет таргеты `runtime` (API: без ML и без ffmpeg) и `worker` (ffmpeg,
torch, pyannote, GigaAM). API-образ ~246 МБ, worker ~2.06 ГБ. API никогда не импортирует код
из `worker/`, а с T13.4 этого кода в его образе и нет: каждый таргет копирует только свои
папки. Следствие: всё, что нужно и API, и воркеру (пути к моделям, схемы), живёт в `app/`.

Слои идут от редко меняющихся к часто меняющимся: apt → общий lock → (worker) ffmpeg →
torch-lock → остальной worker-lock → код. Правка кода не трогает зависимости, бамп pyannote
или GigaAM не пересобирает torch (733 МБ). Кэши pip и npm вынесены в BuildKit cache mount:
в слои они не попадают, а повторная сборка не качает заново то, что уже скачано. Debian
зафиксирован в теге (`python:3.11-slim-trixie`): незаметный переход на следующий релиз
сменил бы и мажорную версию ffmpeg под torchcodec.

### ffmpeg в worker — пакет из Debian, хоть он и тянет LLVM (T13.4)
API ffmpeg не вызывает, поэтому пакет есть только в worker: 408 МБ, из них ~210 МБ — LLVM,
mesa и z3. Их тянут libavdevice и libavfilter, а выкинуть их нельзя: torchcodec, через который
pyannote читает аудио, линкуется со всеми семью библиотеками libav*, включая эти две.
Статический ffmpeg вместе с подачей waveform в pyannote из памяти сэкономил бы ~250 МБ. Но
пришлось бы брать сторонний бинарник или собирать ffmpeg самим, самим же следить за его
обновлениями и заново прогонять `benchmark.equivalence`. Для локальной установки выигрыш
того не стоит.

### Только CPU-колёса torch
`backend/requirements-torch.txt` пинит `torch==2.11.0+cpu`, `torchaudio==2.11.0+cpu`,
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
работа с путями, без ML-импортов, чтобы health-эндпоинт мог отвечать из API-образа. С T13.5
worker в сеть и не может: веса скачивает отдельный сервис `models` (см. ниже).

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
в одном слое Dockerfile, чтобы не остаться в образе. В lock-файл (T13.3) GigaAM не попадает:
у git-зависимости не может быть хэша. Dockerfile ставит её отдельно, той же строкой из
`requirements-worker.txt` и с `--no-deps`. Содержимое закрепляет id коммита, а зависимости
GigaAM лежат в `requirements-worker.lock`.

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
точным срезом кадров, а не вызовом ffmpeg. С T12.2 кадры чанка идут в модель прямо из
памяти, без временного WAV (см. «ASR батчами» ниже).

### Word-level таймстемпы обязательны
Распознаватель возвращает слова с таймкодами, локальными для чанка (`_decode(...,
word_timestamps=True)`, как у `transcribe(path, word_timestamps=True)`); к ним
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

### Живой worker обязан слать heartbeat сам, пока считает задачу
RQ шлёт heartbeat только между задачами: в форкающем воркере родитель продолжает стучать,
пока считает ребёнок, а `SimpleWorker` выполняет задачу прямо в своём процессе, и в Redis
на всё это время не уходит ничего. Ключ регистрации живёт 120 с, а транскрипция идёт
десятки минут — воркер молча исчезал из `Worker.all()` посреди работы.

Потеря при этом невосстановима: `Worker.heartbeat()` пишет только `last_heartbeat`, поэтому
после истечения ключ возвращается с одним этим полем — без `queues`. Воркер навсегда
переставал совпадать со своей очередью, и `/health` показывал «нет воркера» до перезапуска
процесса. Именно это и происходило раньше; списывалось на потерю данных в Redis.

Чинится фоновым потоком `beat_until` в `worker/main.py`, который освежает регистрацию раз
в полпериода. Ошибка Redis в потоке не роняет воркер — следующий удар повторит попытку.

### Мёртвый worker не считается живым по регистрации в Redis
Убитый контейнер не успевает сняться с регистрации, и `Worker.all()` отдаёт его ещё
~2 минуты, пока ключ не истечёт. `/health` из-за этого показывал `ok` при мёртвом воркере,
а UI прятал предупреждение. `check_workers` теперь дополнительно смотрит на
`last_heartbeat`: допускаются два пропущенных удара плюс небольшой запас.

UI страницы задачи тоже опирается на `/health`, а не на тишину в SSE: при мёртвом worker
счётчик «Идёт X» останавливается и появляется предупреждение. Подробности —
[docs/processing-time.md](docs/processing-time.md#мёртвый-worker).

### Удаление задачи не пытается убить выполняющуюся
`DELETE /api/v1/jobs/{job_id}` снимает задачу с очереди, удаляет запись и папку.
Прервать `SimpleWorker` в середине инференса нельзя, поэтому worker просто обнаруживает
пропажу записи на ближайшем `store.update` и выходит без записи `FAILED` — воскрешать
удалённую задачу нельзя. Стадия, упавшая только потому, что у неё забрали файлы, не
пробрасывает исключение в RQ: пользователь сам попросил забыть эту задачу.
SSE отдаёт `event: gone` и закрывает поток, иначе клиент вечно висел бы на keep-alive.

`update` и `replace` читают запись, меняют её и пишут обратно. Обычный `SET` воскрешал задачу,
удалённую между чтением и записью, — со статусом выполняющейся и без файлов. Поэтому запись
идёт через `SET ... XX` (только если ключ ещё есть, атомарно в Redis), а несостоявшаяся
запись — это тот же `JOB_NOT_FOUND`, что и у `require` (T13.6). Recovery пропускает задачу,
удалённую, пока стартует worker, и не роняет старт.

### Бенчмарк сравнивает модели на одной диаризации
`benchmark/run.py` нормализует и диаризует запись ровно один раз, а потом прогоняет по
этим артефактам все модели. Иначе сравнение мерило бы ещё и разброс диаризации, а она
занимает ~85% времени прогона. Всё идемпотентно: добавить третью модель стоит только её
инференса. Чистая логика сравнения лежит в `benchmark/metrics.py` без ML-импортов и
покрыта тестами, как и остальная детерминированная логика pipeline.

### В бенчмарке нет WER, и это не недосмотр
Эталонной расшифровки нет — владелец проекта решил её не делать. Поэтому отчёт измеряет
**расхождение моделей между собой**, а не точность. Совпадение 82% не значит, что у любой
из моделей 18% ошибок: это доля мест, где они разошлись. Не выдавать эту цифру за WER.
«Шумная речь» из T10.3 приближена участками перекрывающейся речи из обычной (не exclusive)
диаризации: без прослушивания это единственный доступный признак трудного участка.

### Default `ASR_MODEL` = `v3_e2e_rnnt` (T10.4)
Замерено на 37-минутном судебном заседании, обе модели в одном процессе:

| | `v3_e2e_rnnt` | `v3_rnnt` |
|---|---|---|
| ASR | 180 с (RTF 0.081) | 209 с (RTF 0.093) |
| пунктуация, регистр, цифры | есть | нет вообще |
| юридические термины | чаще угадывает | реже |

`v3_rnnt` отдаёт сплошной поток в нижнем регистре и числа прописью («сто пятьдесят
девятой» вместо «159»), то есть требовал бы отдельной стадии восстановления пунктуации,
которой в проекте нет. Обратная сторона: `v3_e2e_rnnt` выдал на 141 слово больше в местах
расхождения против 39 у `v3_rnnt` — часть из них вставки, которых в речи не было. Если
когда-нибудь появится эталон, проверять надо именно это.

### Пороги выравнивания измерены и оставлены как есть (T10.5)
`benchmark/thresholds.py` на реальной записи:

- `SPEAKER_MATCH_TOLERANCE_MS=250` — допуск понадобился 13 словам из 3967, и максимальное
  расстояние до интервала было 41 мс. Порог на порядок больше, чем требуется. Не уменьшаю:
  данных одна запись, а ошибка в меньшую сторону даёт слова без спикера, что хуже.
- `BLOCK_SILENCE_GAP_MS=1500` — медиана паузы внутри реплики 120 мс, 90-й перцентиль
  400 мс, 99-й — 2281 мс. Порог лежит между p90 и p99, то есть рвёт блок на действительно
  заметной паузе. 1000 мс дали бы 130 разрывов вместо 74 — заметно мельче.
- `MAX_BLOCK_SECONDS=40` — **18 блоков из 128 (14%) упираются в потолок**, то есть режутся
  по таймеру, а не по смыслу. Это единственный порог, где измерение указывает на проблему.
  Правильное решение — резать длинный блок по самой длинной паузе внутри него, а не по времени;
  это изменение поведения, а не настройка, и в Phase 10 оно не входит.

### Время обработки проставляет `JobStore.update`, а не worker
`started_at` ставится при первом переходе в рабочий статус, `finished_at` — при переходе в
терминальный. Через `update` проходит каждая смена статуса, включая `store.fail` из
recovery, поэтому отметку негде забыть. `RESTING_STATUSES` (терминальные + `QUEUED`) не
считаются началом работы. У записей, созданных до появления полей, они `null` — UI в этом
случае сервер откатывается на `created_at → updated_at` и выставляет
`processing_includes_queue=true`, а UI честно помечает, что это время включает ожидание в
очереди.

Само время считает только сервер: `processing_seconds` — `@computed_field` у `Job`, в
Redis не пишется. UI его не пересчитывает и к выполняющейся задаче прибавляет только время
с момента получения записи, поэтому часы браузера в расчёт не входят. Раньше фронт и бэкенд
считали время по-разному, и расхождение никто не ловил.

Recovery передаёт `finished_at=job.updated_at`: узнаёт она о крахе только при следующем
старте worker, и иначе весь простой контейнера считался бы обработкой. Подробности —
[docs/processing-time.md](docs/processing-time.md).

### Список задач читается сканом, без индекса
`JobStore.iter_jobs` идёт по `SCAN job:*`. Sorted-set индекс пришлось бы задним числом
заполнять для всех записей, созданных до его появления, а на локальной установке задач
десятки. Нечитаемые записи пропускаются, а не роняют список. `recover_orphaned_jobs`
использует тот же обход, но материализует его в список: пометка задачи переписывает ключ,
а мутировать пространство ключей во время скана не стоит.

### Запись без файлов — не результат
Задача, у которой удалили папку, показывалась в списке как «Готово», хотя открыть её
нельзя: ни транскрипта, ни экспорта, ни аудио. `purge_jobs_without_files` при старте worker
удаляет такие записи. Проверяются только терминальные задачи: свежесозданная существует в
Redis на мгновение раньше своей папки, и чистка гонялась бы с загрузкой файла.

### Smoke-тесты
`scripts/smoke-test.{sh,ps1}` поднимают полный цикл задачи на синтетическом тоне. Тон —
не речь, поэтому `DIARIZATION_FAILED` там считается допустимым исходом; любая другая
ошибка означает поломку pipeline.

### Эмбеддинги: один прогон ResNet на окно (T12.1)
`SpeakerDiarization.get_embeddings` в pyannote считает эмбеддинг отдельно для каждой пары
(окно, локальный спикер), то есть гонит одно и то же 10-секундное окно через свёрточный
ствол WeSpeaker ResNet трижды. Маска спикера при этом используется только в statistics
pooling. На CPU этот шаг занимал ~70% всей задачи, и 95% его времени уходило на `conv2d`.
`worker/pipeline/embeddings.py` подменяет метод на экземпляре пайплайна: окно проходит ствол
один раз, а pooling считается сразу по всем спикерам (модель принимает веса
`(batch, speakers, frames)`). Дополнительно свёртки переведены в `channels_last`.

Бит-в-бит эмбеддинги не совпадают и совпасть не могут: FFT в fbank округляет чуть иначе
в зависимости от состава батча. Сам pyannote ведёт себя так же: при смене
`embedding_batch_size` с 32 на 33 эмбеддинги сдвигаются на ~2e-7. Поэтому критерий другой —
итоговая диаризация должна совпасть точно, а эмбеддинги — в пределах 1e-5 (по факту
1.5e-7). Это проверяет `python -m benchmark.equivalence`. Эталоном служит
`load_diarization_pipeline` — стоковый pyannote; оптимизацию ставит только singleton
`get_diarization_pipeline`.

Это переопределение внутреннего метода, поэтому `pyannote.audio` закреплён точной версией.
После обновления pyannote прогнать `benchmark.equivalence`. Если модель эмбеддингов окажется
не WeSpeaker ResNet, установщик оставляет стоковый метод и пишет предупреждение.

### ASR батчами через `forward` + `_decode` (T12.2)
`GigaAMASR.transcribe` берёт один файл за вызов и на каждый запускает ffmpeg. 157 чанков
37-минутной записи означали 157 процессов ffmpeg и 157 одиночных проходов модели.
`GigaAmBatchRecognizer` в `worker/pipeline/asr.py` склеивает `ASR_BATCH_SIZE` чанков в
дополненный нулями батч и вызывает те же `forward` + `_decode`, что и собственный
`transcribe_longform` в GigaAM. На CPU это в 2.2 раза быстрее при батче 8.

Результат точный, а не приблизительный: текст и таймкоды совпадают с `transcribe()`
(`benchmark.equivalence --only asr`). Держится это на двух свойствах закреплённого коммита:
у препроцессора v3 `center=False` (ни один mel-кадр не залезает в паддинг) и энкодер
маскирует паддинг по длинам (`_mask_time`, маски в attention и conv). `_decode` приватный,
поэтому после обновления gigaam проверку надо прогнать заново.

Стадия получает распознаватель через `get_asr_recognizer`, а `transcribe_chunks` работает с
любым объектом по протоколу `Recognizer`. Тесты передают фейк без torch.

### Число потоков torch задаётся до импорта torch (T12.3)
`TORCH_NUM_THREADS` применяет `configure_torch_threads` в `worker/main.py` до прогрева
моделей. Порядок важен. На arm64 свёртки идут через oneDNN/ACL, а их пул потоков создаётся
один раз при старте OpenMP: после первой операции `torch.set_num_threads` до свёрток уже не
доходит, это проверено замером. Поэтому функция выставляет и `OMP_NUM_THREADS` (его OpenMP
читает при загрузке), и `set_num_threads`.

По умолчанию значение не задано: переносимого правильного числа нет. Docker на Apple Silicon
отдаёт все ядра вместе с энергоэффективными. На M3 Pro (11 vCPU) GigaAM работает на 8 потоках
в 1.5 раза быстрее, чем на 11. Эмбеддинги pyannote от числа потоков почти не зависят.

Несколько worker'ов вместо одного на этой машине почти ничего не дают: 2 процесса × 5
потоков — 1.19× суммарной пропускной способности, 3 × 3 — 1.41×, при этом каждая задача идёт
вдвое дольше. К тому же recovery рассчитан на один worker (см. выше).

### Прогресс диаризации идёт из `hook` pyannote (T12.4)
Диаризация занимает ~72% времени задачи. Раньше ей отводилось 5–30% шкалы, и внутри стадии
прогресс не двигался: бар ~20 минут стоял на ~13%, а потом за пару минут ASR проскакивал до
85. Теперь `run_diarization` передаёт pyannote `hook`, и тот отчитывается о каждом батче
сегментации и эмбеддингов. Границы шагов внутри стадии (`SEGMENTATION_END`,
`EMBEDDINGS_END`) и доли стадий в `STAGE_PROGRESS_RANGE` взяты из замера после T12.1/T12.2.
При заметном изменении скорости стадий их надо пересчитать.

hook вызывается на каждый батч, это ~140 раз на 37-минутной записи. В Redis уходит только
сдвиг самого бара: пока задача идёт, UI показывает лишь `progress`.

Исключение из колбэка (`JOB_NOT_FOUND` у удалённой задачи, ошибка Redis) выносится из
pyannote в обёртке `_ProgressFailed` и пробрасывается как есть. Иначе общий
`except Exception` выдал бы его за `DIARIZATION_FAILED` — код, которого запись не
заслужила. Побочный эффект: удалённая задача останавливается посреди диаризации. Раньше
worker узнавал об удалении только после всей диаризации.

### Телеметрия pyannote выключена принудительно (T13.1)
`pyannote.audio` 4 по умолчанию (`metrics_enabled: true` в его `telemetry/config.yaml`)
отправляет на `https://otel.pyannote.ai/v1/traces` отчёты: о загрузке каждой модели и
пайплайна и о каждом обработанном файле — длительность записи, число спикеров, версия, ID
сессии. Аудио и текст не уходят, но правило 1 нарушено. `HF_HUB_OFFLINE` эту отправку не
останавливает. Проверено перехватом экспортёра в контейнере: стоковый пайплайн на одном файле
отправил бы 4 отчёта, пайплайн worker'а — ни одного.

`_disable_telemetry` в `worker/pipeline/models.py` выставляет `PYANNOTE_METRICS_ENABLED=false`
и `HF_HUB_DISABLE_TELEMETRY=1` перед каждой загрузкой. Значения задаются принудительно, а не
через `setdefault`: посторонняя переменная окружения не должна включить отправку обратно.
pyannote читает флаг при каждом отчёте, поэтому порядок импорта не важен. Те же значения
стоят в `ENV` образа worker — для ручных `docker compose run worker python ...`. После
обновления pyannote проверить, что флаг по-прежнему называется так же.

### Сборка воспроизводима: lock-файлы с хэшами (T13.3)
`requirements*.txt` задают диапазоны версий, транзитивные зависимости в них не закреплены
вовсе. Поэтому чистая сборка через месяц получила бы другие numpy, scipy и lightning, а на
numpy и scipy идёт кластеризация диаризации. Phase 12 сверяла результат на одном конкретном
наборе версий, и именно его надо воспроизводить.

Образы ставят пакеты только из `requirements.lock`, `requirements-torch.lock` и
`requirements-worker.lock`, с `--require-hashes --no-deps`. Файл, хэш которого не совпал,
роняет сборку, а лишнего пакета взяться неоткуда. Lock-файлы генерирует
`scripts/lock-deps.sh` (`uv pip compile --universal`): один файл годится и для amd64, и для
arm64. Первый набор версий снят `pip freeze` с образа, проверенного в Phase 12. Отличий два:
`setuptools` закреплён на версии базового образа (его требует torch, а иначе uv взял бы
свежий), и добавились пакеты под маркерами других платформ.

- Правка `requirements*.txt` → `./scripts/lock-deps.sh`: остальные версии не двигаются, uv
  берёт их из прежнего lock.
- `./scripts/lock-deps.sh --upgrade` двигает всё. После этого — `benchmark.equivalence`
  и прогон реальной записи.
- Worker-lock ограничен версиями API-lock (`-c requirements.lock`): общие fastapi, redis и
  rq в обоих образах одинаковые. Torch-lock — подмножество worker-lock, для отдельного слоя.
- Индексы PyPI и PyTorch смешиваются (`unsafe-best-match`): `+cpu`-колёса есть только на
  втором. Это безопасно, потому что каждый файл закреплён хэшем.

Локальный venv для тестов по-прежнему ставится из диапазонов (`requirements.txt` +
`requirements-dev.txt`): ML-зависимостей в нём нет, и точные версии ему не нужны.

### Worker без выхода в интернет (T13.5)
T13.1 показала: библиотека может начать слать данные наружу в минорной версии, а гарантия
«инференс локальный» держалась только на флагах в коде. Теперь её держит сеть. Worker и Redis
подключены только к сети `internal` (`internal: true` в `docker-compose.yml`), у которой нет
маршрута наружу. API подключён и к ней, и к `default`: опубликованному порту нужна обычная
сеть. Frontend — только к `default`, к API ходит браузер, а не контейнер.

Веса скачивает одноразовый сервис `models` — тот же образ worker, `python -m
worker.fetch_models`. Worker ждёт его через `depends_on: service_completed_successfully`. Если
веса на месте, сервис выходит за секунду и даже не импортирует torch. Неудачное скачивание
стек не останавливает: сервис всё равно завершается с кодом 0. Worker стартует, а задачи,
которым нужна недостающая модель, падают со своим стабильным кодом (`HF_TOKEN_REQUIRED`,
`MODEL_NOT_AVAILABLE`), и UI его показывает. Причина остаётся в логах `models`. С ненулевым
кодом worker не стартовал бы вовсе, и пользователь видел бы только «Worker недоступен».

`models` всегда работает с `DEVICE=cpu`: под CUDA-override ему не выдаётся GPU, а
`load_asr_model` проверяет устройство до скачивания.

Бенчмарк тоже запускается в worker (`docker compose run --rm --no-deps worker ...`), то есть
без сети. Вторую модель GigaAM для сравнения сначала скачать:
`docker compose run --rm -e ASR_MODEL=v3_rnnt models`.

---

## Current assignment

Phases 0–8 готовы, Phase 9 пропущена по решению владельца проекта (он проверяет Windows
сам), Phase 10 закрыта частично. Идёт `Phase 11 — Polish`: сделаны T11.1 (список задач и
ссылка на задачу) и T11.2 (время обработки; после ревью оно считается на сервере, см.
[docs/processing-time.md](docs/processing-time.md)). Осталось: вёрстка длинного транскрипта без виртуализации, чистка логов
(~70 строк INFO на задачу), CI для линта и тестов, финальный тест на чистой машине,
просмотр DOCX и PDF глазами.

По запросу владельца отдельно от Phase 11 сделана `Phase 12 — Performance` (T12.1–T12.5 в
`TASKS.md`): запись 37 мин на CPU обрабатывается за 6 мин вместо 22, результат тот же.

Phase 9 не трогать без запроса: CUDA-override и `DEVICE=cuda` остаются непроверенными.

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

### Что добавила Phase 10
- полная 37-минутная запись прошла pipeline: 5 спикеров, 3967 слов, 128 блоков, ни одного
  слова без спикера, 22 минуты на CPU (из них 20 — диаризация);
- обе модели GigaAM сравнены на ней, пороги выравнивания измерены.

### Что добавила Phase 12
- та же 37-минутная запись через API за 364 с вместо 22 мин, с `TORCH_NUM_THREADS=8`;
  результат совпал с Phase 10 до слова, таймкода и спикера;
- `benchmark.equivalence --seconds 0`: диаризация всей записи со стоковым pyannote —
  1014 с, с оптимизацией — 339 с, идентично; ASR всех 157 чанков — идентично.

### Что всё ещё не проверено вживую
- **WER не измерен ни для одной модели** — эталонной расшифровки нет. Всё, что известно о
  качестве, получено сравнением моделей друг с другом;
- бенчмарк построен на одной записи одного жанра (формальная юридическая речь, один
  доминирующий говорящий). На спонтанном многоголосом разговоре выводы могут не повториться;
- вёрстка DOCX и PDF не просматривалась глазами — проверены структура через `python-docx`
  и байты PDF (шрифт, кириллические глифы);
- вёрстка длинного транскрипта в браузере: проверено на 9 блоках, не на 128;
- Windows и CUDA — Phase 9, пропущена. Ускорения Phase 12 замерены только на CPU arm64;
  `channels_last` и батчинг GigaAM на CUDA не проверялись.

Не переходить к Phase 11 автоматически.

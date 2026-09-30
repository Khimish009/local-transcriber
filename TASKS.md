# Local Transcriber — Implementation Tasks

Работать вертикальными инкрементами. Не пытаться сразу реализовать весь ML pipeline и UI одновременно.

---

# Progress

| Phase | Статус |
|---|---|
| 0 — Bootstrap | ✅ сделано |
| 1 — Job infrastructure | ✅ сделано |
| 2 — Audio preprocessing | ✅ сделано |
| 3 — Diarization | ✅ сделано |
| 4 — GigaAM ASR | ✅ сделано |
| 5 — Alignment | ✅ сделано |
| 6 — Exports | ✅ сделано |
| 7 — Result UI | ✅ сделано |
| 8 — Reliability | ✅ сделано |
| 9 — Windows/CUDA | ⏭️ пропущена: владелец проекта проверяет сам на своей Windows-машине |
| 10 — Quality benchmark | 🟡 частично: T10.2/T10.3/T10.4 сделаны, T10.1 закрыт на одной записи вместо трёх |
| 11 — Polish | ⬅️ следующий |

Текущее состояние системы описано в `README.md` («Текущий статус»), принятые технические решения — в `AGENTS.md` («Уже принятые решения»).

Пользовательский сценарий работает end-to-end: загрузка → прогресс → транскрипт с таймкодами и спикерами → переименование → скачивание JSON/TXT/DOCX/PDF. Phase 8 закрыла надёжность: результаты и веса переживают `docker compose down`, сбои дают стабильные коды ошибок, осиротевшая из-за краха worker задача больше не висит вечно, задачу можно удалить вручную.

---

# Phase 0 — Bootstrap

## T0.1 Repository skeleton

Создать структуру:
- `frontend/`
- `backend/`
- `scripts/`
- `data/`
- `models/`

Добавить:
- `.gitignore`
- `.env.example`
- `docker-compose.yml`
- `docker-compose.cuda.yml`

### Done
- repository starts;
- docs remain in root;
- secrets/data/models ignored by Git.

## T0.2 Docker hello-world

Поднять:
- frontend;
- api;
- redis;
- worker.

Добавить healthchecks.

### Done

```bash
docker compose up --build
```

Запускается без ML-моделей и:
- frontend отвечает;
- API `/api/v1/health` отвечает;
- worker видит Redis.

---

# Phase 1 — Job infrastructure

## T1.1 Job model

Реализовать:
- UUID;
- states;
- progress;
- error structure;
- Redis storage.

## T1.2 Upload API

Реализовать:

```http
POST /api/v1/jobs
```

- streaming upload;
- format validation;
- size validation;
- safe filename;
- storage in `data/jobs/<job_id>/source`.

## T1.3 Job status API

```http
GET /api/v1/jobs/{job_id}
```

## T1.4 Worker queue

FastAPI enqueue → RQ → worker.

Сначала dummy job:
- ждёт несколько секунд;
- обновляет progress;
- завершает COMPLETED.

## T1.5 SSE progress

Добавить:

```http
GET /api/v1/jobs/{job_id}/events
```

Frontend должен получать updates.

### Phase 1 Done
Без ML уже можно:
- upload file;
- создать background job;
- видеть progress в UI.

---

# Phase 2 — Audio preprocessing

## T2.1 FFmpeg service

Нормализовать вход в:

```text
16kHz / mono / PCM WAV
```

Сохранять:

```text
work/normalized.wav
```

## T2.2 Metadata

Получать:
- duration;
- source format;
- sample rate;
- channels.

## T2.3 Tests

Проверить MP3/M4A/WAV fixtures.

### Phase 2 Done
Любой поддерживаемый input превращается в стабильный normalized WAV.

---

# Phase 3 — Diarization

## T3.1 Model loader

Добавить model cache и загрузку:

```text
pyannote/speaker-diarization-community-1
```

Не скачивать модель на каждую job.

## T3.2 HF token handling

- читать token только из env;
- понятная ошибка `HF_TOKEN_REQUIRED`;
- token не логировать.

## T3.3 Offline model directory

Если модель уже присутствует в `/models`, использовать локальную копию.

## T3.4 Diarization worker stage

Получать:
- regular diarization;
- exclusive diarization.

Сохранять промежуточный JSON:

```text
work/diarization.json
```

## T3.5 Speaker count

Поддержать:
- auto;
- exact speaker count.

### Phase 3 Done
Для тестового WAV получаем интервалы `SPEAKER_00`, `SPEAKER_01`, ...

---

# Phase 4 — GigaAM ASR

## T4.1 GigaAM model loader

Модель загружается один раз на worker process.

Model name configurable:

```text
ASR_MODEL
```

## T4.2 Speech chunk builder

Из speech intervals сформировать ASR chunks.

Требования:
- не делать тысячи очень коротких chunks;
- merge small gaps;
- max duration configurable;
- сохранять global offset.

## T4.3 ASR

Для каждого chunk:
- run GigaAM;
- получить word timestamps;
- прибавить global offset.

Сохранять:

```text
work/asr_words.json
```

## T4.4 Progress

Обновлять progress по длительности уже обработанных chunks.

### Phase 4 Done
Получаем список слов:

```json
{
  "start": 12.42,
  "end": 12.83,
  "text": "добрый"
}
```

на global timeline.

---

# Phase 5 — Alignment

## T5.1 Speaker assignment

Назначить каждому слову speaker через midpoint и exclusive diarization.

## T5.2 Tolerance

Для пограничных случаев использовать небольшой configurable tolerance.

Если надёжно назначить нельзя:
- `speaker_id = null`.

## T5.3 Segment builder

Группировать слова в transcript blocks:
- смена speaker;
- пауза;
- max block size.

## T5.4 Canonical JSON

Создать:

```text
result/transcript.json
```

### Phase 5 Done
JSON содержит:
- source metadata;
- models;
- speakers;
- words;
- segments.

---

# Phase 6 — Exports

## T6.1 TXT

Readable format:

```text
[00:00:12 – 00:00:18] Спикер 1
Текст...
```

## T6.2 DOCX

Использовать `python-docx`.

В документе:
- title;
- filename;
- duration;
- speaker blocks;
- timestamps.

## T6.3 PDF

Использовать `reportlab`.

Обязательно проверить Cyrillic font.

Docker image должен содержать подходящий Unicode font, например DejaVu Sans.

## T6.4 Regeneration service

Экспорт генерируется только из canonical JSON.

### Phase 6 Done
Все четыре формата доступны:
- JSON
- TXT
- DOCX
- PDF

---

# Phase 7 — Result UI

## T7.1 Transcript viewer

Отобразить:
- timestamp;
- speaker;
- text.

## T7.2 Audio player

Клик по timestamp → seek audio.

## T7.3 Audio Range API

Backend streaming endpoint с Range support.

## T7.4 Rename speakers

UI редактирует names.

Backend:
- обновляет JSON;
- регенерирует TXT/DOCX/PDF;
- НЕ запускает diarization/ASR повторно.

## T7.5 Downloads

Добавить download buttons.

### Phase 7 Done
Полный пользовательский flow работает end-to-end.

---

# Phase 8 — Reliability

## T8.1 Restart persistence

Проверить:
- `docker compose down`;
- `docker compose up`;
- старые results доступны.

Проверено: после `down`/`up` `result/transcript.json` и экспорт доступны, веса моделей не перекачиваются, `/health` снова `ok`.

## T8.2 Failure handling

Проверить:
- corrupted audio;
- unsupported format;
- missing HF token;
- worker crash;
- model load failure;
- FFmpeg failure.

Сделано: все шесть сценариев отдают стабильные коды (`UNSUPPORTED_FORMAT`, `FFMPEG_FAILED`,
`HF_TOKEN_REQUIRED`, `MODEL_NOT_AVAILABLE`, `WORKER_CRASHED`). Долг Phase 4 закрыт:
`recover_orphaned_jobs` при старте worker помечает незавершённые job, за которыми никто
не стоит, как `FAILED` / `WORKER_CRASHED`. Заодно починен `/health`: мёртвый worker больше
не считается живым по устаревшей регистрации в Redis.

## T8.3 Cleanup

Добавить manual delete job.

Автоматический TTL можно оставить post-MVP.

Сделано: `DELETE /api/v1/jobs/{job_id}` и кнопка «Удалить задачу» в UI.

---

# Phase 9 — Windows/CUDA

⏭️ **Пропущена по решению владельца проекта** — он проверяет CPU-режим на своей
Windows-машине сам. Код не трогался: `docker-compose.cuda.yml` остаётся непроверенным,
`DEVICE=cuda` тоже. Заготовка на месте: `_resolve_device` в `worker/pipeline/models.py`
отдаёт `MODEL_NOT_AVAILABLE`, если CUDA запрошена, но недоступна — тихого fallback нет.

## T9.1 CPU Windows smoke test

Проверить на Docker Desktop Windows:

```bash
docker compose up --build
```

## T9.2 CUDA override

Создать `docker-compose.cuda.yml`.

Worker должен видеть CUDA, если host environment поддерживает её.

## T9.3 Runtime device selection

```text
DEVICE=cpu
DEVICE=cuda
```

Fail clearly, если `DEVICE=cuda`, но CUDA недоступна.

Не делать silent fallback без сообщения пользователю.

---

# Phase 10 — Quality benchmark

Инструменты: `backend/benchmark/` — `run` (прогон записей через обе модели на одной
диаризации), `report` (сравнение и разбор расхождений), `thresholds` (калибровка порогов
выравнивания). Логика сравнения чистая и покрыта тестами; ML нужен только для `run`.

```bash
# положить записи в data/benchmark/samples/, затем:
docker compose run --rm --no-deps worker python -m benchmark.run
cd backend && PYTHONPATH=. .venv/bin/python -m benchmark.report \
    --runs ../data/benchmark/runs --terms ../data/benchmark/terms.txt \
    --out ../data/benchmark/report.md
```

Отчёты остаются в `data/` и не коммитятся: они содержат расшифровку пользовательской
записи (AGENTS.md rule 4).

## T10.1 Evaluation samples

Выбрать реальные русскоязычные записи.

🟡 **Частично.** Использована одна запись — судебное заседание, 37 минут, 5 спикеров.
Спецификация требует минимум трёх записей и 30–60 минут суммарно: по длительности норма
выполнена, по разнообразию материала — нет. Всё, что ниже, измерено на одном жанре речи
(юридическая, формальная, один доминирующий говорящий) и на других записях может
выглядеть иначе.

## T10.2 Compare GigaAM variants

Сравнить:
- `v3_e2e_rnnt`;
- `v3_rnnt`.

✅ Сделано. Обе модели прогнаны по одной и той же диаризации и одному `normalized.wav`,
в одном процессе, поэтому времена сравнимы напрямую.

## T10.3 Error notebook/report

Для каждого:
- names;
- numbers;
- jargon;
- noisy speech;
- short replies;
- long monologues.

✅ Сделано, с оговоркой: **WER не измерен**. Эталонной расшифровки нет и делать её владелец
проекта не стал, поэтому отчёт показывает расхождение моделей между собой и материал, на
котором оно возникает, а не абсолютную точность. «Шумная речь» приближена участками
перекрывающейся речи из диаризации — без прослушивания это единственный доступный признак.

## T10.4 Choose default

После реального теста зафиксировать default `ASR_MODEL`.

✅ Зафиксирован `v3_e2e_rnnt`. Он быстрее, даёт пунктуацию, регистр и числа цифрами и
чаще угадывает юридические термины и фамилии. Обоснование — в AGENTS.md.

## T10.5 Калибровка порогов выравнивания

Долг Phase 5: `SPEAKER_MATCH_TOLERANCE_MS`, `BLOCK_SILENCE_GAP_MS` и `MAX_BLOCK_SECONDS`
были подобраны умозрительно. Теперь измерены (`benchmark.thresholds`), значения оставлены
без изменений — см. AGENTS.md. Открытый вопрос: 14% блоков упираются в потолок 40 с и
режутся по таймеру, а не по смыслу.

---

# Phase 11 — Polish

- UI states;
- README;
- clean logs;
- startup instructions;
- smoke scripts for bash and PowerShell;
- basic CI for lint/unit tests;
- final clean-machine test.

---

# Implementation order

Не менять порядок без причины:

```text
0 Bootstrap
→ 1 Jobs
→ 2 Audio
→ 3 Diarization
→ 4 ASR
→ 5 Alignment
→ 6 Exports
→ 7 UI
→ 8 Reliability
→ 9 Windows/CUDA
→ 10 Quality
→ 11 Polish
```

Причина: каждый этап должен оставлять runnable system и минимизировать число одновременно неизвестных проблем.

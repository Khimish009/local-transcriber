# Local Transcriber — Product & Technical Specification

## 1. Назначение

Local Transcriber — локальное приложение для расшифровки русскоязычных записей встреч.

Главные приоритеты:
1. качество распознавания;
2. корректное разделение по спикерам;
3. таймкоды;
4. приватность;
5. переносимость через Docker;
6. удобство использования.

Скорость — вторичный приоритет.

---

## 2. MVP scope

### Входные форматы

Обязательно:
- WAV
- MP3
- M4A

Желательно:
- MP4
- WEBM
- OGG

FFmpeg должен нормализовать всё в единый промежуточный формат:

```text
WAV
mono
16 kHz
PCM s16le
```

Оригинал не изменяется.

### Ограничения MVP

- основной язык: русский;
- один загружаемый файл = одна job;
- одна job обрабатывается одним worker;
- авторизация пользователей не нужна;
- внешние облачные ASR/LLM API запрещены;
- поддержка live-transcription не входит в MVP.

---

## 3. ML pipeline

### 3.1 ASR

Основной кандидат:
- GigaAM v3.

Модель должна быть конфигурируемой через environment:

```text
ASR_MODEL=v3_e2e_rnnt
```

Также необходимо иметь возможность переключиться на:

```text
ASR_MODEL=v3_rnnt
```

Перед фиксацией default-модели выполнить локальный benchmark на реальных русскоязычных записях пользователя:
- минимум 3 записи;
- желательно 30–60 минут суммарно;
- сравнить ошибки в именах, числах, технических терминах, коротких репликах и шумных участках;
- сравнить удобство чтения.

Не оптимизировать только по скорости.

ASR должен возвращать word-level timestamps.

### 3.2 Speaker diarization

Использовать:

```text
pyannote/speaker-diarization-community-1
```

Хранить:
- обычную diarization;
- exclusive diarization.

Для назначения слов спикерам использовать `exclusive_speaker_diarization`, так как она даёт один активный speaker label в каждый момент времени.

Если пользователь знает число спикеров, UI должен позволить передать:
- auto;
- exact speaker count.

Позже можно добавить min/max.

### 3.3 Подготовка speech chunks

Не привязывать ASR жёстко к границам speaker-turn: слишком короткие отрезки могут ухудшить распознавание.

Алгоритм MVP:
1. получить интервалы речи из diarization;
2. объединить небольшие паузы;
3. сформировать ASR chunks;
4. ограничить chunk безопасной максимальной длительностью;
5. транскрибировать chunk;
6. сместить локальные timestamps слов на global audio timeline;
7. назначить каждому слову speaker по midpoint слова и exclusive diarization.

Пороговые значения должны быть конфигурируемыми, например:

```text
MAX_ASR_CHUNK_SECONDS=20
MERGE_SILENCE_GAP_MS=400
```

Не хардкодить эти параметры в business logic.

### 3.4 Speaker assignment

Для каждого слова:

```text
midpoint = (word.start + word.end) / 2
```

Найти exclusive speaker interval, содержащий midpoint.

Если exact match отсутствует:
1. найти ближайший speaker interval в пределах небольшого tolerance;
2. иначе установить `speaker = null`;
3. не придумывать спикера.

### 3.5 Transcript grouping

Слова собираются в readable blocks.

Новый блок начинается при:
- смене speaker;
- большой паузе;
- достижении максимальной длины блока.

Не объединять длинные монологи в один гигантский абзац.

---

## 4. Каноническая модель данных

Источник истины — JSON.

Пример:

```json
{
  "version": 1,
  "job_id": "uuid",
  "source": {
    "filename": "meeting.m4a",
    "duration_seconds": 3522.41
  },
  "language": "ru",
  "asr": {
    "engine": "gigaam",
    "model": "v3_e2e_rnnt"
  },
  "diarization": {
    "engine": "pyannote",
    "model": "speaker-diarization-community-1"
  },
  "speakers": [
    {
      "id": "SPEAKER_00",
      "display_name": "Спикер 1"
    }
  ],
  "words": [
    {
      "start": 12.42,
      "end": 12.83,
      "text": "Добрый",
      "speaker_id": "SPEAKER_00"
    }
  ],
  "segments": [
    {
      "id": "seg-001",
      "start": 12.42,
      "end": 18.73,
      "speaker_id": "SPEAKER_00",
      "text": "Добрый день. Давайте обсудим результаты."
    }
  ]
}
```

Экспорт в TXT/DOCX/PDF всегда генерируется из этого JSON.

Переименование спикера должно изменять только metadata/result JSON и заново генерировать exports. Повторный ASR не нужен.

---

## 5. Job states

Использовать понятный state machine:

```text
QUEUED
PREPARING_AUDIO
DIARIZING
TRANSCRIBING
ALIGNING
GENERATING_EXPORTS
COMPLETED
FAILED
```

Для каждой job хранить:
- status;
- progress 0..100;
- current_stage;
- message;
- error_code;
- created_at;
- updated_at.

### Progress

Пример весов:

```text
PREPARING_AUDIO       0–5
DIARIZING             5–30
TRANSCRIBING         30–85
ALIGNING             85–92
GENERATING_EXPORTS   92–100
```

Во время ASR рассчитывать реальный progress:

```text
processed_audio_duration / total_audio_duration
```

---

## 6. Backend API

Base:

```text
/api/v1
```

### Health

```http
GET /api/v1/health
```

Ответ должен отражать:
- API status;
- Redis status;
- models status;
- worker availability.

### Create job

```http
POST /api/v1/jobs
Content-Type: multipart/form-data
```

Fields:
- `file`
- `speaker_count` optional

Response:

```json
{
  "job_id": "uuid",
  "status": "QUEUED"
}
```

### Job list

```http
GET /api/v1/jobs?limit=50
```

Все задачи, новые сверху. Нужен, чтобы результат оставался доступен после перезагрузки страницы: `job_id` больше не живёт только в памяти вкладки.

### Job status

```http
GET /api/v1/jobs/{job_id}
```

### Delete job

```http
DELETE /api/v1/jobs/{job_id}
```

Удаляет запись job, её место в очереди и всю папку `data/jobs/<job_id>` (исходник, промежуточные артефакты, результаты). Ответ `204`, для неизвестной job — `404 JOB_NOT_FOUND`.

Задачу, которая уже выполняется в worker, прервать нельзя: `SimpleWorker` исполняет её в своём процессе. Очередь и запись исчезают сразу, worker останавливается на ближайшем обновлении статуса.

Автоматический TTL — post-MVP.

### Job events

Предпочтительно SSE:

```http
GET /api/v1/jobs/{job_id}/events
```

Frontend использует SSE для progress.

Polling можно оставить fallback.

### Get transcript

```http
GET /api/v1/jobs/{job_id}/transcript
```

### Source audio

```http
GET /api/v1/jobs/{job_id}/audio
```

Отдаёт исходную запись с поддержкой HTTP Range, чтобы плеер мог перематывать по таймкоду.

### Rename speakers

```http
PATCH /api/v1/jobs/{job_id}/speakers
```

Body:

```json
{
  "SPEAKER_00": "Анна",
  "SPEAKER_01": "Сергей"
}
```

После изменения backend регенерирует exports.

### Download

```http
GET /api/v1/jobs/{job_id}/download/json
GET /api/v1/jobs/{job_id}/download/txt
GET /api/v1/jobs/{job_id}/download/docx
GET /api/v1/jobs/{job_id}/download/pdf
```

### Audio streaming

```http
GET /api/v1/jobs/{job_id}/audio
```

Нужна поддержка HTTP Range, чтобы HTML audio player мог перематывать запись.

---

## 7. Frontend

Маршруты:

```text
/                  — загрузка + список задач
/jobs/<job_id>     — прогресс и результат конкретной задачи
```

У задачи есть собственный URL: перезагрузка страницы и ссылка на результат должны работать.

### Screen 1 — Upload

UI:
- drag & drop;
- file picker;
- selected filename;
- file size;
- speaker count: Auto / 2 / 3 / 4 / ...;
- кнопка «Расшифровать».

Проверить format и max size до upload.

### Screen 2 — Processing

Показывать:
- имя файла;
- текущий этап;
- progress bar;
- процент;
- обработанное время / длительность, если доступно;
- понятное сообщение при ошибке.

### Screen 3 — Result

Показывать:
- audio player;
- список speaker names с возможностью rename;
- transcript blocks;
- start/end timestamp;
- speaker;
- text;
- download buttons.

При клике на timestamp:

```text
audio.currentTime = segment.start
audio.play()
```

### UI MVP

Не тратить время на сложный дизайн.
Приоритет:
- чисто;
- удобно;
- хорошо читается длинный текст;
- responsive desktop;
- корректные loading/error states.

---

## 8. File storage

Структура persistent data:

```text
data/
  jobs/
    <job_id>/
      source/
        original.m4a
      work/
        normalized.wav
      result/
        transcript.json
        transcript.txt
        transcript.docx
        transcript.pdf
```

Модели:

```text
models/
  gigaam/
  pyannote/
  huggingface-cache/
```

Не коммитить `data/` и `models/`.

После `docker compose down` файлы должны сохраняться.

---

## 9. Docker architecture

```text
browser
   │
   ▼
frontend :3000
   │
   ▼
api :8000
   │
   ├──────────────► Redis
   │                  │
   │                  ▼
   └──────────────► worker
                       │
                       ├─ FFmpeg
                       ├─ GigaAM
                       ├─ pyannote
                       └─ exporters
```

Services:
- `frontend`
- `api`
- `worker`
- `redis`

Допустим отдельный one-shot service:
- `model-init`

### Base compose

CPU-only и portable.

### CUDA override

`docker-compose.cuda.yml` изменяет worker runtime/device configuration, не дублируя весь compose.

---

## 10. Repository structure

Рекомендуемая структура:

```text
local-transcriber/
├── README.md
├── SPEC.md
├── TASKS.md
├── AGENTS.md
├── .env.example
├── .gitignore
├── docker-compose.yml
├── docker-compose.cuda.yml
│
├── frontend/
│   ├── Dockerfile
│   ├── package.json
│   └── src/
│
├── backend/
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py
│   │   ├── api/
│   │   ├── core/
│   │   ├── schemas/
│   │   └── services/
│   ├── worker/
│   │   ├── jobs.py
│   │   ├── pipeline/
│   │   │   ├── audio.py
│   │   │   ├── diarization.py
│   │   │   ├── asr.py
│   │   │   ├── alignment.py
│   │   │   └── exports.py
│   │   └── main.py
│   └── tests/
│
├── scripts/
│   ├── smoke-test.sh
│   └── smoke-test.ps1
│
├── data/
│   └── .gitkeep
│
└── models/
    └── .gitkeep
```

---

## 11. Configuration

`.env.example` должен содержать минимум:

```dotenv
APP_ENV=local

API_PORT=8000
WEB_PORT=3000

REDIS_URL=redis://redis:6379/0

DATA_DIR=/data
MODELS_DIR=/models

HF_TOKEN=

ASR_MODEL=v3_e2e_rnnt

MAX_UPLOAD_MB=2048
MAX_ASR_CHUNK_SECONDS=20
MERGE_SILENCE_GAP_MS=400

DEVICE=cpu
```

Секреты не должны попадать в Git.

---

## 12. Errors

Нужны стабильные error codes:

```text
UNSUPPORTED_FORMAT
FILE_TOO_LARGE
FFMPEG_FAILED
MODEL_NOT_AVAILABLE
HF_TOKEN_REQUIRED
DIARIZATION_FAILED
ASR_FAILED
EXPORT_FAILED
JOB_NOT_FOUND
RESULT_NOT_READY
WORKER_CRASHED
```

`RESULT_NOT_READY` (HTTP 409) означает, что job существует, но результат ещё не сформирован. Это не ошибка: UI продолжает показывать прогресс.

`WORKER_CRASHED` означает, что worker-процесс умер во время обработки. Такую job нельзя ни продолжить, ни считать успешной: при старте worker помечает все незавершённые job, за которыми больше никто не стоит, как `FAILED` с этим кодом. Автоматический перезапуск не делается — сбой, вызванный самой записью, повторялся бы бесконечно.

Frontend показывает понятное сообщение, но backend logs сохраняют technical traceback.

---

## 13. Logging

Структурированные logs.

Обязательно:
- job_id;
- stage;
- duration;
- exception.

Не логировать весь transcript по умолчанию.

---

## 14. Tests

### Unit
- timestamp formatting;
- chunk creation;
- speaker assignment;
- segment grouping;
- export generation;
- API schemas.

### Integration
- create job;
- progress transitions;
- rename speaker;
- download exports;
- range audio endpoint.

### Smoke
Небольшой fixture WAV:
1. upload;
2. дождаться COMPLETE;
3. проверить JSON;
4. проверить наличие DOCX/PDF.

ML smoke test можно отделить от обычных unit tests, чтобы CI не скачивал гигабайты моделей.

---

## 15. Quality evaluation task

Перед объявлением MVP готовым подготовить небольшой local evaluation set.

Для каждой тестовой записи вручную проверить:
- пропущенные слова;
- подменённые слова;
- числа;
- фамилии;
- термины;
- diarization mistakes;
- speaker switch delay;
- overlapping speech.

Сравнить как минимум:
- `v3_e2e_rnnt`;
- `v3_rnnt`.

Скорость измерять, но не использовать как главный критерий.

---

## 16. Security / privacy

- filenames sanitization;
- случайные job UUID;
- запрет path traversal;
- ограничение размера upload;
- MIME/extension validation;
- данные не уходят наружу после скачивания моделей;
- `.env` gitignored;
- HF token не возвращается через API;
- CORS только для локального frontend;
- не включать debug traceback в UI.

---

## 17. Non-goals MVP

Не реализовывать на первом этапе:
- users/auth;
- cloud storage;
- collaboration;
- live transcription;
- automatic speaker identification по голосовому профилю;
- LLM-summary;
- translation;
- semantic search;
- Kubernetes;
- PostgreSQL;
- Kafka.

---

## 18. Acceptance criteria

MVP принят, если:

1. На macOS CPU:
   ```bash
   docker compose up --build
   ```
   поднимает систему.

2. На Windows Docker Desktop:
   тот же CPU compose работает без изменения кода.

3. На Windows/Linux NVIDIA:
   CUDA override позволяет worker использовать NVIDIA GPU.

4. После upload русской записи появляется job.

5. UI показывает реальные стадии и progress.

6. Результат содержит speaker labels и timestamps.

7. Переименование speaker не запускает ASR повторно.

8. JSON является source of truth.

9. TXT/DOCX/PDF корректно отображают кириллицу.

10. Контейнеры можно перезапустить, результат остаётся.

11. После первичной загрузки моделей inference может выполняться без доступа к внешним API.

# Local Transcriber

Локальное приложение для расшифровки русскоязычных аудио/видео записей с:
- разделением по спикерам;
- таймкодами;
- отображением прогресса;
- локальной обработкой без отправки записей во внешние API;
- экспортом в JSON, TXT, DOCX и PDF.

## Цель

Пользователь загружает запись встречи в браузере, видит прогресс обработки, получает структурированный транскрипт вида:

```text
[00:00:12 – 00:00:18] Спикер 1
Добрый день. Давайте обсудим результаты.

[00:00:18 – 00:00:27] Спикер 2
Я подготовил данные за последние три дня.
```

После обработки пользователь может:
- прослушивать исходную запись;
- переходить по таймкоду к нужному месту;
- переименовывать спикеров;
- скачать результат в JSON, TXT, DOCX или PDF.

## Технологии

### Frontend
- Next.js
- TypeScript
- React
- простой локальный UI без авторизации

### Backend
- Python 3.11+
- FastAPI
- Redis
- RQ (job queue)
- FFmpeg

### ML
- GigaAM v3 — распознавание русской речи
- pyannote `speaker-diarization-community-1` — diarization
- CPU как обязательный portable-режим
- CUDA как дополнительный режим для Windows/Linux + NVIDIA

### Export
- JSON — канонический результат
- TXT
- DOCX через `python-docx`
- PDF через `reportlab`

## Основной pipeline

```text
Upload
  ↓
Save original
  ↓
FFmpeg normalization
  ↓
Diarization
  ↓
Speech-region/chunk preparation
  ↓
GigaAM ASR + word timestamps
  ↓
Speaker assignment
  ↓
Merge into transcript blocks
  ↓
Save canonical JSON
  ↓
Generate TXT / DOCX / PDF
```

Подробная спецификация находится в `SPEC.md`.
Порядок реализации находится в `TASKS.md`.
Инструкции для coding-agent находятся в `AGENTS.md`.

---

## Текущий статус: Phase 4 — GigaAM ASR

Работают загрузка, подготовка аудио, разделение по спикерам и распознавание речи с word-level таймкодами. Готового транскрипта ещё нет: стадии ALIGNING / GENERATING_EXPORTS в worker — заглушки, которые проходят по настоящему state machine. Слова пока не привязаны к спикерам — это Phase 5.

Работает:
- `docker compose up --build` поднимает `frontend`, `api`, `worker`, `redis`, у всех есть healthchecks;
- `GET /api/v1/health` — статус API, Redis, worker, лимитов загрузки и реального наличия весов моделей;
- `POST /api/v1/jobs`, `GET /api/v1/jobs/{job_id}`, SSE `GET /api/v1/jobs/{job_id}/events`;
- FFmpeg-стадия: метаданные исходника + `work/normalized.wav` (WAV / mono / 16 kHz / PCM s16le), оригинал не изменяется;
- диаризация через `pyannote/speaker-diarization-community-1`: обычная и exclusive разметка сохраняются в `work/diarization.json`, спикеры анонимные (`SPEAKER_00`, ...);
- количество спикеров: auto или точное значение из UI;
- распознавание речи через GigaAM: речевые интервалы диаризации собираются в ASR-чанки (короткие паузы склеиваются по `MERGE_SILENCE_GAP_MS`, длина ограничена `MAX_ASR_CHUNK_SECONDS`), слова получают таймкоды на глобальном таймлайне и сохраняются в `work/asr_words.json`;
- модель ASR выбирается через `ASR_MODEL` (`v3_e2e_rnnt` по умолчанию, `v3_rnnt` как альтернатива), веса качаются в `models/gigaam/` при первой задаче — токен для них не нужен;
- прогресс стадии TRANSCRIBING (30–85) считается по реально обработанной длительности аудио;
- обе модели загружаются один раз на процесс worker и переиспользуются между задачами;
- веса лежат в `models/huggingface-cache/` и `models/gigaam/` и переживают перезапуск; если веса pyannote уже на месте, включается `HF_HUB_OFFLINE=1` и сеть не нужна;
- `HF_TOKEN` читается только из окружения, не логируется и не возвращается через API; без него задача падает с понятным `HF_TOKEN_REQUIRED`.

Следующий этап — `Phase 5 — Alignment` (см. `TASKS.md`).

### Что нужно для диаризации

1. Принять условия модели на https://huggingface.co/pyannote/speaker-diarization-community-1
2. Создать read-токен в настройках Hugging Face.
3. Положить его в `.env`:

```dotenv
HF_TOKEN=hf_...
```

4. `docker compose up -d worker` — при первой задаче веса скачаются в `models/`. Дальше токен не нужен.

Пока весов нет (pyannote или GigaAM), `/api/v1/health` показывает `models: missing`, а UI выводит предупреждение. Веса GigaAM скачиваются с публичного CDN Sber и токена не требуют.

### Быстрый старт

```bash
cp .env.example .env
docker compose up --build
```

Проверка запущенной системы:

```bash
./scripts/smoke-test.sh          # macOS / Linux
pwsh scripts/smoke-test.ps1      # Windows
```

### Локальная разработка без Docker

Backend:

```bash
cd backend
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
PYTHONPATH=. .venv/bin/python -m pytest      # тесты
.venv/bin/ruff check . && .venv/bin/ruff format --check .
REDIS_URL=redis://localhost:6379/0 PYTHONPATH=. .venv/bin/uvicorn app.main:app --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev        # http://localhost:3000
npm run typecheck
```

### Замечания по эксплуатации

- Контейнеры работают от `root`: `/data` и `/models` — это bind mounts (`./data`, `./models`), и фиксированный UID в образе не совпал бы с пользователем хоста на macOS/Windows/Linux одновременно. Приложение локальное, наружу не публикуется.
- Redis запущен с `appendonly yes` и хранит данные в volume `redis-data`. Это нужно, чтобы состояние job и регистрация RQ-worker переживали перезапуск контейнера.
- Если Redis всё же потеряет данные, worker перестанет считаться живым до перезапуска: `docker compose restart worker`.
- Worker использует `SimpleWorker` (RQ без fork) и грузит модели один раз при старте. Обычный `Worker` форкает процесс на каждую задачу, а fork после загрузки torch-модели приводит к зависанию инференса. Плата за это: жёсткий крах внутри инференса роняет worker (его поднимает restart policy), а задача остаётся в незавершённом статусе до перезапуска — автоматическое восстановление таких задач относится к Phase 8.
- Образы разделены: `api` не содержит torch, pyannote и gigaam (604 МБ), ML-зависимости стоят только в `worker`. Версии torch зафиксированы как `+cpu` с индекса PyTorch — обычные PyPI-колёса тянут CUDA-библиотеки на обеих архитектурах.
- GigaAM ставится из репозитория по зафиксированному коммиту: на PyPI до сих пор лежит 0.1.0 без моделей v3 и без word-level таймкодов, к тому же с пином `torch<=2.5.1`.

---

## Перенос на другой компьютер

Проект должен быть спроектирован так, чтобы код и Docker-конфигурация хранились в GitHub, а модели и пользовательские записи — нет.

На новом компьютере:

```bash
git clone <repository-url>
cd local-transcriber
cp .env.example .env
```

В `.env` нужно задать `HF_TOKEN`, если веса pyannote ещё не скачаны.

После этого:

```bash
docker compose up --build
```

Важно: `docker build` только собирает image. Для запуска всей системы используем `docker compose up --build`, потому что нужно одновременно поднять frontend, API, worker и Redis.

После старта UI должен быть доступен по адресу:

```text
http://localhost:3000
```

API:

```text
http://localhost:8000
```

Swagger:

```text
http://localhost:8000/docs
```

## Модели

Веса моделей нельзя коммитить в Git.

Они должны храниться локально, например:

```text
./models/
```

или в Docker volume.

При первом запуске приложение должно:
1. проверить наличие моделей;
2. если моделей нет — скачать их;
3. сохранить их в persistent volume;
4. при следующих запусках использовать локальные веса.

Для `pyannote/speaker-diarization-community-1` пользователь предварительно принимает условия модели на Hugging Face и передаёт `HF_TOKEN`.

`.env`, `models/`, `data/` должны быть в `.gitignore`.

## Portable CPU mode

Обязательный базовый режим:

```bash
docker compose up --build
```

Он должен работать:
- macOS — CPU;
- Windows — CPU;
- Linux — CPU.

Это reference-режим, на котором проверяется переносимость.

## NVIDIA mode

Для Windows/Linux с NVIDIA предусмотреть отдельный compose override:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.cuda.yml \
  up --build
```

На Windows GPU-режим предполагает Docker Desktop + WSL2 + NVIDIA GPU/driver.

CPU-режим должен оставаться рабочим независимо от наличия GPU.

## Privacy

Приложение не должно:
- отправлять аудио во внешние ASR API;
- отправлять транскрипты во внешние LLM/API;
- использовать telemetry для содержимого файлов;
- логировать текст разговора без необходимости.

Внешний интернет разрешён только для первичной загрузки зависимостей и весов моделей.

После загрузки моделей inference должен поддерживать полностью локальную работу.

## Definition of Done

MVP считается готовым, когда на чистой машине можно:
1. клонировать репозиторий;
2. заполнить `.env`;
3. выполнить `docker compose up --build`;
4. открыть UI;
5. загрузить русскоязычный MP3/M4A/WAV;
6. увидеть прогресс;
7. получить текст с таймкодами и спикерами;
8. переименовать спикеров;
9. скачать JSON, TXT, DOCX и PDF;
10. перезапустить контейнеры без повторного скачивания моделей и без потери готовых результатов.

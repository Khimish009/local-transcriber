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

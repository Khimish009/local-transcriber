# START PROMPT FOR CODING AGENT

Ты находишься в корне нового репозитория проекта `Local Transcriber`.

Сначала прочитай полностью:
- `README.md`
- `SPEC.md`
- `TASKS.md`
- `AGENTS.md`

Не начинай с ML-моделей.

Твоя первая задача — выполнить только `Phase 0 — Bootstrap` из `TASKS.md` и получить минимальную, но реально запускаемую систему.

Требования к первой итерации:

1. Создай структуру репозитория согласно `SPEC.md`.
2. Подними через Docker Compose:
   - Next.js frontend;
   - FastAPI API;
   - Redis;
   - Python RQ worker.
3. Добавь:
   - `.gitignore`;
   - `.env.example`;
   - `docker-compose.yml`;
   - начальную структуру `docker-compose.cuda.yml`;
   - Dockerfile для frontend;
   - Dockerfile для backend/worker.
4. Реализуй:
   - `GET /api/v1/health`;
   - проверку доступности Redis;
   - простую страницу frontend, которая показывает статус API.
5. Добавь healthchecks контейнеров.
6. Добавь минимальные backend tests.
7. Не скачивай GigaAM и pyannote на этом этапе.
8. Не реализовывай upload, diarization, ASR и exports до завершения Phase 0.
9. Не добавляй PostgreSQL, Kafka, Kubernetes или другие ненужные сервисы.
10. После реализации самостоятельно проверь проект командами, доступными в текущем окружении.

Ключевой acceptance criterion:

```bash
docker compose up --build
```

должен поднимать рабочую систему.

В конце:
- перечисли созданные/изменённые файлы;
- покажи итоговую структуру репозитория;
- перечисли выполненные проверки и их результат;
- явно напиши, что осталось до `Phase 1`;
- если что-то не удалось проверить, не скрывай это.

После Phase 0 остановись. Не переходи автоматически к Phase 1.

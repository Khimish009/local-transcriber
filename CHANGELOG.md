# Changelog

Формат вдохновлён [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/). Записи — от новых к старым.

## [Unreleased]

### Fixed
- Recovery больше не засчитывает простой worker во время обработки: у упавшей задачи
  `finished_at` теперь равен её последнему обновлению, а не моменту перезапуска worker.
  Подробности — [docs/processing-time.md](docs/processing-time.md).

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

## First implementation assignment

Start with `Phase 0 — Bootstrap`.

Do not implement real ML inference yet.

Deliver:
- repository skeleton;
- `.gitignore`;
- `.env.example`;
- FastAPI service;
- Next.js frontend;
- Redis;
- RQ worker;
- `docker-compose.yml`;
- initial `docker-compose.cuda.yml` placeholder/override structure;
- service healthchecks;
- `/api/v1/health`;
- minimal UI showing backend health;
- basic tests;
- exact startup instructions.

Success condition:

```bash
docker compose up --build
```

starts all services and the browser can open the local app.

Only after Phase 0 is working should you proceed to Phase 1.

# SupportBundleAI

SupportBundleAI helps users turn technical issue reports into controlled diagnostic evidence and concise escalation bundles. It uses an AI-generated collection plan, executes only approved local collectors, sanitizes evidence, and packages verified results for escalation.

## Current status

Phases 1-6 are implemented: FastAPI and Pydantic foundations, SQLite persistence, controlled synthetic scenarios, and deterministic read-only collectors. Collectors use synthetic scenario evidence only; sanitization, policy execution, Groq integration, and real-machine collection are not implemented. Phase 7 will add policy validation and execution limits.

## Technology stack

Python, FastAPI, Pydantic Settings, SQLite, SQLAlchemy 2.x, Alembic, Groq, psutil, PyYAML, Jinja2, and pytest.

## Project structure

- `app/api/`: API route modules.
- `app/models/`: issue, plan, evidence, and bundle models.
- `app/services/`: application workflow orchestration.
- `app/agent/`: AI provider, planning, analysis, and prompts.
- `app/collectors/`: allow-listed deterministic synthetic diagnostic collectors.
- `app/security/`: collection policy and evidence sanitization.
- `app/reporting/`: escalation-summary rendering and templates.
- `app/bundling/`: bundle manifests and ZIP export.
- `app/core/`: configuration, exceptions, and logging setup.
- `app/database/`: SQLAlchemy base, engine, sessions, and ORM records.
- `app/repositories/`: focused transactional persistence operations.
- `app/utils/`: shared utilities.
- `alembic/`: versioned database migrations.
- `controlled_environment/`: synthetic scenarios, logs, and configs used as collector evidence.
- `tests/`: automated tests.
- `output/`: generated bundles and reports.
- `docs/`: project overview, planned architecture, security boundaries, status, and API documentation.

## Setup assumptions

The Python virtual environment and dependencies in `requirements.txt` are expected to be installed. Configuration is loaded from `.env`; set `DATABASE_URL` for SQLite and replace the visible Groq placeholders before future AI integration is used. Diagnostic files remain in controlled filesystem directories; SQLite stores metadata and relative paths only. Application startup does not create tables.

## API

- `GET /`: application metadata and Swagger documentation path.
- `GET /api/health`: API and local-configuration status only.
- Swagger UI: `/docs`.

Groq connectivity, sanitization, unrestricted real-machine collection, and evidence processing are not implemented. Collected synthetic artifacts are not yet sanitized.

## Run the application and tests

Apply or inspect the database migration:

```text
alembic upgrade head
```

```text
alembic current
```

```text
pytest
```

```text
uvicorn app.main:app --reload
```

## Scope limitations

- Controlled synthetic environment.
- Read-only diagnostic collection.
- No unrestricted shell execution.
- No automatic remediation.
- Groq is used only for planning and analysis.
- Local Python code controls tool execution.
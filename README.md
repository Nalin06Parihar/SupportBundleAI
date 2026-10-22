# SupportBundleAI

SupportBundleAI helps users turn technical issue reports into controlled diagnostic evidence and concise escalation bundles. It uses an AI-generated collection plan, executes only approved local collectors, sanitizes evidence, and packages verified results for escalation.

## Current status

Phases 1-14 are implemented for a demo-ready workflow over three synthetic scenarios. The application generates a proposed evidence plan, applies local policy, runs approved read-only synthetic collectors, sanitizes evidence locally, obtains human approval before external analysis, and produces a verified bundle that requires final human release approval.

## Technology stack

Python, FastAPI, Streamlit, Pydantic Settings, SQLite, SQLAlchemy 2.x, Alembic, Groq, psutil, PyYAML, Jinja2, and pytest.

## Project structure

- `app/api/`: API route modules.
- `frontend/app.py`: Streamlit dashboard using only the FastAPI endpoints.
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

## Setup

Install the project dependencies and create the local database schema:

```powershell
uv sync
uv run alembic upgrade head
```

Configure `.env` for the Groq API key/model and local database/output paths. Keep credentials private. The dashboard uses `SUPPORTBUNDLE_API_URL` for the FastAPI base URL and defaults to `http://127.0.0.1:8000`.

## API

- `GET /`: application metadata and Swagger documentation path.
- `GET /api/health`: API and local-configuration status only.
- `GET /api/scenarios` and `/api/workflows` routes for scenario selection, workflow review, approval, and released bundle download.
- Swagger UI: `/docs`.

The Streamlit dashboard communicates with the backend only through the documented HTTP API. Collectors use synthetic scenario data; they do not collect unrestricted real-machine data.

## Run FastAPI and Streamlit (in separate terminals)

Start the FastAPI backend in one terminal:

```powershell
uv run uvicorn app.main:app --reload
```

Start the dashboard in another terminal:

```powershell
uv run streamlit run frontend/app.py
```

Optionally configure a different backend URL before starting Streamlit:

```powershell
$env:SUPPORTBUNDLE_API_URL = "http://127.0.0.1:8000"
```

Run the test suite:

```powershell
uv run pytest
```

## Scope limitations

- Controlled synthetic environment.
- Read-only diagnostic collection.
- No unrestricted shell execution.
- No automatic remediation.
- Groq is used for planning and analysis only after the relevant human approval.
- Human approval is required before evidence is sent for external analysis and before a bundle is released.
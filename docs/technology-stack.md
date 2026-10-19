# Technology Stack

## Current

- **Python**: the requested project target is Python 3.11+, but the existing `pyproject.toml` currently declares `requires-python = ">=3.13"`. This metadata was preserved; reconcile the supported-version target before lowering the configured minimum.
- **FastAPI**: synchronous HTTP API foundation and generated OpenAPI documentation.
- **Uvicorn**: ASGI development server.
- **SQLite**: local persistence for workflow, approval, audit, artifact, collector-run, and bundle metadata.
- **SQLAlchemy 2.x**: typed synchronous ORM mappings and transactional repository operations.
- **Alembic**: versioned schema migrations; application startup does not create tables.
- **Pydantic**: typed validation and response schemas.
- **pydantic-settings**: validated environment and `.env` configuration.
- **psutil**: installed for planned local system collectors; not used for collection yet.
- **PyYAML**: installed for planned configuration/scenario support.
- **Jinja2**: installed for planned escalation-summary rendering.
- **pytest**: API and unit test runner.
- **HTTPX**: test client transport used by FastAPI `TestClient`.
- **Python standard-library logging**: console application logging without a third-party logging service.
- **`pathlib`**: portable filesystem path handling.
- **`hashlib`**: planned SHA-256 manifest generation.
- **`zipfile`**: planned local diagnostic archive creation and verification.
- **`subprocess`**: reserved only for future predefined, allow-listed actions; no subprocess calls are implemented.

## Planned

The AI framework and provider stack selected for the planned workflow:

- **langgraph**: stateful workflow orchestration.
- **langchain-core**: model and message abstractions for the provider adapter.
- **langchain-groq**: ChatGroq adapter for the selected external model provider.
- **Groq Python SDK (`groq`)**: provider SDK used by the planned ChatGroq integration; not called in the current implementation.

- **Streamlit dashboard**: future user interface that will communicate with FastAPI.
- **Regex-based sanitization**: planned initial approach for removing sensitive values from evidence.
- **Optional Presidio integration**: possible future enhancement for identifying sensitive information.

LangGraph will handle state, branching, interruption, resumption, approval points, and supplemental collection loops. Pydantic validates graph state and node inputs and outputs. `langchain-groq` provides the ChatGroq adapter. Local Python code controls all collection, sanitization, policy enforcement, file access, hashing, and packaging. LangGraph checkpointing is not implemented yet.

Do not use `pydantic-ai`; do not add the full `langchain` package unless a concrete future requirement needs it. LangGraph, LangChain Core, and ChatGroq integrations are not implemented yet.

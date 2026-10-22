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
- **langchain-core**: model and message abstractions for structured evidence planning.
- **langchain-groq**: the current `ChatGroq` model adapter; its compatible Groq SDK dependency is managed by the integration.
- **langgraph**: internal workflow state transitions and human plan-approval interrupt.
- **langgraph-checkpoint-sqlite**: official durable SQLite checkpointer (`AsyncSqliteSaver`) for resumable graph state, separate from application business records.
- **`pathlib`**: portable filesystem path handling.
- **`hashlib`**: planned SHA-256 manifest generation.
- **`zipfile`**: planned local diagnostic archive creation and verification.
- **`subprocess`**: reserved only for future predefined, allow-listed actions; no subprocess calls are implemented.

## Planned

The remaining AI workflow stack:

- **Streamlit dashboard**: future user interface that will communicate with FastAPI.
- **Regex-based sanitization**: implemented deterministic local redaction for text/log and JSON artifacts, followed by local verification and residual-risk classification.
- **Optional Presidio integration**: possible future enhancement for identifying sensitive information.

The Phase 8 planner uses `ChatGroq.with_structured_output()` with function calling and the existing `EvidencePlan` Pydantic contract. It produces proposals only: the local policy validator remains authoritative, and the planning service does not execute collectors or send raw evidence. Phase 9/10 use LangGraph for workflow state transitions, the human plan-approval interrupt, local sanitization, residual-risk classification, and the evidence-review interrupt. The workflow UUID is the checkpoint `thread_id`; the official SQLite checkpointer is separate from repository-backed business persistence. The workflow allows one plan-revision cycle and pauses for evidence review after collection and sanitization. Local Python code continues to control collection, sanitization, policy enforcement, file access, hashing, and packaging.

Do not use `pydantic-ai`; do not add the full `langchain` package unless a concrete future requirement needs it. LangChain Core, ChatGroq planning, and LangGraph checkpointed workflow orchestration are implemented.

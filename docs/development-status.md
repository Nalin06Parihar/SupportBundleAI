# Development Status

## Completed

- Phase 1 project scaffold.
- Virtual environment setup.
- Dependency installation.
- Configuration module.
- Custom exception hierarchy.
- Logging foundation.
- Basic FastAPI application declaration.
- Phase 2 FastAPI foundation.
- FastAPI lifespan management.
- Root and health endpoints.
- Basic exception handlers.
- Initial API tests.
- Project documentation folder.
- Phase 3 issue contracts.
- Phase 3 workflow-state contracts.
- Phase 3 evidence-plan contracts.
- Phase 3 policy-decision contracts.
- Phase 3 human-approval contracts.
- Phase 3 collector-result and artifact metadata contracts.
- Phase 3 sanitization and residual-risk contracts.
- Phase 3 analysis contracts.
- Phase 3 bundle metadata contracts.
- Phase 3 standard API error contracts.
- Phase 3 model validation tests.
- Finalized LangGraph architecture documentation.

## Phase 4 implementation

Implemented SQLite persistence with synchronous SQLAlchemy 2.x typed ORM records, session-per-operation repositories, SQLite foreign-key enforcement, FastAPI database-session dependency, and the initial Alembic migration. Application startup initializes and disposes the engine without creating tables. Diagnostic contents and ZIP binaries remain outside SQLite; only metadata and controlled relative paths are stored.

Validation passed:

- `python -m compileall app`
- `alembic upgrade head`
- `alembic current` reports `0001_initial (head)`
- `alembic check` reports no schema differences
- `pytest`: 116 passed

LangGraph checkpoint persistence is not implemented.

## Phase 5: controlled synthetic diagnostic environment

Completed three synthetic scenarios for application startup failure, high CPU usage, and network/dependency timeout. Each includes YAML metadata, a synthetic log, and JSON state/configuration. Added a strict Pydantic scenario contract and safe YAML loader that validates references and confines resolved evidence files to the controlled environment.

Validation passed:

- `python -m compileall app`
- `pytest tests/test_scenarios.py`: 11 passed
- `pytest`: 127 passed

## Phase 6: deterministic diagnostic collectors

Completed an allow-listed registry and eight deterministic, read-only collectors for system information, resource usage, disk usage, process lists, service status, application logs, network information, and application configuration. Collectors read only scenario-declared synthetic data; file access is bounded and artifacts use controlled relative paths. Collected artifacts are marked not scanned and unsanitized. PowerShell and unrestricted real-machine collection remain unimplemented.

Validation passed:

- `python -m compileall app`
- `pytest tests/test_collectors.py`: 13 passed
- `pytest`: 140 passed

## Not implemented

- LangGraph checkpoint persistence.
- Policy engine.
- Sanitization implementation.
- ChatGroq provider.
- LangGraph graph, checkpoints, and interrupts.
- Approval API endpoints.
- Evidence-review UI.
- Evidence analysis.
- Report generation.
- Manifest generation.
- ZIP generation.
- Diagnostic workflow API.
- Streamlit dashboard.

## Next phase

Phase 7: policy validation and execution limits.

## Database migration commands

```text
alembic upgrade head
alembic current
```

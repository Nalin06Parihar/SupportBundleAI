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

## Phase 7: policy and safety validation

Implemented deterministic evidence-plan validation before approval or collection. The policy layer checks the registered collector allow-list, scenario declarations, controlled evidence references and extensions, configured file-size and tool-call limits, bounded collector parameters, JSON-compatible values, unsafe fields and values, and duplicate or equivalent steps. Safe reductions produce `approved_with_changes`; hard violations produce `blocked`. Validation returns Phase 3 decision contracts and does not mutate plans or execute collectors. Human approval cannot override a hard policy block.

Validation passed:

- `python -m compileall app`
- `pytest tests/test_policy.py`: 11 passed
- `pytest`: 151 passed

## Phase 8: ChatGroq evidence planner

Implemented a provider-independent structured chat-model interface and a lazy, reusable ChatGroq adapter. The adapter uses function-calling structured output with the existing `EvidencePlan` contract, configured timeouts and bounded retries, and optional output-token limits. The evidence-planning service builds prompts from issue details, safe synthetic scenario summaries, and only the scenario-supported registry metadata. It returns a validated proposal, logs only safe operational metadata, and maps provider/configuration failures to safe `AIProviderError` messages.

Plans remain proposals: the Phase 7 local policy validator remains authoritative and the Phase 9 workflow calls it before any plan is presented for human approval. The planning service does not invoke policy validation, read scenario evidence files, execute collectors, or send raw evidence to Groq. Application startup does not create or contact a Groq client.

Validation:

- `python -m compileall app`
- `pytest tests/test_planning_service.py`
- `pytest`

## Phase 9: LangGraph workflow and human plan approval

Implemented an internal LangGraph workflow with nodes for workflow initialization, issue/scenario validation, evidence-plan generation, local policy validation, approval-request creation, interrupt/resume, approval-decision processing, approved collection, collection completion, policy rejection, and safe failure handling. The graph routes blocked plans to rejection without creating an approval; approved or partially approved decisions execute only explicitly approved, locally eligible step IDs. Revision requests return to planning for at most one revision cycle.

The human approval request and safe audit event are persisted before the LangGraph interrupt. Decisions are validated against the active workflow, approval, plan, policy decisions, and step IDs before repository finalization or collection. Workflow UUIDs are LangGraph `thread_id` values. Existing business repositories continue to persist workflows, approvals, collector-run status, artifacts, and audit events. The official `langgraph-checkpoint-sqlite` `AsyncSqliteSaver` stores resumable graph state in a separate SQLite checkpoint file; no application business tables are used as checkpoint storage.

Collector runs are reserved with deterministic workflow/step IDs before execution, preventing a resumed graph from re-running a step. Results are persisted as metadata, while collector result data is excluded from graph state. Artifacts remain unsanitized and `not_scanned`. The workflow ends at `collection_completed` or `partial`; sanitization is not run.

Validation:

- `python -m compileall app`
- `pytest tests/test_workflow_graph.py`: 9 passed
- `pytest`: 167 passed

## Phase 10: local sanitization and residual-risk review

Implemented local deterministic sanitization after collection. Bounded structured collector results are staged as unsanitized local artifacts rather than placed in graph state or SQLite. Text/log and JSON artifacts are read through the controlled scenario or output roots, redacted into separate workflow-scoped output paths, independently verified, and registered with sanitized metadata. Originals remain unchanged. Unsupported, oversized, malformed, or unverifiable inputs are blocked. Sanitized excerpts are bounded and carry no raw collector results; the report records redaction counts and safe residual-risk flags.

The graph now advances from collection through sanitization, verification, residual-risk classification, and a persisted evidence-review request, then interrupts at `awaiting_evidence_approval`. The evidence approval request and safe audit events are idempotent. This phase does not finalize evidence-release decisions, send evidence to Groq, or analyze evidence.

Validation:

- `python -m compileall app`
- `pytest tests/test_sanitizer.py tests/test_workflow_graph.py`
- `pytest`

## Phase 11: evidence-release approval and analysis

Implemented evidence-release decision handling at the human-review interrupt. Decisions are checked against the active workflow and approval, sanitized excerpts, owning artifacts, blocked status, reviewer acknowledgement, and explicit external-analysis authorization before finalization. Rejected and cancelled decisions stop without calling the analyzer; revision requests return to the evidence-review boundary.

The external-analysis payload is deterministic and size-bounded. It contains a sanitized issue summary, only explicitly approved sanitized excerpts with traceable artifact/excerpt IDs and source collector names, existing missing-evidence metadata, and safe warnings. Excluded excerpts, blocked artifacts, raw evidence, paths, prompts, and provider responses are not included. Payload reduction retains traceability metadata and records a safe warning.

The separate structured ChatGroq analyzer uses the existing provider abstraction at temperature `0.0` and the `AnalysisResult` contract. A local validation boundary rejects invented or mismatched evidence references, unsupported collectors, unsafe paths or executable next steps, and unexpected fields before analysis is persisted. Analysis records and safe metadata are persisted idempotently; raw evidence and provider responses are not stored.

Insufficient evidence may trigger one supplemental cycle. The supplemental plan passes through the existing local policy validator, human plan approval, approved collectors, local sanitization, and a new evidence-release approval before analysis resumes. A second insufficient result does not trigger another plan and preserves visible limitations. Collection and analysis end before report rendering or bundle creation.

Validation:

- `python -m compileall app`
- `pytest tests/test_analysis_workflow.py`
- `pytest`
- Alembic migration `0002_analysis` adds safe analysis-result persistence and the bounded supplemental-cycle counter.

## Phase 12: report, verified bundle, and final release

Implemented a Jinja2-escaped HTML escalation summary based on validated issue, plan, collection, sanitization, and analysis contracts. Candidate bundle contents are limited to the report, safe JSON summaries, the manifest, and explicitly approved sanitized artifacts.

The deterministic manifest uses the existing `ManifestEntry` contract and streamed SHA-256 hashing. The ZIP is reopened and checked for required and unexpected members, safe relative paths, readable contents, and exact manifest sizes and hashes. Sanitized artifact eligibility is rechecked locally before verification. Only bundles marked verified and sanitized-only can enter final human review.

After verification, the workflow persists one bundle-release approval request and interrupts with safe review metadata. Approval with reviewer acknowledgement records the release and completes the workflow; rejection/cancellation do not release it. One revision request may regenerate a candidate and request approval again. Bundle files stay on disk, while only safe bundle metadata and approval/audit records are persisted.

Validation:

- `python -m compileall app`
- `pytest tests/test_bundling_workflow.py`
- `pytest`

## Phase 13: FastAPI workflow and review endpoints

Implemented scenario listing, workflow creation and safe status views, plan/evidence/bundle approval endpoints, and ZIP download gated on manifest verification, sanitized-only eligibility, and final release approval. The API reuses the existing workflow service and approval contracts; provider failures and workflow errors are returned using safe messages. Status responses omit raw evidence, internal paths, prompts, and provider response data.

Validation:

- `python -m compileall app`
- `pytest tests/test_workflow_api.py`
- `pytest`

## Phase 14: minimal Streamlit dashboard

Implemented a single-file Streamlit dashboard using only the FastAPI workflow endpoints. It lists synthetic scenarios, collects issue details, starts and refreshes workflows, supports policy-aware plan approval and sanitized-evidence release approval, displays structured analysis, and gates bundle approval and ZIP download on the backend's verification and release state. Workflow state is kept in Streamlit session state, and requests use the configurable `SUPPORTBUNDLE_API_URL` value (default `http://127.0.0.1:8000`). The UI uses manual refresh only and displays safe API error summaries.

The root README documents setup and the FastAPI and Streamlit startup commands.

Validation:

- `python -m compileall frontend`
- `pytest`
- Manual end-to-end workflow verification for all three synthetic scenarios

## Database migration commands

```text
alembic upgrade head
alembic current
```

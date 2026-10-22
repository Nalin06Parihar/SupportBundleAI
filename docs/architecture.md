# Planned Architecture

## Component flow

```text
Streamlit dashboard (planned)
        |
        v
FastAPI API
        |
        v
Internal LangGraph workflow service (implemented; no public workflow routes)
        |
        +--> Issue validation
        |
        +--> ChatGroq evidence planner (implemented; proposal only)
        |
        +--> Local policy validator (implemented)
        |       |
        |       +--> Human plan approval interrupt
        |
        +--> Allow-listed collector registry (implemented)
        |
        +--> Local sanitization pipeline (implemented)
        |
        +--> Residual-risk classification (implemented)
        |
        +--> Human evidence-review interrupt (implemented)
        |
        +--> Evidence-release decision validation (implemented)
        |
        +--> ChatGroq evidence analyzer (approved sanitized excerpts only)
        |
        +--> Local analysis-reference validation and evidence-sufficiency routing
        |
        +--> One bounded, fully re-approved supplemental collection cycle
        |
        +--> Jinja2 report rendering (planned)
        |
        +--> Candidate bundle generation (planned)
        |
        +--> Human bundle-release approval interrupt
        |
        +--> SHA-256 verification and final ZIP (planned)
        |
        v
SQLite workflow and audit metadata persistence
        +
Separate SQLite LangGraph checkpoint file (implemented)
```

Pydantic validates request, response, domain, approval, and workflow-state contracts. SQLite stores workflow, approval, audit, artifact, collector-run, and bundle metadata through synchronous SQLAlchemy 2.x repositories. Alembic controls schema migrations. Diagnostic files remain in controlled filesystem directories; application SQLite stores relative paths and metadata only. The official `langgraph-checkpoint-sqlite` `AsyncSqliteSaver` stores resumable graph state in a separate SQLite database; the workflow UUID is its `thread_id`. Checkpoint state contains only JSON-serializable safe metadata, not SQLAlchemy sessions, model clients, collector instances, absolute paths, or unsanitized evidence.

Future frontends must call FastAPI rather than importing backend business logic directly. Groq proposes plans and analyzes only explicitly approved sanitized evidence; local Python code validates requests and controls all collection, sanitization, policy enforcement, file access, hashing, and packaging. Human approval cannot override hard local security blocks.

## Implemented now

The current backend has the FastAPI application, lifecycle management, root metadata route, health route, configuration, safe custom exception mappings, Phase 3 Pydantic contracts, Phase 4 metadata persistence, Phase 5 controlled scenarios, Phase 6 deterministic read-only synthetic collectors, Phase 7 local policy validation, the Phase 8 ChatGroq evidence-planning adapter, the Phase 9/10 internal LangGraph workflow service with local sanitization, and Phase 11 evidence-release approval and structured analysis.

The graph nodes initialize the workflow, validate issue/scenario context, generate a plan, apply local policy, persist pending plan approvals, interrupt for human decisions, validate/finalize those decisions, execute only approved eligible steps, and continue through local sanitization, independent verification, residual-risk classification, and evidence review. Evidence-release approval requires explicit reviewer acknowledgement and external-analysis authorization, and only selected sanitized excerpts enter the size-bounded analysis payload. The analyzer uses structured `AnalysisResult` output; a separate local boundary verifies every artifact/excerpt reference, supported collector name, and safe text before persisting results. Rejection stops before the external analyzer, while revision requests return to the evidence-review boundary. Analysis consumes no raw evidence, and the planning service never executes collectors.

Insufficient evidence can reserve one supplemental cycle. Its proposal passes through the same local policy validation, human plan approval, approved collector execution, sanitization, and evidence-release approval boundaries. If evidence remains insufficient after that cycle, the workflow preserves the analysis limitations without replanning. Collector runs and collection-completion events are keyed by workflow and cycle/step IDs to prevent duplicate execution or audit records on resume. The Phase 11 graph ends at the analysis-complete boundary; report rendering, manifest creation, verified ZIP packaging, and final release approval remain Phase 12 work.

Business persistence and graph checkpointing are separate: repositories own workflows, approvals, artifact metadata, audit events, collector runs, and structured analysis records; `AsyncSqliteSaver` owns resumable graph execution state in its separate SQLite file. The workflow UUID remains the LangGraph `thread_id`. Evidence review pauses at `awaiting_evidence_approval`; the release decision is persisted before approved excerpts are sent for external analysis. Checkpoints and business records contain safe structured metadata only, never raw evidence, provider prompts or responses, or model clients.

## FastAPI lifecycle responsibilities

At startup, the application:

- Loads validated settings.
- Configures standard-library logging.
- Creates the output directory.
- Validates the controlled-environment directory.
- Initializes a database engine and session factory without creating schema tables.
- Logs safe application name, version, and environment.

At shutdown, it disposes the database engine and logs application shutdown. Lifecycle startup must not contact Groq, perform diagnostic collection, run PowerShell, or collect user or machine evidence. Schema creation and upgrades are controlled by Alembic, not startup.

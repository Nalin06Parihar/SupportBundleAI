# Planned Architecture

## Component flow

```text
Streamlit dashboard (planned)
        |
        v
FastAPI API
        |
        v
LangGraph workflow (planned)
        |
        +--> Issue validation
        |
        +--> ChatGroq evidence planner (planned)
        |
        +--> Local policy validator (planned)
        |       |
        |       +--> Human plan approval interrupt
        |
        +--> Allow-listed collector registry (planned)
        |
        +--> Local sanitization pipeline (planned)
        |
        +--> Residual-risk classification
        |
        +--> Human evidence-release approval interrupt
        |
        +--> ChatGroq evidence analyzer (planned)
        |
        +--> Evidence-sufficiency decision
        |
        +--> Optional supplemental collection loop
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
```

Pydantic validates request, response, domain, approval, and future workflow-state contracts. SQLite stores workflow, approval, audit, artifact, collector-run, and bundle metadata through synchronous SQLAlchemy 2.x repositories. Alembic controls schema migrations. Diagnostic files remain in controlled filesystem directories; SQLite stores relative paths and metadata only. LangGraph checkpointing is not implemented.

Future frontends must call FastAPI rather than importing backend business logic directly. Groq proposes plans and analyzes only explicitly approved sanitized evidence; local Python code validates requests and controls all collection, sanitization, policy enforcement, file access, hashing, and packaging. Human approval cannot override hard local security blocks.

## Implemented now

The current backend has the FastAPI application, lifecycle management, root metadata route, health route, configuration, safe custom exception mappings, Phase 3 Pydantic contracts, Phase 4 metadata persistence, Phase 5 controlled scenarios, and Phase 6 deterministic read-only synthetic collectors. Collectors read only scenario-defined evidence and do not inspect the real host. LangGraph, ChatGroq, policy validation, sanitization, workflow routes, reports, ZIP generation, and frontend components remain unimplemented.

## FastAPI lifecycle responsibilities

At startup, the application:

- Loads validated settings.
- Configures standard-library logging.
- Creates the output directory.
- Validates the controlled-environment directory.
- Initializes a database engine and session factory without creating schema tables.
- Logs safe application name, version, and environment.

At shutdown, it disposes the database engine and logs application shutdown. Lifecycle startup must not contact Groq, perform diagnostic collection, run PowerShell, or collect user or machine evidence. Schema creation and upgrades are controlled by Alembic, not startup.

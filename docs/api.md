# API

Only routes currently implemented are documented here.

## `GET /`

Returns non-sensitive application metadata and the Swagger documentation path.

Expected response:

```json
{
  "name": "SupportBundleAI",
  "version": "0.1.0",
  "documentation": "/docs"
}
```

The name and version are loaded from application settings.

## `GET /api/health`

Confirms that the FastAPI application is running and local configuration has loaded.

Expected response:

```json
{
  "status": "healthy",
  "application": "SupportBundleAI",
  "version": "0.1.0",
  "environment": "development"
}
```

The health endpoint:

- Does not test Groq connectivity.
- Does not execute collectors.
- Does not inspect the machine.
- Does not indicate that future diagnostic services are ready.
- Does not expose sensitive configuration.

## API documentation

- Swagger UI: `/docs`
- OpenAPI schema: `/openapi.json`

## Planned API contracts

Phase 3 defines model contracts but does not expose additional routes. Future APIs are expected to support:

- Issue submission and workflow creation.
- Workflow status and safe event summaries.
- Collection-plan approval or rejection.
- Sanitized evidence review and evidence-release approval.
- Candidate bundle metadata and final bundle-release approval.

These contracts are not active endpoints and must not be treated as implemented routes.

# API

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

## `GET /api/scenarios`

Returns scenario ID, title, description, and issue category for the controlled synthetic scenarios. Evidence file paths and evidence contents are not returned.

## `POST /api/workflows`

Accepts an `IssueRequest` with a selected supported `scenario_id`. Starts the existing workflow and returns the workflow ID, status, current stage, and pending approval metadata.

## `GET /api/workflows/{workflow_id}`

Returns safe workflow status, issue summary, proposed plan, collection progress, sanitization summary, structured analysis, missing evidence, pending approval metadata, safe warnings, and errors. Sanitized excerpts are included only at the evidence-review stage. Raw evidence, filesystem paths, prompts, and provider responses are not returned.

The `bundle_available` field is true only after final release approval and workflow completion.

## Approval endpoints

- `POST /api/workflows/{workflow_id}/approvals/plan` accepts a `PlanApprovalDecision` and resumes the pending plan approval.
- `POST /api/workflows/{workflow_id}/approvals/evidence` accepts an `EvidenceReleaseDecision` and resumes the pending evidence-release approval. Excerpts and artifacts are checked against the workflow's sanitized, locally approved evidence.
- `POST /api/workflows/{workflow_id}/approvals/bundle` accepts a `BundleReleaseDecision` and resumes final release approval. Only verified, sanitized-only bundles can be approved for release.

Each endpoint returns the updated safe workflow view.

## `GET /api/workflows/{workflow_id}/bundle`

Downloads the ZIP only after its manifest verification succeeded, it is marked sanitized-only, and final release approval completed. The archive is returned as an `application/zip` file with a safe download filename.

Approval and workflow errors use safe responses. Invalid input returns 422, unknown resources 404, invalid workflow state 409, policy or release violations 403, AI provider failures 502, and safe internal failures 500.

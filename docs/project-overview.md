# Project Overview

## Project name

SupportBundleAI

## Problem statement

Technical support escalations often lack consistent, relevant evidence. SupportBundleAI is intended to guide users through controlled collection of diagnostic evidence and produce a concise escalation bundle without granting an AI provider direct control of the machine.

## Expected outcome

The planned outcome is a verified diagnostic ZIP containing sanitized, relevant evidence, a SHA-256 manifest, and a rendered escalation summary.

## Intended workflow

1. User submits a technical issue.
2. AI proposes an evidence-collection plan.
3. Local policy validates the plan.
4. A human approves the collection plan.
5. Approved local collectors gather evidence.
6. Local deterministic sanitization processes the evidence.
7. Residual sensitivity is identified locally.
8. A human reviews the sanitized evidence.
9. Explicit approval is required before external AI analysis.
10. External AI analyzes only approved sanitized evidence.
11. The workflow may propose supplemental evidence collection.
12. A human approves supplemental collection before it runs.
13. An escalation summary is generated.
14. A candidate bundle is generated.
15. A human approves the candidate bundle before release.
16. The manifest is verified and the bundle is completed.

No raw evidence is sent to Groq automatically. The Phase 3 models define validated contracts for this workflow; orchestration and execution remain unimplemented.

## Current controlled-environment scope

The project is intended to use synthetic scenarios, logs, and configurations in `controlled_environment/` during initial development. The current application validates that this directory exists at startup but does not read or collect its contents.

## Primary user flow

The planned user flow is to describe a technical issue, review a controlled evidence plan, approve permitted collection, and download a sanitized escalation bundle. Issue submission, review, collection, and bundle delivery are not implemented.

## Non-goals

- Unrestricted shell access or arbitrary command execution.
- Automatic remediation of user systems.
- Giving Groq direct access to the local machine.
- Production collection from real user systems during the initial synthetic-environment phase.

## Current limitations

Phase 2 provides application metadata and a local health endpoint. Phase 3 adds domain, approval, API, and future workflow-state contracts. Groq is not contacted; collectors do not run; planning, sanitization, analysis, reporting, persistence, and bundling are not implemented.

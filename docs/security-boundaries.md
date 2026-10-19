# Security Boundaries

These boundaries are mandatory for future implementation:

1. Groq never receives unrestricted access to the local machine.
2. Groq-generated commands must never be directly executed.
3. Groq may request only predefined collector names and validated parameters.
4. Local Python code controls all tool execution.
5. Collector names must come from an allow-listed registry.
6. PowerShell actions must be predefined and allow-listed.
7. Arbitrary shell execution is not permitted.
8. Collection is read-only.
9. Tool calls, timeouts, file sizes, and evidence payloads must be bounded.
10. Paths must be validated against approved directories.
11. Path traversal must be rejected.
12. Raw evidence must be sanitized before it is sent to Groq.
13. Only sanitized evidence may enter the final ZIP.
14. API keys must come from environment configuration and must never be logged or returned.
15. The system does not automatically remediate issues.
16. Initial development uses synthetic controlled-environment data.
17. Startup lifecycle code must not initiate external AI requests or diagnostic collection.
18. Plan approval occurs before any collector executes.
19. Automated sanitization runs locally.
20. Residual sensitivity review occurs before evidence leaves the environment.
21. External AI analysis requires explicit human approval.
22. Humans may exclude additional data but cannot override hard blocks on recognized secrets.
23. Raw evidence is never sent automatically to Groq.
24. Only included sanitized excerpts may be sent externally.
25. Candidate bundle contents require final release approval.
26. Unsanitized originals must never enter the final bundle.
27. Human approval decisions must be auditable.
28. Approval comments must not bypass policy controls.
29. Evidence-release approval and bundle-release approval are separate decisions.
30. SQLite stores workflow, approval, audit, artifact, collector-run, and bundle metadata only.
31. Diagnostic file contents and ZIP binaries remain outside SQLite in controlled filesystem directories.
32. Persisted artifact and bundle paths are relative; absolute paths are rejected.
33. Schema creation and upgrades are Alembic-controlled; application startup must not create tables.

## External-provider trust boundary

Groq is an external service. Any future data sent to it crosses the local-machine trust boundary and must be limited to approved, sanitized information. The provider must not receive secrets, raw evidence, local paths, or execution authority. Local policy and Python code remain authoritative over what may be collected and transmitted.

The current application does not make Groq requests or collect real-machine evidence. Implemented collectors read deterministic synthetic evidence only from files declared by a validated controlled scenario. Their output has not yet been sanitized.

## Human approval boundaries

Plan approval is required before collection. A separate evidence-release decision is required before any sanitized excerpt is sent to the external provider, and final bundle release requires its own approval. Approval can further restrict what is shared, but it cannot override local hard security blocks for recognized secrets. Approval comments are review context only, not policy overrides.

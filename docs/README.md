# SupportBundleAI Documentation

This folder contains authoritative project context for contributors and coding assistants:

- [project-overview.md](./project-overview.md): goals, intended workflow, scope, and limitations.
- [architecture.md](./architecture.md): planned components, boundaries, and data flow.
- [technology-stack.md](./technology-stack.md): current and planned technologies.
- [security-boundaries.md](./security-boundaries.md): mandatory security and trust boundaries.
- [development-status.md](./development-status.md): completed work, outstanding work, and next phase.
- [api.md](./api.md): API routes that currently exist.

Contributors and coding assistants should read the relevant documents before modifying architecture or implementing a new phase. Update the appropriate documentation whenever implementation decisions change.

## Context instructions for coding assistants

1. Read `project-overview.md` before implementing features.
2. Read `architecture.md` before changing module boundaries or data flow.
3. Read `security-boundaries.md` before implementing collectors, AI calls, sanitization, file access, or PowerShell functionality.
4. Read `development-status.md` before starting work to avoid rebuilding completed phases.
5. Read `api.md` before creating or modifying API routes.
6. Update the appropriate documents when an implementation decision changes.
7. Treat the actual source code as the final implementation truth if documentation and code conflict.
8. Do not interpret documentation as permission to weaken security boundaries.

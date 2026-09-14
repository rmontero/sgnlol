# Build assignments

Ten subagents are run in batches because this session permits three concurrent subagents plus the integrating parent.

1. Models, configuration, routing: complete; 21 tests passed.
2. GitHub and Slack normalization/signatures: complete; 25 tests passed.
3. Durable queue and transactional outbox: complete; 9 tests passed.
4. OpenAI Agents SDK scoring: complete.
5. Slack message formatting and delivery: complete.
6. Async worker and retry/fail-safe behavior: complete.
7. Operator CLI, usage and filtered digest: complete.
8. Deployment packaging and onboarding documentation: complete.
9. Security and reliability review with regression fixes: complete.
10. End-to-end integration acceptance tests: complete.

Parent owns shared contracts, FastAPI lifecycle/ingress, environment installation, dependency lock, integration fixes, and final verification.

## Chosen defaults

- FastAPI and SQLite on one always-on process with a persistent local volume.
- Explicit source allowlists, no public dashboard, no OAuth onboarding, no GitHub writes.
- GPT-4.1 mini is configurable. Provider cost and scoring quality require a representative evaluation before claiming the PRD metrics.
- Ten-second fixed batching window; three scoring attempts with backoff; forward with warning after exhausted scoring failures.
- Uncertain Slack send results are held as UNKNOWN for operator reconciliation, never blindly retried.
- Slack ingestion includes new human messages and app mentions in allowlisted channels; thread replies share the thread batching key.
- Secrets are provided at runtime. The build does not post to Slack or call a live model without account setup.

## Final local verification

- Full Python 3.14 suite: 119 passed. One upstream Starlette/AnyIO deprecation warning.
- Ruff lint and formatting checks passed.
- End-to-end acceptance exercises signed FastAPI requests, durable SQLite restart, real scoring/delivery components with mocked provider boundaries.
- GitHub raw-body digest dedup also blocks replay with a changed unsigned delivery header.
- All ten assigned subagents completed; no live Slack or OpenAI calls were made.
- Docker image `sgnlol:local` built successfully with Python 3.14.
- All 119 tests also passed inside the image with `--network none`. Warnings were the upstream deprecation plus pytest cache permissions in the non-root image.
- Container smoke check returned `{"status":"ok"}` from `/healthz`; UID was 10001 and external networking was disabled. Temporary smoke container was removed.

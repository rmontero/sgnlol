# Scoring, storage, and dashboard implementation

User-authorized scope: source Slack channel #ai-tinkerers; escalate relevant flags to @rob; retain GitHub PR ingestion for rmontero/sgnlol. Resolve names to exact IDs before enabling routes.

## Design
Keep a single FastAPI process and persistent SQLite queue. Add read-only, authenticated operator APIs and a packaged HTML/CSS/JS dashboard. Scores remain 0–1 with rationale, summary, token usage and explicit fallback state. Show source filters, search, score ordering, event details, delivery status and current routing. Protect all dashboard content and APIs with independent HTTP Basic credentials over HTTPS. Never expose webhook/API secrets or accept a client-selected recipient.

Preserve existing event history and conservative UNKNOWN delivery behavior. No autonomous reposts. The dashboard displays recorded facts; empty data stays empty. Existing Slack starter files coexist with the deployable triage package.

## Ten bounded agent tasks
1. Storage analytics queries and pagination.
2. Dashboard API and authentication.
3. Responsive dashboard UI.
4. Scoring provenance and regression coverage.
5. CLI inspection and safe backup.
6. Routing provisioning and persistence.
7. Independent security review.
8. End-to-end acceptance tests.
9. Operations and user documentation.
10. UI/accessibility and release review.

The root agent integrates packaging, config, live checks and Railway deployment. Three subagents run concurrently in waves.

## Verification
Restore original regression tests alongside Slack starter tests. Validate storage upgrades against existing data; deny unauthenticated dashboard access; test hostile event text and safe source links; prove scoring/filter/delivery decisions with mocks; build package including static files; check rendered UI; deploy exact release and verify health, authenticated APIs and persistence. Live Slack identity/routing and provider checks must be distinguished from mocked tests.

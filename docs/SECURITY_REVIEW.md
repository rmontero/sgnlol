# Security and reliability review

Reviewed the local ingestion, configuration, routing, scoring, queue, worker, and delivery implementation. This is a code review with deterministic regression tests, not a penetration test or live provider verification.

## Findings corrected during integration

- **P1 — revoked Slack workspace identity could remain authorized.** Routing rechecked channel and destination but omitted the originating workspace. A pending event from an old workspace could survive reassignment of an organization ID. Routing now requires the event workspace to match the current configured workspace. Tests cover queued routing and already-scored pending delivery.
- **P1 — recovered claims could exceed the scoring attempt ceiling.** Expired processing leases and requeues after configuration failures incremented attempts without preventing another model call. The worker now enforces the ceiling before calling the scorer. A regression verifies exhausted recovered work enters the configured dead-letter policy without a model request.
- **Reliability — collapsed output could hide the highest-risk update.** Large batches previously displayed the first 20 events regardless of score. The single collapsed notification now displays the highest-scoring events and explicitly states how many additional updates remain in the durable event log. All events remain stored; the notification intentionally does not reproduce every detail.
- **Reliability — Slack source links were omitted.** Normalization produced a custom-scheme link that the delivery URL filter rejected. Normalization now produces an HTTPS Slack message link; live Slack navigation remains unverified.

- **Replay hardening — GitHub delivery headers are unsigned.** Ingress now stores a digest of the signature-verified body transactionally with the event. Reusing a captured valid body with an altered delivery ID remains deduplicated; the signed ingress regression covers this.

## Verified safeguards

`tests/test_security.py` passes 11 checks covering workspace revocation, exhausted claims, priority preservation, malformed nested webhook values, exclusion of opaque metadata from model input, and mention-safe Slack output. Existing tests separately exercise signed ingress, durable deduplication, route isolation, scoring validation, explicit delivery retry limits, and uncertain delivery outcomes.

The model receives bounded untrusted event fields, has no tools or routing authority, and tracing is disabled. Slack blocks use plain text, fallback mentions are escaped, and link unfurling is disabled. Unexpected post-send failures become `unknown` and require reconciliation rather than automatic reposting. Configuration is revalidated before scoring and sending.

## Operating boundaries

Run one worker process against the SQLite store. Local filesystem/database access is trusted and event content is stored unencrypted; use restricted filesystem permissions and an appropriate host storage policy. Routing changes cannot recall a message already submitted to Slack. Model prompt isolation reduces risk but does not prove classifier resistance to every adversarial input. Provider credentials, real Slack delivery, real model quality, and production deployment have not been verified by this review.

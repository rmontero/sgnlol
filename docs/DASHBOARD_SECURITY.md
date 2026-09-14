# Dashboard security review

Reviewed 2026-09-13: dashboard authentication and API, analytics queries, static UI, scoring provenance, and adjacent worker delivery recovery. This is a local source/test review; it does not certify the deployed routing or real external delivery.

## Actionable findings

### P2: Fallback forwarding inflates measured relevance

`triage/analytics.py:28-36` averages and buckets every stored score, including the worker's fail-safe `score=1, fallback=true`. The dashboard calls the resulting metric “Average relevance,” and its score badge renders a fallback as a critical 100. During a scoring outage, these values suggest measured high relevance despite no successful model assessment. Separate fallback forwarding decisions from relevance averages/buckets and make their badges explicitly distinguishable. Preserve their delivery state and fallback counter. A regression should show that adding a fallback does not change the successful-model relevance average.

### P2: Synthetic policy decisions are labeled model scored

`triage/worker.py:33-39` records a score of zero when routing is revoked without calling the scorer. `triage/static/dashboard/app.js:16` calls any non-fallback numeric score “model scored,” including that synthetic policy record and legacy records with no provenance. Require recorded model provenance for that claim; otherwise show a policy decision or unavailable provenance. Do not invent provenance for legacy records. This also affects aggregate relevance and the assertion that every filtered event was below threshold.

## Verified controls

- Every registered dashboard page, asset, and API uses the same environment-backed Basic authentication dependency. Missing configuration fails closed (503); absent/wrong/malformed credentials fail with 401. Username/password comparisons use constant-time comparisons. The intended deployment is HTTPS.
- Dashboard responses, including authentication failures and probed traversal errors, have `Cache-Control: no-store`; CSP forbids inline scripts, framing, objects by default, and third-party fetches. Referrer policy is `no-referrer`.
- Static asset selection uses a fixed two-file allowlist. Encoded and double-encoded traversal attempts returned 404.
- Untrusted event text, scores, errors, and routing fields are inserted with `textContent`, not HTML parsing. Source links require HTTPS, reject embedded credentials, and use `noopener noreferrer`. No event-controlled URLs are fetched by the server.
- Analytics binds filter/search/ID values as SQL parameters. Sort SQL is selected from fixed constants; page sizes are bounded. Tests cover injection-shaped input. All analytics operations also passed against a SQLite connection with `PRAGMA query_only=ON`.
- APIs project selected fields; provider receipt payloads, opaque event metadata, and application credential settings are not returned. Worker persisted errors are fixed sanitized messages. Dashboard password is excluded from settings repr.
- The model can author only score/rationale/summary. Model name, rubric version, usage, and fallback are assigned by code; extra model output fields are rejected. Scoring has no tools or handoffs and disables tracing.
- Dashboard routes are GET-only and do not authorize delivery/retries or mutate routing. Delivery rechecks current routes; unknown outcomes remain unknown for manual reconciliation instead of being automatically resent.

## Validation

`python -m pytest -q tests/test_dashboard.py tests/test_analytics.py tests/test_scoring_provenance.py tests/test_worker.py`: **42 passed**. One third-party Starlette/AnyIO deprecation warning.

Additional local TestClient probes: three malformed/wrong-scheme Authorization values returned 401 with no-store; encoded and double-encoded asset traversal and SQL-shaped event ID each returned 404 with no-store. No live Slack messages or model requests were made during this review.

## Integration resolution

Both scoring-honesty findings were corrected before release: average and score buckets count only records with model provenance and no fallback; the API exposes model_scored_count. Fallback scores display F rather than 100, sort last under score ordering, and are excluded from minimum-score filtering. Legacy and synthetic no-model records carry unknown-provenance labeling. Filtering metrics say policy filtering rather than assuming every filtered event was below threshold. Regression test test_policy_and_legacy_scores_are_not_measured_model_relevance covers aggregate exclusions; UI reviewer exercised fallback/legacy badge behavior.

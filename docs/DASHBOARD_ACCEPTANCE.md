# Dashboard integration acceptance

The provider-free acceptance suite passed **10 tests** on 2026-09-14 UTC:

```sh
.venv/bin/python -m pytest tests/test_dashboard_acceptance.py -q
.venv/bin/ruff check tests/test_dashboard_acceptance.py
```

The tests use temporary routing and SQLite files, fake workspace/channel/member IDs, signed webhook bodies, the real ingress, worker and dashboard API, and mocked scoring and Slack delivery. No provider calls or messages are sent.

| Behavior | Evidence |
| --- | --- |
| Allowed GitHub PR and Slack message reach the queue before provider work | Signed requests return queued true before scorer/sender calls |
| Scores and rationales persist | Real worker processes each event; authenticated event/detail API and a reopened SQLite connection read the committed score |
| Low relevance remains inspectable without delivery | Score 0.1 is filtered; no batch or send exists |
| Above-threshold events reach the exact configured authority | Score 0.95 creates one batch for the fixture authority; sent state is visible in event and delivery APIs |
| Uncertain delivery is retained without repost | Mock ambiguous failure yields unknown; another tick and duplicate webhook do not send again |
| Duplicate ingress is suppressed | Same signed event returns queued false; scorer and sender remain at their original call counts |
| Unauthorized sources are ignored | Signed events from an unlisted repo/channel never score, send, or enter storage |
| Inspection requires authentication | Anonymous events request is rejected; authenticated reads succeed |
| Older storage remains readable | Frozen original events schema and a score without model/rubric fields open and render through authenticated API |
| Hostile event text stays data | Original markup-like text survives JSON inspection with nosniff; asset source uses textContent and restricts outgoing links to HTTPS |

The frontend check is static asset inspection, not a browser execution/XSS test. Model ranking quality, live webhook subscriptions, production routing, Slack delivery, and Railway volume recovery are not established by these local tests. The run emitted one upstream Starlette/AnyIO deprecation warning; no test failures. Ruff passed.

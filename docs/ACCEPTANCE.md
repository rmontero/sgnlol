# Local acceptance evidence

Run from the repository root:

```sh
.venv/bin/python -m pytest tests/test_acceptance.py -q
```

Verified locally: **4 passed**. The suite exercises actual signature verification,
FastAPI handlers, normalization, routing, disk-backed SQLite storage, the worker,
the `Scorer` adapter, and Slack request rendering/response handling.

| Scenario | Verified behavior |
| --- | --- |
| Enterprise GitHub event | Signed PR webhook is durably queued before scoring; a closed/reopened database resumes work; configured repository recipient receives one notification; replay is deduplicated after restart. |
| Startup Slack event | Signed URL verification succeeds; allowed-channel user message routes to the founder; duplicate delivery and bot messages do not add work. |
| Filtered event | Below-threshold score produces no Slack request and retains score/rationale in the filtered audit view. |
| Same-PR burst | Two distinct webhook deliveries for one PR produce one pending batch and one notification containing two updates after its due time. |

The test replaces `Runner.run` with deterministic structured results and replaces
Slack HTTP transport with `httpx.MockTransport`. Unexpected network requests fail.
It does not replace ingress, storage, worker, scoring adapter, or delivery logic.
The batching test advances the stored due time instead of waiting 60 seconds.

These results establish local integration behavior only. They do not verify live
OpenAI classification quality, Slack token scopes or installation, GitHub webhook
installation, live latency, measured noise reduction, production throughput, or
production availability. Live acceptance requires configured provider credentials,
a public webhook endpoint, and representative labeled events. The remaining unit
and reliability tests cover additional rejection and recovery cases separately.

## Integrated build verification

The final full suite passes 119 tests both in the local Python 3.14 environment and inside the built `sgnlol:local` Docker image with outbound networking disabled. Ruff lint and formatting checks pass. A separate container startup smoke check returned HTTP 200 with `{"status":"ok"}` while running as UID 10001. These checks do not establish live provider delivery or classifier quality.

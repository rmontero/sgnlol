# Receive OpenAI webhook events

Endpoint: `POST https://sgnlol-production.up.railway.app/webhooks/openai`

1. In your OpenAI project's webhook settings, create an endpoint with the URL above and select the event types you need (for example `response.completed`, `response.failed`, or batch events).
2. Copy the signing secret supplied by OpenAI into the Railway service variable `OPENAI_WEBHOOK_SECRET`. This is a webhook signing secret, not the OpenAI API key. The secret change redeploys the service.
3. Send a test event from OpenAI's webhook settings and confirm HTTP 200. Receiving events does not require `OPENAI_API_KEY` or any outbound request.
4. Inspect received metadata with `sgnlol openai-events --json --limit 50` in the deployed environment. The stored event payload is in the `openai_webhook_events` SQLite table; CLI output deliberately omits it.

Verification uses the Standard Webhooks library recommended in the [official OpenAI guide](https://developers.openai.com/api/docs/guides/webhooks). The signed raw body is verified before parsing, including the five-minute timestamp tolerance. Valid envelopes are persisted to the existing Railway volume before acknowledgment. Event and delivery IDs are deduplicated, including across restarts. Unknown future event types are accepted when they have the standard event envelope.

Responses:

- `200 {"received": true, "duplicate": false}`: saved durably.
- `200 {"received": true, "duplicate": true}`: already received; no duplicate receipt.
- `401`: missing, stale, malformed, or incorrect signature.
- `400`: signed payload has invalid JSON or an invalid event envelope.
- `413`: payload exceeds 1 MiB.
- `503`: signing secret is not configured or storage is unavailable. No false success acknowledgment is returned.

This endpoint receives and records OpenAI events. It does not fetch response output, trigger relevance scoring, or send Slack messages. The current scorer uses foreground requests, so it does not automatically generate background-response completion webhooks. The endpoint can receive events from other work in the configured OpenAI project. Event receipts follow the same volume backup and retention requirements as other persisted data.

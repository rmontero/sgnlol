# Signal inbox and operator guide

The dashboard at `/dashboard` reads the events and delivery records stored by the Railway worker. Accounts, roles, and cached reads are described below.

## Sign in and inspect activity

Open [the production dashboard](https://sgn.lol/dashboard). Public HTTP port 80 redirects to HTTPS; the app listens on internal port 8000. Use the dashboard's sign-in form and keep credentials out of URLs and source control.

On an empty accounts table, `DASHBOARD_USERNAME` (default `rob`) and `DASHBOARD_PASSWORD` bootstrap the first admin. Accounts then persist in SQLite; changing those variables does **not** reset existing passwords or restore disabled users. Without any account, access returns 503.

| Role | Permissions | Organization access |
| --- | --- | --- |
| Admin | Events, deliveries, routing, user management, cache status | All organizations |
| Analyst | Events, deliveries, routing | Assigned organization IDs only |
| Viewer | Events and scores | Assigned organization IDs only |

Admins use **Users & access** to create users, change passwords/roles/organization assignments, or disable access. Assign `talacha` for the current installation. New and changed passwords require 12–1024 printable ASCII characters and are stored as salted scrypt hashes. Passwords cannot be retrieved. The last active admin cannot be disabled or demoted. To rotate your own password, save it and sign in again. Browser sessions use a Secure, HttpOnly, SameSite=Strict cookie, expire after 12 hours, and are revoked on account changes or Sign out. Session tokens are stored only as hashes in SQLite. Signing in replaces the previous session for that account. API clients can still supply Basic credentials explicitly, but the server does not issue browser authentication challenges.

Every request checks the account in SQLite, so disabling an account or changing its role affects the next request, including cached reads. Non-admin scope is derived from that account, enforced through parameterized SQL on events, details, aggregates, and deliveries, and included in cache keys. This is application-enforced row isolation: SQLite has no native RLS policies. CLI/database administrators remain trusted and unrestricted. A future PostgreSQL migration can add database-native RLS without changing the role model.

User changes require authenticated admin permission, JSON, a same-origin request, and the dashboard's request-verification header. There is no public signup. Ten incorrect passwords for a username within a minute temporarily throttle sign-in for that username.

- **Signal inbox:** filter by source, processing status, minimum score, or text; sort by newest or highest score. Open an event to read its source content, rationale, summary, model/rubric provenance, fallback marker, and delivery history.
- **Deliveries:** inspect recipient, batching state, attempts, and failures separately from scoring. A completed event does not by itself prove a Slack message was delivered.
- **Routing:** inspect the current allowlist, recipient, and thresholds. This is a read-only view; there is no configuration editor.

Click **Refresh** to fetch current records. The interface does not stream new events automatically. Empty results can mean no allowed events have arrived, or that the selected filters exclude them. Dashboard pages and `/api/dashboard/*` endpoints require the same authentication. Activity inspection is read-only; admin user management writes only account records.

## What scores mean

The worker classifies accepted GitHub and Slack events using OpenAI. It stores the score, rationale, summary, model, rubric version, token usage, and fallback flag alongside processing state. Older records can lack provenance fields.

| Dashboard score | Internal/API/CLI score | Rubric |
| --- | --- | --- |
| 0–39 | 0.00–0.39 | Routine updates, acknowledgments, low-information noise |
| 40–69 | 0.40–0.69 | Useful progress or discussion without a concrete action |
| 70–89 | 0.70–0.89 | Review requests, substantive blockers, actionable questions |
| 90–100 | 0.90–1.00 | Concrete production/security impact or urgent engineering action |

The dashboard rounds to a 0–100 display; routing compares the underlying 0–1 value. At threshold `0.7`, qualifying events are batched for Slack delivery. Below-threshold events remain stored as `filtered`. A **fallback decision** means model scoring failed and the configured failure policy was used; it is not evidence that the model assigned that relevance. Overview figures describe stored activity, not proof of a noise-reduction or accuracy target.

## Authorized route and a real test

The requested production route is Slack workspace `T02CGKDRDV1`, source `#ai-tinkerers` (`C0C18A105S7`), flag recipient `@rob` (`U02C1MHKQF9`), and repository `rmontero/sgnlol`, with threshold `0.7`. Confirm these values in **Routing** after deployment.

1. Ensure the Slack app belongs to `#ai-tinkerers` and its Events API sends `message.channels` to `/webhooks/slack` with Socket Mode disabled. Send a new human-authored message in that channel. Previously ignored messages are not backfilled.
2. For GitHub, register `/webhooks/github` with the matching signing secret and subscribe to supported PR events. Open a PR, request a review, or comment on a PR in `rmontero/sgnlol`. A standalone push, regular issue comment, or ping is not a scored PR event.
3. Inspect GitHub's recent webhook delivery response. `queued:true` means a new event entered the queue. `queued:false` can mean unsupported content, a source outside the allowlist, or a duplicate. HTTP 200 alone does not prove scoring.
4. Refresh the inbox and open the new record. Look for a score and rationale, then inspect **Deliveries** and the recipient's Slack conversation if it crosses the threshold. Allow time for scoring and the short batching window.

## Storage and CLI

Production events live in `/app/data/triage.sqlite3` on the persistent Railway volume. Queue state, scores, filtered records, and delivery batches survive a normal redeploy. OpenAI webhook receipts use a separate table and are not relevance-scored GitHub/Slack events. There is no automatic backfill or dashboard delete/retry action.

Run the following inside the service, or against a local database with `--database PATH` and routing file with `--config PATH`. A local checkout does not automatically inspect production data.

```sh
sgnlol validate-config
sgnlol stats --json
sgnlol events --source slack --sort newest --limit 25 --json
sgnlol events --source github --min-score 0.7 --search review --sort score --limit 25 --json
sgnlol events --status filtered --limit 25 --json
sgnlol inspect-event EVENT_ID --json
sgnlol digest --limit 50 --json
sgnlol batches --json
sgnlol openai-events --limit 20 --json
sgnlol backup /app/data/triage-backup-YYYYMMDD-HHMM.sqlite3
```

`events` supports `--source`, `--status`, `--min-score`, `--search`, `--sort`, and `--limit` (1–100). `inspect-event` takes the stored event ID. `digest` prints filtered records locally and sends nothing to Slack. `batches` lists failed and unknown deliveries for reconciliation.

`backup` creates a consistent SQLite snapshot, including committed WAL data, in a **new private file**. It refuses to replace an existing file or the source database/sidecars. Use a unique destination and transfer the backup off the service volume for recovery from volume loss. Do not simply copy the main SQLite file while the worker is writing.

An `unknown` delivery may already have reached Slack. Inspect Slack and the batch record before reconciliation; never blindly repost an unknown batch. See [OPERATIONS.md](OPERATIONS.md).

## Change routing

`ROUTING_CONFIG_YAML` is the authoritative startup override. The Railway launcher validates it and atomically persists it to `/app/data/orgs.yaml`; invalid YAML stops startup and preserves the previous file. Removing the variable preserves the last persisted YAML. See [RAILWAY.md](RAILWAY.md).

The existing `sgnlol set-threshold ORG_ID 0.7 [--repo owner/name]` command edits that file. If `ROUTING_CONFIG_YAML` remains set, its values overwrite such edits at the next startup. Update the Railway variable when using it as the configuration authority.

## Slack manifest

The installed app uses HTTP Events API with Socket Mode disabled. Its Messages tab is enabled so the configured user recipient can receive flags. The generated starter shortcut and slash command were removed from the deployed manifest because they require Socket Mode or HTTP handlers; the triage service does not implement them. No additional OAuth scopes were requested.

Provider diagnostics record known safe error codes (for example `messages_tab_disabled`) and identify exhausted OpenAI credits without storing raw provider error bodies. Historical failed batches remain failed; fixing configuration does not automatically resend them.

## Redis read cache

Set `REDIS_URL` to the private Railway Redis service reference `${{Redis.REDIS_URL}}`. The cache stores only short-lived dashboard event/overview/delivery responses. `CACHE_TTL_SECONDS` defaults to 5 (allowed 1–60); keys include account permissions, organization scope, request filters, and SQLite change versions. Authentication, users, and routing are always read from their authoritative sources. Entries expire and never replace durable SQLite records or webhook deduplication.

Redis connection/read/write failures fall back to SQLite with short connection timeouts and a five-second retry delay. Without `REDIS_URL`, caching is disabled. Admins can inspect cache hits, misses, and errors in **Users & access** or `/api/dashboard/cache`. Redis stays on Railway's private network with no public TCP proxy. The current deployment is a single app replica with a persistent SQLite volume.

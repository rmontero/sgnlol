# Signal inbox and operator guide

The dashboard at `/dashboard` reads the events and delivery records stored by the Railway worker. This guide describes the new implementation; its production rollout must be verified separately.

## Sign in and inspect activity

Set `DASHBOARD_PASSWORD` privately in Railway, then open [the production dashboard](https://sgnlol-production.up.railway.app/dashboard). HTTP Basic authentication uses username `rob` by default; `DASHBOARD_USERNAME` can override it. An unset password disables access with HTTP 503. Use HTTPS and keep credentials out of URLs, screenshots, and source control.

- **Signal inbox:** filter by source, processing status, minimum score, or text; sort by newest or highest score. Open an event to read its source content, rationale, summary, model/rubric provenance, fallback marker, and delivery history.
- **Deliveries:** inspect recipient, batching state, attempts, and failures separately from scoring. A completed event does not by itself prove a Slack message was delivered.
- **Routing:** inspect the current allowlist, recipient, and thresholds. This is a read-only view; there is no configuration editor.

Click **Refresh** to fetch current records. The interface does not stream new events automatically. Empty results can mean no allowed events have arrived, or that the selected filters exclude them. Dashboard pages and `/api/dashboard/*` endpoints require the same authentication and perform no writes.

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

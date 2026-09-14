# Operations

## Deployment and secrets

Run one Uvicorn worker in one always-on container with persistent local SQLite storage. The checked-in Compose file binds port 8000 to loopback. Put a TLS reverse proxy in front of it, forwarding `/webhooks/github` and `/webhooks/slack`; arrange authentication/network restrictions for operational access. Webhooks authenticate their raw bodies with provider signatures. The application rejects bodies larger than 1 MiB. Synchronize the host clock for Slack's five-minute signature window.

```sh
cp .env.example .env
cp config/orgs.example.yaml config/orgs.yaml
# Edit both files with deployment-specific values before continuing.
docker compose build
docker compose run --rm triage sgnlol validate-config
docker compose up -d
docker compose exec triage sgnlol stats
```

`docker compose` loads `.env` into the process. Database/config paths are explicitly overridden for container mounts. The data volume must be writable by UID 10001. Routing is mounted as a directory so atomic host edits are visible; it is read-only inside the container. Edit routing on the host, validate there with the CLI, then the next ingress/worker operation reloads it. Environment/secret changes require container recreation. Never use `docker compose down -v` on a deployment whose history you need to retain.

The supplied manifest and YAML are templates, not provisioned services. Live provider credentials, registered webhooks, and live provider delivery remain unconfigured. See RAILWAY.md for the deployed infrastructure and verification evidence. Use one Slack workspace/bot token per deployment. Organization records do not select separate provider clients.

## Slack setup

Create a Slack app from `config/slack-manifest.example.json`, replacing the request URL with your public HTTPS `/webhooks/slack` endpoint. Set `SLACK_SIGNING_SECRET` before Slack attempts URL verification. Install the app into the intended workspace and privately set `SLACK_BOT_TOKEN` and `SLACK_BOT_USER_ID`. Set the matching workspace ID in routing.

The manifest requests `chat:write` for notifications, `channels:history` for public-channel message events, and `groups:history` for private-channel message events. Remove `groups:history` and `message.groups` if you only use public channels. Invite the bot to each source and destination channel; configure exact channel IDs. This implementation does not need `chat:write.public`, administrator scopes, user tokens, or direct-message event subscriptions. The code can normalize `app_mention` events, but the template intentionally subscribes only to channel messages, avoiding two subscriptions for a single mention. Interactivity is not required for source URL buttons.

Slack output includes a batch marker in message metadata and HTTPS source links. A notification shows at most 20 event summaries; additional events remain recorded in the durable batch.

## GitHub setup

Register repository or organization webhooks at your public HTTPS `/webhooks/github` endpoint using `application/json`. Generate a strong private webhook secret and set the same value as `GITHUB_WEBHOOK_SECRET`. Subscribe to Pull requests, Pull request reviews, Pull request review comments, and Issue comments. Ordinary issue comments are ignored unless attached to a PR. Configure each accepted `owner/repository` explicitly in YAML; repository ownership must match `github_org`.

No GitHub API token is needed because normalization uses webhook payloads. GitHub ping and unsupported actions receive an accepted response without queueing. Deduplication uses delivery IDs plus a digest of the signature-verified raw body (GitHub delivery headers themselves are unsigned); retain these records through any expected replay period.

## Configuration and CLI

Commands accept `--config PATH`, `--database PATH`, and `--json` before or after their subcommand. Defaults come from `CONFIG_PATH` and `DATABASE_PATH`.

```sh
sgnlol validate-config --config config/orgs.yaml
sgnlol stats --json
sgnlol digest --limit 50
sgnlol batches --limit 100 --json
sgnlol set-threshold example 0.8
sgnlol set-threshold example 0.75 --repo example-org/example-repo
```

`set-threshold` validates and atomically replaces YAML. Run it on the host for a Compose deployment because the container config mount is read-only. An organization threshold applies to Slack and to repositories without explicit overrides. A repository override takes precedence for GitHub. Scores equal to the threshold pass.

Startup organizations require `recipient`; use the founder’s existing bot DM conversation ID (`D…`), or a Slack user ID supported by `chat.postMessage` for App Home messaging. Channel IDs remain available for deliberately configured team destinations. Enterprise GitHub events route to each repository's `recipients`; enterprise Slack events require an explicit organization `recipient`, otherwise they have no destination. A source allowlist and a destination are both required. Removing a source or destination also revokes queued sends when the worker reloads configuration.

Invalid configuration fails closed: startup fails, ingress returns 503, and the worker retains durable state for retry. `stats` reports queue/batch counts and token totals for stored score results; totals do not measure every failed provider attempt. `digest` lists filtered records locally and does not send a daily digest. `batches` lists failed/unknown batches for operators and never resends them. Filter rate is an observed routing statistic, not measured recall or notification reduction.

## Failure recovery

Model scoring is timed out and retried up to `MAX_ATTEMPTS`. The worker caps scoring timeouts at 90 seconds. Default `FAIL_SAFE=forward` emits an explicit fallback after exhaustion; `dead_letter` retains the failed event instead. Investigate missing credentials and provider availability when fallback/dead-letter counts rise. There is no dead-letter replay CLI.

Explicit safe Slack rejections can retry with backoff and rate-limit delays. A timeout after transmission, ambiguous server error, or expired send lease can mean Slack accepted the notification. Such batches are `unknown` and never automatically reposted. Restarting the process preserves this state.

For UNKNOWN reconciliation:

1. Record the batch ID, organization, and destination from `sgnlol batches --json`; retain a database backup.
2. Inspect the destination with an authorized Slack operator. Match the notification's metadata marker, computed as SHA-256 of `organization_id:batch_id`, and its content. Record any confirmed Slack timestamp in the incident record.
3. If delivery is confirmed, a maintainer may use the Store's guarded `mark_sent(batch_id, timestamp)` method during a controlled maintenance session; no reconciliation CLI is provided.
4. If delivery remains uncertain, leave it UNKNOWN. An absent search result/marker is not proof that no write occurred. A deliberate replacement notification requires separate operator authorization and a recorded duplicate-risk decision; there is no automatic resend path.

For failed batches, correct credentials, membership, or configuration and retain incident evidence. Do not edit batch status to `pending` as a routine recovery step.

## Backups, retention, and restore

SQLite WAL mode requires a consistent backup. Do not copy only the live main database file. Use Python's SQLite backup API or stop the service and preserve the database together with its WAL files. Example from the container copies into the durable data directory:

```sh
docker compose exec triage python -c "import sqlite3; src=sqlite3.connect('/app/data/triage.sqlite3'); dst=sqlite3.connect('/app/data/triage.backup.sqlite3'); src.backup(dst); dst.close(); src.close()"
```

Copy completed backups to encrypted off-host storage with restricted access. Establish and test a retention policy appropriate to source content, including backup expiry. There is no automatic record pruning: disk use grows with accepted events. Removing records also removes deduplication history and can permit old webhook replay. Do not prune unresolved/queued work. A retention implementation must preserve referential integrity and a suitable event identity history.

For restore, stop the container, preserve the current volume, restore a consistent backup owned by UID 10001, and restart only one replica. Restoring an older backup can roll back knowledge of sends: reconcile affected pending/sending batches before resuming public ingress or processing. Test restoration on an isolated copy with outbound network disabled to prevent duplicate notifications.

## Health, alerts, and rollout evidence

`GET /healthz` is a process liveness check only; it does not verify configuration, database writeability, worker progress, or provider access. Container health checks use it. Configure external uptime alerting and inspect CLI statistics independently. Alert on sustained queued/processing growth, any UNKNOWN or failed delivery, increasing dead letters/fallbacks, repeated worker warnings, low disk space, and backup failure. Logs avoid raw provider content, but SQLite and filtered digests contain source content and require restricted access.

Before enabling real traffic, verify signed ingress, allowlists, intended destinations, live scoring, Slack delivery, restart persistence, backup restore, and alert routing in your environment. Automated tests use mocked external providers; local success does not establish a deployed system's availability or PRD accuracy/cost targets. No periodic quiet-PR scanner or measured recall/reduction/cost baseline is implemented.

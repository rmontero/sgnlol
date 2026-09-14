# sgnlol

GitHub–Slack relevance triage runs as one always-on Python process. Signed webhooks are normalized into a durable SQLite queue, scored with the OpenAI Agents SDK, filtered against reloadable routing thresholds, and batched into Slack notifications. There is no dashboard.

Railway deployment details and remaining integration setup are in [RAILWAY.md](docs/RAILWAY.md).

## Run locally

Use Python 3.11 or newer. The lock file pins the complete development and runtime environment.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
cp .env.example .env
cp config/orgs.example.yaml config/orgs.yaml
```

Edit `.env` with your own credentials and replace the routing identifiers in `config/orgs.yaml`. Keep both files private. The application reads process environment variables; it does not load `.env` itself. Uvicorn's `--env-file` option loads it for the server:

```sh
uvicorn triage.app:app --env-file .env --host 127.0.0.1 --port 8000 --workers 1
```

For CLI commands, export the same variables into your shell, or use the container commands below. CLI defaults use `config/orgs.yaml` and `data/triage.sqlite3`.

```sh
sgnlol validate-config
sgnlol stats
sgnlol digest
sgnlol batches
```

See [operations](docs/OPERATIONS.md) for provider registration, container startup, thresholds, recovery, backups, and monitoring.

For OpenAI event receipt, configure the [OpenAI webhook endpoint](docs/OPENAI_WEBHOOK.md).

## Behavior

- GitHub PR activity, reviews, review comments, and PR issue comments are accepted only for explicitly configured repositories.
- Slack human messages are accepted only from configured channels and workspace identities. Bot messages and message subtypes are ignored to prevent loops.
- Startup routing uses a single configured recipient. Enterprise routing uses repository recipients; Slack events use the organization recipient when configured.
- Scoring returns a structured score, summary, and rationale. Threshold comparisons are inclusive. Provider content is untrusted and the model has no write tools.
- Short batching windows group updates by organization, recipient, and subject. Durable deduplication suppresses repeated provider event IDs.
- Exhausted scoring retries forward a clearly marked fallback by default; `FAIL_SAFE=dead_letter` changes this behavior. Ambiguous Slack writes enter `unknown` and are never automatically reposted.

## Validation

```sh
python -m pytest
ruff check .
sgnlol validate-config --config config/orgs.example.yaml
```

Unit and integration-style tests use local fixtures and mocked providers. No live Slack/OpenAI API delivery, public webhook setup, or production deployment is implied by these checks. Evaluate recall, notification reduction, token cost, and latency with labeled representative traffic before claiming PRD targets are met.

## Current scope

This is a single-process deployment with one Slack bot token and workspace per deployment. The configuration schema supports organization records and separates stored organization data, but independent Slack workspaces require separate deployments and credentials. Use local durable storage and one replica; do not run this SQLite worker on ephemeral/serverless storage or scale Uvicorn workers.

There is no periodic scanner for quiet or stale PRs, no scheduled digest delivery, and no automated retention job. The digest CLI inspects filtered events locally. Scoring can use age metadata only when a webhook arrives. GitHub context comes from webhook payloads; the service does not fetch complete discussion history.

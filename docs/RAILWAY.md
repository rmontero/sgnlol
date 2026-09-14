# Railway deployment

Project: `sgnlol` (`d7b1b56f-39f2-4d24-807a-5592f092cec0`)
Service: `e935d646-ba05-41a3-9a60-1b85db9d263d`
Environment: `production` (`0dd2f9a5-5a78-4e79-9684-493de1a5f288`)
Volume: `sgnlol-volume` (`9f66c446-5d0f-48d9-9531-12e5fc18ca78`), mounted at `/app/data`

Public URL: https://sgnlol-production.up.railway.app
Railway console: https://railway.com/project/d7b1b56f-39f2-4d24-807a-5592f092cec0/service/e935d646-ba05-41a3-9a60-1b85db9d263d?environmentId=0dd2f9a5-5a78-4e79-9684-493de1a5f288

## Runtime

The service builds from the Dockerfile, uses one replica with sleeping disabled, and checks `/healthz` before rollout. `CONFIG_PATH=/app/data/orgs.yaml`, `DATABASE_PATH=/app/data/triage.sqlite3`, and `PORT=8000` keep routing and events on persistent storage.

The start command is `python -m triage.railway_start`. Railway mounts volumes as root; `RAILWAY_RUN_UID=0` lets the launcher adjust the ownership of the mount and known application files. It then clears supplementary groups and switches to UID/GID 10001 before executing Uvicorn. See Railway's [volume permissions documentation](https://docs.railway.com/volumes/reference). The API runs as a non-root user.

`INITIALIZE_EMPTY_CONFIG=1` is only needed for the initial empty volume. It creates `orgs: []` if the routing file does not exist and never overwrites existing routing. It is now set to `0` after initial setup. The default application startup remains strict: missing or invalid routing stops startup.

## Integration setup

The initial deployment used an empty allowlist. Provider credentials were subsequently configured and checked; an empty allowlist still ignores source events. Configure or maintain these Railway service variables privately: `GITHUB_WEBHOOK_SECRET`, `SLACK_SIGNING_SECRET`, `SLACK_BOT_TOKEN`, `SLACK_BOT_USER_ID`, and `OPENAI_API_KEY`. Use the dashboard or `railway variable set KEY --stdin --service e935d646-ba05-41a3-9a60-1b85db9d263d` to avoid secrets in shell history.

For the new routing support, set `ROUTING_CONFIG_YAML` to the complete YAML document following `config/orgs.example.yaml`. At startup the launcher validates and atomically writes it to `/app/data/orgs.yaml`. Invalid overrides stop startup without replacing the previous file. The environment value is authoritative on every startup; removing it preserves the persisted file. Direct file edits reload without a redeploy, but a remaining environment override replaces them on the next startup, including edits made with `sgnlol set-threshold`.

The authorized source is `#ai-tinkerers` (`C0C18A105S7`) in workspace `T02CGKDRDV1`, with flags sent to `@rob` (`U02C1MHKQF9`). The repository is `rmontero/sgnlol` and the initial threshold is `0.7`. Verify the deployed routing view matches these values before testing. Older ignored messages are not backfilled.

Set `DASHBOARD_PASSWORD` privately to enable `/dashboard`; its HTTP Basic username defaults to `rob` and can be changed with `DASHBOARD_USERNAME`. See [DASHBOARD.md](DASHBOARD.md) for score inspection, delivery states, and SQLite backups. These new capabilities still require a successful production rollout and live checks before being treated as deployed.

Validate remotely when SSH access is available:

```sh
railway ssh --service e935d646-ba05-41a3-9a60-1b85db9d263d -- sgnlol validate-config
```

Register GitHub at `/webhooks/github` and Slack Events API at `/webhooks/slack` under the public URL. Slack Socket Mode must be disabled, `message.channels` subscribed, and the app invited to the allowed source channel. Unsigned webhook requests are rejected with 401. A signed request returning 200 may still be ignored if unsupported, outside the allowlist, or duplicated; inspect queue records and delivery state to verify the full flow.

## Deploy subsequent changes

```sh
railway up --project d7b1b56f-39f2-4d24-807a-5592f092cec0 --environment 0dd2f9a5-5a78-4e79-9684-493de1a5f288 --service e935d646-ba05-41a3-9a60-1b85db9d263d --detach --json
railway deployment list --project d7b1b56f-39f2-4d24-807a-5592f092cec0 --environment 0dd2f9a5-5a78-4e79-9684-493de1a5f288 --service e935d646-ba05-41a3-9a60-1b85db9d263d --json
```

Verify the exact returned deployment ID reaches SUCCESS and the public health endpoint responds. Do not remove the volume. Backups and uncertain-send reconciliation remain described in OPERATIONS.md.

## Initial deployment history

These observations describe the initial rollout, not the current routing or dashboard release.

Initial application deployment `04f045c8-5dab-41e2-bea2-b7724b68bc7d` reached SUCCESS. The HTTPS `/healthz` endpoint returned 200 and both webhook endpoints rejected unsigned requests with 401. The then-current local suite passed 122 tests, including volume initialization, preserving existing routing, and privilege-drop behavior.

Remote SSH inspection was unavailable because the Railway account has no registered SSH key. No key was added. At that stage, application credentials and source routing were unconfigured, and no live GitHub-to-Slack or model calls had been exercised.

The subsequent initial-setup deployment `48f15844-f5e3-4d15-b9de-dd12f73a656a` reached SUCCESS with `INITIALIZE_EMPTY_CONFIG=0`. Its public health returned 200 and unsigned webhook requests returned 401 again. Successful startup with initialization disabled confirms the routing file persisted across the redeploy. The volume mount was read back as `/app/data`.

## OpenAI event receiver

`POST /webhooks/openai` receives signed OpenAI project events. Set `OPENAI_WEBHOOK_SECRET` to the secret OpenAI provides when registering the endpoint. See [OPENAI_WEBHOOK.md](OPENAI_WEBHOOK.md) for setup and receipt inspection. This is independent of the API key used for relevance scoring.

# PRD: GitHub–Slack Relevance Triage Agent

## Problem Statement

GitHub generates a high volume of PR and comment events. When forwarded to Slack unfiltered, high-signal items (blocking review requests, security flags, stalled PRs, direct mentions) get buried in routine noise (nit comments, CI pings, minor pushes). Recipients either mute the channel or miss what matters. Startups have a founder tracking everything with no bandwidth for noise; enterprise clients have a PM or squad leader who needs squad-level signal without reading every event.

## Goals

1. Cut Slack notification volume from GitHub/Slack event traffic by 70–90% while preserving recall on genuinely relevant items.
2. Ship a thin, modular v1 in days, not weeks, running near-zero infra cost at low volume.
3. Support two routing presets out of the box — single recipient (startup founder) and role-based (enterprise PM/squad leader) — configurable per org without code changes.
4. Keep OpenAI spend predictable: score with the cheapest model tier that classifies relevance reliably, not a frontier reasoning model per event.
5. Build on the OpenAI Agents SDK so the client's own team (standardizing on OpenAI) can maintain and extend it without adopting a new agent framework.

## Non-Goals

- **Self-serve multi-tenant app (OAuth install flow, marketplace listing)** — premature before there's more than one or two clients; v1 is config-driven for a small number of orgs.
- **Web dashboard for routing config** — v1 config lives in a file/DB row edited directly; a UI is a P2, and a candidate for CopilotKit's generative-UI layer if it's ever built.
- **Turning off native GitHub/Slack notifications** — this augments, not replaces; disabling native notifications is the client's change-management decision, not this system's job.
- **Write-back to GitHub (comments, labels, approvals, merges)** — v1 is read → score → notify only. No write scope, no write risk.
- **Other chat platforms (Teams, Discord)** — Slack only, per the stated requirement.

## User Stories

**Startup founder (single recipient)**
- As a founder, I want only PRs/comments that need my direct attention (blocking reviews, security flags, stalled PRs) sent to my Slack DM, so I'm not reading every commit notification.
- As a founder, I want setup to take minutes, not engineering time, since I have neither to spare.

**Enterprise PM / squad leader**
- As a squad leader, I want relevant PR activity for my squad's repos routed to our team channel, filtered to squad-level signal, so I can track delivery risk without reading every inline comment.
- As a PM, I want the relevance bar configurable per squad/repo, since teams work differently.

**Team maintaining the service**
- As the maintaining team, I want ingest, scoring, routing, and delivery as separate modules, so any piece (e.g., swapping in Jira later) can change without a rewrite.
- As the maintaining team, I want visibility into what got filtered and why, so I can tune the scoring prompt over time.
- As the maintaining team, I want a defined fail-safe behavior when scoring errors or times out, so a bug doesn't silently swallow something important.

**Edge cases**
- As a recipient, if one PR generates several relevant events in a short window, I want them batched into one update, not spammed as separate messages.
- As a recipient, if GitHub or Slack is briefly unavailable, I want the event queued and retried, not dropped.

## Requirements

### Must-Have (P0)
1. **GitHub webhook ingestion** — signature-verified (HMAC), covering PR opened/updated/closed, review submitted, review comment, and issue comment on a PR. Invalid signatures rejected (401); valid events acknowledged inside GitHub's timeout even though scoring runs async.
2. **Slack webhook ingestion** — Slack Events API, covering messages/mentions in configured channels or threads. The bot's own posts are excluded from ingestion to prevent feedback loops.
3. **Relevance scoring** — each event passed to an OpenAI Agents SDK agent that returns a score plus a short rationale, using a small/fast model by default.
4. **Org-type routing** — org type (startup vs. enterprise) drives default recipient resolution: startup → single DM; enterprise → configured PM/squad-leader per repo or team.
5. **Threshold-gated delivery** — only events above the configured relevance threshold are posted to Slack, formatted with source link, one-line summary, and score/rationale.
6. **Dedup/batching** — multiple relevant events on the same PR within a short window collapse into a single update.
7. **Config store** — org → repos → recipients → threshold, in a flat file or lightweight DB table, editable without a redeploy.
8. **Async-safe reliability** — webhooks ack fast and process off the request path; failed scoring calls retry with backoff and get logged, not dropped.
9. **Secrets handling** — GitHub webhook secret, Slack signing secret/bot token, and OpenAI key live in environment/secrets manager, never in committed config.

### Nice-to-Have (P1)
1. Per-repo/per-team threshold tuning beyond the two org-type presets.
2. A lightweight admin surface (Slack slash command or CLI) to adjust thresholds/recipients without editing raw config.
3. A daily/weekly digest of filtered-out items, so owners can spot-check the filter and build trust in it.
4. A basic usage view — events processed, tokens spent, filter rate — since the data is already logged.
5. GitHub Issues as an additional event source, alongside PRs.

### Future Considerations (P2)
1. Web dashboard for non-technical org admins — candidate for a CopilotKit-style generative-UI layer if a real UI becomes necessary.
2. Self-serve multi-tenant onboarding (GitHub App + Slack App OAuth install) if this becomes a product sold to many orgs.
3. Additional sources (Jira, Linear, GitLab) through the same ingest → score → route pipeline.
4. Feedback loop — recipients react to a delivered message to fine-tune the scoring prompt/threshold over time.
5. Write-back actions (agent comments/labels on PRs) — deliberately deferred from v1.

## Success Metrics

**Leading**
- Slack message volume reduction vs. raw webhook volume: target 70–90%, measured weekly per org.
- Relevance precision (recipient marks a delivered message as worth surfacing): target ≥85%.
- Delivery latency, webhook receipt → Slack post: target <60s p95.
- OpenAI cost per event scored: target ceiling to be set once model tier and budget are confirmed (see Open Questions).

**Lagging**
- Recipient-reported "missed something important" incidents, checked qualitatively at 30/60 days.
- Org doesn't mute or disable the integration within the first 60 days.

## Open Questions
- What exactly feeds the Slack-side ingestion — all messages in designated channels, only mentions/threads on existing PR notifications, or something narrower? *(client/product)*
- Is there an existing relevance rubric, or should v1 propose one (blocking review, security label, stale >48h, direct owner mention, CI failure on main)? *(client/product)*
- Single client first with an architecture that generalizes, or multi-org from day one? Determines whether config starts as a flat file or a proper isolated DB schema. *(engineering)*
- Fail-safe default when scoring errors or times out — drop, log-only, or forward-with-warning? *(engineering/client)*
- Confirmed OpenAI model tier and monthly budget ceiling, to lock in the cost target. *(client)*
- Hosting constraints — existing cloud account/compliance requirements, or is a low-cost PaaS acceptable for v1? *(client)*

## Timeline Considerations

No hard external deadline given. Suggested phasing:
- **Phase 1 (1–2 wks):** GitHub ingestion + scoring + single-recipient Slack delivery — thinnest end-to-end slice, validates the startup case.
- **Phase 2 (+1 wk):** Slack-side ingestion, dedup/batching, enterprise routing (PM/squad-leader resolution).
- **Phase 3 (+1 wk):** Reliability hardening (retries, dead-letter logging), threshold config, filtered-items digest.

Dependency: GitHub App/webhook credentials and a Slack App (bot token + Events API signing secret) must exist before Phase 1 can be tested end-to-end.

## Recommended Stack

**Language: Python.** The OpenAI Agents SDK ships first-class in both Python and TypeScript, but Python has the more mature ecosystem for this kind of thin webhook-glue service (FastAPI, Slack Bolt, PyGithub). If the team that will maintain this is more fluent in TypeScript day to day, the TS Agents SDK + Fastify/Express + Slack Bolt for JS is an equally valid substitute — pick whichever the maintainers already know, since this stays a thin service either way.

**Framework:** FastAPI, single process. No orchestration framework needed at this scale.

**Pipeline:**
- GitHub → webhook → FastAPI route → queue (in-process asyncio is fine at low volume; Redis/SQS if durability across restarts matters) → Agents SDK scoring agent → Slack `chat.postMessage`.
- Slack → Events API webhook → same FastAPI service, different route → same scoring pipeline → Slack delivery.

**Storage:** Postgres (or SQLite for a true single-tenant MVP) for org/routing config and an event log (powers the dedup window and the filtered-digest feature). No vector DB or heavier data infra needed for this scope.

**Hosting:** Given frugality is explicit, a small always-on container on Fly.io, Railway, or Render (with their managed low-cost Postgres) is cheaper and simpler to operate than Lambda cold-start handling for webhook SLAs. AWS is a reasonable alternative if the client already has AWS infra/credits, but Kubernetes is overkill at this scale and works against the "keep it simple, frugal" goal — don't reach for it here.

**On CopilotKit and Ambiguous.ai as starting points:** neither is a strong fit as the *foundation* for this scope.
- **CopilotKit** is a frontend agentic-UI framework (AG-UI protocol) for embedding rich human-in-the-loop chat/generative UI into a product. It's genuinely useful if the P2 admin dashboard gets built, but it doesn't simplify webhook ingestion or Slack delivery — the actual integration surface here.
- **Ambiguous.ai** is a full AI-native workspace suite (docs, sheets, CRM, chat, etc.) with an MCP-based agent surface, built to replace a stack of SaaS tools. Routing a scored GitHub event through a whole workspace platform to post a Slack message adds cost and surface area without solving any part of this problem better than calling the Slack Web API directly.

Recommendation: build directly against the GitHub and Slack APIs plus the OpenAI Agents SDK. Revisit CopilotKit specifically if/when the P2 dashboard becomes real scope.
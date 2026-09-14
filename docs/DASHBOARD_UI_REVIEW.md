# Dashboard UI release review

Reviewed the dashboard static assets during the scoring/storage/dashboard build.

## Changes

- Policy fallback decisions display `F`, with an accessible description and an explicit explanation that no model relevance assessment succeeded. They no longer look like a model relevance score of 100.
- Historical numeric scores without model provenance display `?`, retain their recorded value, and explain that provenance is unavailable. Only a stored model identifier earns the “model scored” label.
- Model relevance average and distribution explain that they include verified model scores only. The separate fallback count remains visible.
- The filtered metric says “Not forwarded” because routing policy changes can filter an event independently of its score.
- Stored input/output token totals and per-event usage are visible, with a limitation explaining that failed attempts may not be recorded.
- Routing cards show source channels, flag recipients, thresholds, and repository rules in readable fields. The user-confirmed IDs display as `#ai-tinkerers (C0C18A105S7)` and `@rob (U02C1MHKQF9)`; these are display aliases, not permission or routing changes.
- Pagination disables during requests and remains disabled on errors, preventing stale next/previous controls from advancing offsets. Generation checks already suppress stale responses. An empty page now has an accurate label.
- Loading regions expose `aria-busy`; score badges have accessible labels and hover descriptions. The dialog retains its native focus management, Escape handling, and explicit close button. Backdrop click detection covers each edge.
- Muted event text contrast was strengthened. JavaScript, CSS, and HTML were formatted with an already-cached Prettier executable; no package or framework dependency was added.

## Verification

- `node --check triage/static/dashboard/app.js`: passed.
- A Node VM exercise with a minimal DOM stub passed fallback, legacy, verified-model and unscored badge cases; rejected JavaScript and credential-bearing source URLs; and verified the confirmed recipient display alias.
- Source review confirms event content is added through `textContent`, rather than interpreted as HTML.
- Browser operation remained with the coordinating agent to avoid concurrent interaction. Final rendered desktop/mobile, keyboard, and live-deployment checks belong to that release verification; this review does not claim those passed.

## Remaining limits

This is an authenticated read-only operator dashboard, without settings editing or approval actions. Token counters are stored usage, not a billing reconciliation. Display aliases are currently the two user-confirmed workspace identities; additional names require verified configuration or lookup. Historical unknown-provenance numeric scores remain sortable and filterable, while fallback decisions are excluded from numeric minimum-score matching by the analytics layer.

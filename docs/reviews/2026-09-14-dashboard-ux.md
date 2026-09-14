# Dashboard UX/UI review — September 14, 2026

Reviewed the live authenticated admin interface at https://sgn.lol/dashboard, including Signal inbox, Deliveries, Sources, and Users & access. Desktop viewport: 1280 × 720. Mobile viewport: 390 × 844. Reviewed rendered screenshots, accessibility trees, element positions, and relevant UI code. No sources, users, messages, or application code were changed. The temporary viewport override was reset.

## Assessment

The dashboard has a consistent green palette, clear typography, readable cards, and useful score explanations. The main weakness is operational clarity: global metrics dominate task-specific screens, configuration is easily mistaken for verified connectivity, and delivery history lacks context that distinguishes historical failures from current problems.

## Prioritized findings

### P1 — Mobile navigation clips an admin destination

At 390 px, document width is 402 px. The five navigation items remain in one horizontal row, and “Users & access” is visibly clipped at the right edge. Its text wraps across multiple lines while neighboring items do not.

**Change:** Use a compact navigation drawer with the active screen name, or a deliberately scrollable navigation strip with an overflow affordance. Keep account identity and role accessible on mobile; the current responsive rules hide that label.

**Acceptance:** No page-level horizontal overflow at 320, 375, 390, and 430 px; every destination and account control is reachable and clearly named.

### P1 — Task content is buried below unrelated metrics

The same four overview cards, scoring distribution, and token totals appear above Deliveries, Sources, and Users & access. On desktop, the delivery report starts around y=976 because the test-alert form also precedes it. On mobile, inbox activity begins around y=921; the source form begins around y=1508 and Save is around y=2500.

**Change:** Keep global analytics on Overview or the inbox. Give Deliveries a report-first layout with a secondary “Send test” action. Give Sources a list-first layout with “Add source” opening a focused drawer or dedicated page. Keep Save/Cancel visible within the edit workflow.

**Acceptance:** Each screen exposes its primary content or action in the initial viewport, without scrolling through unrelated analytics.

### P1 — Source configuration does not communicate integration readiness

Source cards show “Enabled,” but saving a channel/repository does not prove that the Slack app can read it or that GitHub webhooks are installed. The explanation is a paragraph below the long form. A user can reasonably interpret “Enabled” as actively receiving events.

**Change:** Separate “Monitoring enabled” from observed connection state. Show states such as “Awaiting first event,” “Last event received …,” and a verified error where available. Put provider setup instructions next to the source being configured. Only claim connected/healthy when supported by provider or ingress evidence.

**Acceptance:** An admin can tell whether a source is configured, receiving events, or needs setup without reading footnotes.

### P1 — Delivery failures need age, scope, and next-step context

The report correctly includes the imported successful test, but the two September 13 failures still present only “Slack rejected delivery or retry limit reached.” The test summary displays a raw Slack timestamp and conversation ID. There are no filters separating tests from scored alerts, or current failures from historical records.

**Change:** Add status, date-range, and delivery-type filters. Show “Historical failure” when supported by age/context, preserve the original failure, and provide a detail view with safe error information and reconciliation guidance. Render timestamps as human-readable dates, with raw provider IDs behind details. Distinguish “Test alert,” “Imported confirmation,” and “Scored alert.”

**Acceptance:** A reader can distinguish a past failure from current delivery health and see the latest successful delivery without interpreting provider IDs. Do not mark historical failures resolved merely because a later test succeeds.

### P2 — Source and test forms require too much memorized configuration

Sources show IDs such as C0C18A105S7 and U02C1MHKQF9 despite the app already knowing the display labels #ai-tinkerers and @rob elsewhere. Source creation mixes destination, mentions, score thresholds, and phrase filters in one form. The test-alert form requires manually typing the organization and an already configured destination.

**Change:** Use known display names with IDs as secondary text, and permit admin-supplied labels where directory scopes are unavailable. Use configured organization/destination selectors for test alerts. Group source setup into “What to monitor,” “When to escalate,” and “Who to notify”; collapse optional keyword filters. Distinguish user DMs from channel posts and group mentions. Use source-specific field labels rather than “Channel ID or owner/repository.”

**Acceptance:** An admin can test an existing destination without copying IDs, and can understand destination versus mention behavior before saving. Do not silently request broader Slack permissions solely to improve labels.

### P2 — Navigation and edit state are fragile

All navigation remains at /dashboard. Screen selection is stored only in JavaScript state, so there are no direct links to Sources or Deliveries and reloading returns to the inbox. Switching screens does not communicate pending unsaved edits. “Routing” and “Sources” also overlap conceptually: one describes configuration while the other edits it.

**Change:** Add URL-backed view state and browser back/forward support. Preserve drafts or warn before discarding edits. Combine Routing and Sources into one permission-aware area, or rename them to make the distinction clear.

**Acceptance:** Refresh and shared links preserve the selected screen; leaving a changed form cannot silently lose work.

### P2 — Metrics and small text need clearer meaning

“Model relevance 38” looks like a health score but is an average of model-scored events. “Fallback decisions 2 — Review scoring availability” can imply a current incident even when the failures are historical. Distribution notes and metadata use 10–12 px type, which is hard to scan on mobile. There is no time-range control to establish the scope of these totals.

**Change:** Rename to “Average relevance,” show the time window, and distinguish historical fallback count from current scorer status. Move token usage into secondary operational details. Increase metadata/help text where it carries decision-critical information.

## Suggested implementation order

1. Fix mobile navigation and remove repeated analytics from task screens.
2. Make delivery history and integration readiness unambiguous.
3. Simplify source/test forms and add persistent navigation state.
4. Refine metric wording, text sizes, and spacing.

## Limits

This was a rendered UI and workflow review, not a full accessibility certification. Keyboard-only flows, screen-reader announcements, exhaustive contrast measurements, and all role-specific views still need a separate accessibility pass. No live sends or configuration writes were performed during this review.

## Implementation follow-through

Implemented task-first layouts, mobile menu, collapsed all-time analytics, visible account identity and delivery times on mobile, delivery filters/details, observed source activity, source labels and grouped modal editing, configured test selectors, URL-backed screens, draft preservation with unload warnings, and larger supporting text/focus outlines. Kept Routing as an explicitly named read-only reference.

Verification: 241 Python tests passed. Browser checks covered 320/375/390/430 px mobile and 720/760 px small-tablet widths with no document overflow, full navigation, visible timestamps, and a usable modal footer. Source saves/drafts and delivery filtering were exercised against isolated local fixtures. Production configuration and Slack messages were not changed during browser tests.

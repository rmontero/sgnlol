"use strict";
const $ = (id) => document.getElementById(id);
const state = {
  view: "inbox",
  offset: 0,
  deliveryOffset: 0,
  limit: 25,
  generation: 0,
  me: null,
  editingUser: false,
  dirty: new Set(),
};
const titles = {
  sources: ["Sources", "Choose what to monitor and when to raise an alert."],
  users: ["Users & access", "Manage who can see each organization."],
  inbox: [
    "Signal inbox",
    "Every event, ranked. Know what deserves your attention.",
  ],
  deliveries: [
    "Deliveries",
    "Follow each notification from decision to destination.",
  ],
  routing: [
    "Routing reference",
    "The sources you listen to. The people you trust to act.",
  ],
};
function node(tag, className, text) {
  const n = document.createElement(tag);
  if (className) n.className = className;
  if (text !== undefined) n.textContent = String(text);
  return n;
}
function number(n) {
  return Number.isFinite(Number(n))
    ? new Intl.NumberFormat().format(Number(n))
    : "—";
}
function date(value) {
  if (!value) return "—";
  const d = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(d.getTime())
    ? "—"
    : d.toLocaleString([], {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      });
}
function scoreValue(item) {
  const v =
    typeof item.score === "object" && item.score !== null
      ? item.score.score
      : item.score;
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}
function scoreBadge(item) {
  const v = scoreValue(item);
  const fallback = item.score?.fallback;
  const legacy = v !== null && !fallback && !item.score?.model;
  const label = fallback
    ? "Fallback policy decision; no model relevance score"
    : v === null
      ? "Not scored yet"
      : `${legacy ? "Recorded score, model provenance unavailable" : "Model relevance"}: ${Math.round(v * 100)} out of 100`;
  const result = node(
    "span",
    "score " +
      (fallback
        ? "fallback"
        : v === null
          ? "unscored"
          : v >= 0.9
            ? "critical"
            : v >= 0.7
              ? "high"
              : ""),
    fallback
      ? "F"
      : v === null
        ? "—"
        : Math.round(v * 100) + (legacy ? "?" : ""),
  );
  result.setAttribute("aria-label", label);
  result.title = label;
  return result;
}
function identity(value) {
  const names = { C0C18A105S7: "#ai-tinkerers", U02C1MHKQF9: "@rob" };
  return names[value] ? `${names[value]} (${value})` : String(value || "—");
}
function badge(value) {
  const v = String(value || "unknown");
  return node(
    "span",
    "badge" + (/fail|dead|unknown|error/i.test(v) ? " problem" : ""),
    v.replaceAll("_", " "),
  );
}
function empty(container, title, description) {
  container.replaceChildren();
  const box = node("div", "empty");
  box.append(node("strong", "", title), node("p", "", description));
  container.append(box);
}
function safeLink(raw) {
  try {
    const url = new URL(raw);
    return url.protocol === "https:" && !url.username && !url.password
      ? url.href
      : null;
  } catch {
    return null;
  }
}
async function api(path) {
  const response = await fetch("/api/dashboard/" + path, {
    headers: { Accept: "application/json" },
    credentials: "same-origin",
    cache: "no-store",
  });
  if (response.status === 401) window.location.replace("/dashboard/login");
  if (!response.ok)
    throw new Error(
      response.status === 401
        ? "Sign-in is required. Reload the dashboard to authenticate."
        : "The dashboard could not load data (HTTP " +
            response.status +
            "). Try refreshing.",
    );
  return response.json();
}
function error(err) {
  $("notice").textContent =
    err instanceof Error ? err.message : "Could not load dashboard data.";
  $("notice").hidden = false;
}
function setPage(prefix, offset, total, count) {
  const isEvents = prefix === "";
  const info = $(isEvents ? "page-info" : "delivery-page-info");
  info.textContent =
    !count && total
      ? `No results on this page · ${number(total)} total`
      : total
        ? `${number(offset + 1)}–${number(offset + count)} of ${number(total)}`
        : "0 results";
  $(prefix + "previous").disabled = offset <= 0;
  $(prefix + "next").disabled = offset + count >= total;
}
function detail(item) {
  const content = $("detail-content");
  content.replaceChildren();
  content.append(
    scoreBadge(item),
    node("h2", "", item.score?.summary || item.kind || "Event details"),
  );
  content.querySelector("h2").id = "detail-title";
  const meta = node("div", "detail-meta");
  meta.append(badge(item.source), badge(item.status));
  if (item.score?.fallback) meta.append(badge("fallback decision"));
  else if (scoreValue(item) !== null)
    meta.append(
      badge(item.score?.model ? "model scored" : "provenance unavailable"),
    );
  content.append(meta);
  content.append(
    node("h3", "", "Source context"),
    node(
      "p",
      "",
      [
        item.repo,
        item.actor ? identity(item.actor) : "",
        date(item.received_at),
      ]
        .filter(Boolean)
        .join(" · "),
    ),
  );
  content.append(
    node("h3", "", "Original event"),
    node("p", "", item.text || "No message body was stored."),
  );
  content.append(
    node("h3", "", "Why this score"),
    node(
      "p",
      "",
      item.score?.rationale ||
        "This event has no stored scoring rationale yet.",
    ),
  );
  if (item.score?.fallback)
    content.append(
      node(
        "p",
        "",
        "This decision used the configured scoring fallback. It is not a successful model relevance assessment.",
      ),
    );
  if (item.score?.model || item.score?.rubric_version)
    content.append(
      node("h3", "", "Scoring provenance"),
      node(
        "p",
        "",
        [item.score.model, item.score.rubric_version]
          .filter(Boolean)
          .join(" · "),
      ),
    );
  if (item.score)
    content.append(
      node("h3", "", "Recorded token usage"),
      node(
        "p",
        "",
        number(item.score.input_tokens) +
          " input · " +
          number(item.score.output_tokens) +
          " output. Counts reflect stored scoring usage; failed attempts may not be included.",
      ),
    );
  if (scoreValue(item) !== null && !item.score?.fallback && !item.score?.model)
    content.append(
      node(
        "p",
        "",
        "This stored score has no model provenance. It is excluded from model relevance averages and distribution.",
      ),
    );
  content.append(
    node("h3", "", "Scoring guide"),
    node(
      "p",
      "",
      "0–39: low relevance · 40–69: medium · 70–89: high · 90–100: critical. Routing thresholds determine whether a notification is delivered.",
    ),
  );
  if (item.error)
    content.append(
      node("h3", "", "Processing error"),
      node("p", "", item.error),
    );
  content.append(node("h3", "", "Delivery history"));
  if (!item.deliveries?.length)
    content.append(node("p", "", state.me.permissions.includes("deliveries:read") ? "No delivery recorded for this event." : "Delivery details require an analyst or admin role."));
  else
    for (const delivery of item.deliveries) {
      const row = node("div", "detail-delivery");
      row.append(
        badge(delivery.status),
        node("p", "", "Recipient: " + identity(delivery.recipient)),
      );
      if (delivery.error) row.append(node("p", "", delivery.error));
      content.append(row);
    }
  const url = safeLink(item.url);
  if (url) {
    const a = node("a", "source-link", "Open original event ↗");
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    content.append(a);
  }
  if (!$("detail").open) $("detail").showModal();
}
function renderEvents(data) {
  const items = data.items || [];
  $("event-count").textContent = number(data.total || 0);
  if (!items.length)
    empty(
      $("events"),
      "No events to show",
      "Try changing your filters. Events appear after an allowed source sends a supported webhook; a 200 response alone does not mean an event was queued or scored.",
    );
  else {
    $("events").replaceChildren();
    for (const item of items) {
      const row = node("article", "event-row");
      row.append(scoreBadge(item));
      const main = node("div", "event-main");
      const meta = node("div", "event-meta");
      meta.append(
        node(
          "span",
          "source",
          item.source === "github"
            ? "GitHub"
            : item.source === "slack"
              ? "Slack"
              : item.source || "Source",
        ),
        node("span", "", item.repo || item.kind || ""),
        node("span", "", item.actor ? identity(item.actor) : ""),
      );
      const title = node(
        "button",
        "event-title",
        item.score?.summary || item.text || item.kind || "View event",
      );
      title.type = "button";
      title.addEventListener("click", () => detail(item));
      main.append(
        meta,
        title,
        node(
          "p",
          "event-summary",
          item.score?.fallback
            ? "Fallback decision · " +
                (item.score?.rationale || "Review scoring availability")
            : (scoreValue(item) !== null && !item.score?.model
                ? "Provenance unavailable · "
                : "") +
                (item.score?.rationale || "Waiting for a relevance assessment"),
        ),
      );
      const status = node("div", "event-status");
      status.append(badge(item.status));
      row.append(
        main,
        status,
        node("time", "event-time", date(item.received_at)),
      );
      $("events").append(row);
    }
  }
  setPage("", state.offset, data.total || 0, items.length);
}
async function events() {
  const params = new URLSearchParams({
    limit: state.limit,
    offset: state.offset,
    sort: $("sort").value,
  });
  for (const [key, id] of [
    ["q", "search"],
    ["source", "source"],
    ["status", "status"],
    ["min_score", "min-score"],
  ])
    if ($(id).value) params.set(key, $(id).value);
  return api("events?" + params);
}
function renderDeliveries(data) {
  const items = data.items || [];
  if (!items.length)
    empty(
      $("deliveries"),
      "No deliveries recorded",
      "Scored-event alerts and tracked test alerts appear here.",
    );
  else {
    $("deliveries").replaceChildren();
    for (const item of items) {
      const row = node("article", "event-row");
      row.append(node("span", "score", "↗"));
      const body = node("div", "event-main");
      const imported = item.id?.startsWith("test-receipt:");
      const test = item.id?.startsWith("test:");
      const type = imported ? "Imported confirmation" : test ? "Test alert" : "Scored alert";
      const summary = item.status === "sent" ? "Slack confirmed acceptance." : item.status === "unknown" ? "Outcome uncertain. Check Slack before another attempt." : item.status === "failed" ? "This attempt failed. It is not a current connection check." : "Waiting for a confirmed outcome.";
      body.append(node("div", "event-meta", (item.org_id || "Organization") + " · " + type), node("strong", "event-title", identity(item.recipient || item.destination)), node("p", "event-summary", summary));
      const details = node("details", "delivery-details");
      details.append(node("summary", "", "Details and next step"));
      details.append(node("p", "", "Attempt time: " + date(item.due_at) + " · " + number(item.attempts) + " attempt(s)"));
      if (item.error) details.append(node("p", "", item.error));
      if (item.slack_ts) details.append(node("p", "provider-id", "Slack receipt: " + item.slack_ts));
      if (imported) details.append(node("p", "", "Recorded from an earlier successful Slack response; importing did not send another message."));
      else if (!test) details.append(node("p", "", number(item.event_count) + " event(s) · " + item.subject_key));
      if (item.status === "failed") details.append(node("p", "", "Check the destination and app permissions. A new tracked test can verify delivery; this failed attempt remains in history."));
      details.append(node("p", "provider-id", "Record: " + item.id));
      body.append(details);
      const status = node("div", "event-status");
      status.append(badge(item.status === "failed" ? "failed attempt" : item.status));
      row.append(
        body,
        status,
        node(
          "time",
          "event-time",
          date(item.created_at || item.updated_at || item.due_at),
        ),
      );
      $("deliveries").append(row);
    }
  }
  setPage("delivery-", state.deliveryOffset, data.total || 0, items.length);
}
function renderRouting(data) {
  const orgs = data.orgs || [];
  if (!orgs.length) {
    empty(
      $("routing"),
      "Routing setup required",
      "No organizations are configured. Allowed sources and recipients must be configured before events can be queued.",
    );
    return;
  }
  $("routing").replaceChildren();
  for (const org of orgs) {
    const card = node("article", "route-card");
    card.append(node("h3", "", org.name || org.id || "Organization"));
    const dl = node("dl");
    const rows = [
      [
        "Slack channels",
        (org.slack_channels || []).map(identity).join(", ") ||
          "None configured",
      ],
      [
        "Flag recipient",
        org.recipient ? identity(org.recipient) : "Per repository",
      ],
      ["Default threshold", Math.round(org.threshold * 100) + " / 100"],
      ["Slack workspace", org.slack_team_id],
      ["GitHub owner", org.github_org],
      ["Routing mode", org.type],
    ];
    for (const [label, value] of rows)
      dl.append(node("dt", "", label), node("dd", "", value));
    dl.append(node("dt", "", "Repositories"));
    const repos = node("dd");
    for (const [name, config] of Object.entries(org.repos || {})) {
      repos.append(
        node(
          "p",
          "",
          name +
            " · threshold " +
            Math.round((config.threshold ?? org.threshold) * 100) +
            " / 100 · " +
            (config.recipients?.length
              ? config.recipients.map(identity).join(", ")
              : identity(org.recipient)),
        ),
      );
    }
    if (!repos.childElementCount) repos.textContent = "None configured";
    dl.append(repos);
    card.append(dl);
    $("routing").append(card);
  }
}
function disablePagination(prefix) {
  $(prefix + "previous").disabled = true;
  $(prefix + "next").disabled = true;
  $(prefix ? "delivery-page-info" : "page-info").textContent = "";
}
function renderOverview(overview) {
  $("metric-total").textContent = number(overview.total_events);
  $("metric-score").textContent =
    overview.avg_score == null ? "—" : Math.round(overview.avg_score * 100);
  $("metric-model-detail").textContent =
    number(overview.model_scored_count) + " verified model scores · 0–100";
  $("metric-filtered").textContent =
    overview.filter_rate == null
      ? "—"
      : Math.round(overview.filter_rate * 100) + "%";
  $("metric-filtered-detail").textContent =
    number(overview.filtered) + " processed events filtered by policy";
  $("metric-fallback").textContent = number(overview.fallback_count);
  $("token-usage").textContent =
    number(overview.input_tokens) +
    " input / " +
    number(overview.output_tokens) +
    " output";
  const buckets = overview.score_buckets || {};
  $("score-distribution").replaceChildren();
  for (const [key, label] of [
    ["low", "Low 0–39"],
    ["medium", "Medium 40–69"],
    ["high", "High 70–89"],
    ["critical", "Critical 90–100"],
  ]) {
    $("score-distribution").append(
      node("span", "", label + ": " + number(buckets[key] || 0)),
    );
  }
}
async function refresh() {
  const generation = ++state.generation;
  $("refresh").disabled = true;
  $("notice").hidden = true;
  const target = state.view;
  disablePagination(target === "deliveries" ? "delivery-" : "");
  const container = $(target === "inbox" ? "events" : target);
  container.setAttribute("aria-busy", "true");
  empty(container, "Loading…", "Fetching the latest stored activity.");
  try {
    const [overview, content] = await Promise.all([
      target === "inbox" ? api("overview") : Promise.resolve(null),
      target === "inbox"
        ? events()
        : target === "deliveries"
          ? api(deliveryQuery())
          : api(target),
    ]);
    if (generation !== state.generation) return;
    if (overview) renderOverview(overview);
    if (target === "deliveries" && state.me.permissions.includes("sources:manage")) await setupTestDestinations();
    if (generation !== state.generation) return;
    if (target === "inbox") renderEvents(content);
    else if (target === "deliveries") renderDeliveries(content);
    else if (target === "sources") renderSources(content);
    else if (target === "routing") renderRouting(content);
    else renderUsers(content);
    $("updated").textContent =
      "Updated " +
      new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  } catch (err) {
    if (generation === state.generation) {
      error(err);
      empty(
        container,
        "Unable to load activity",
        "Refresh to try again. Previously stored events are not changed by this dashboard.",
      );
    }
  } finally {
    if (generation === state.generation) {
      $("refresh").disabled = false;
      container.setAttribute("aria-busy", "false");
    }
  }
}
function navigate(view, updateUrl = true) {
  const button = document.querySelector('[data-view="' + view + '"]');
  if (!button || button.hidden) view = "inbox";
  state.view = view;
  for (const nav of document.querySelectorAll("[data-view]")) {
    const active = nav.dataset.view === view;
    nav.classList.toggle("active", active);
    if (active) nav.setAttribute("aria-current", "page"); else nav.removeAttribute("aria-current");
  }
  for (const name of ["inbox", "deliveries", "routing", "users", "sources"]) $(name + "-view").hidden = name !== view;
  $("overview-panel").hidden = view !== "inbox";
  $("page-title").textContent = titles[view][0]; $("page-description").textContent = titles[view][1];
  document.title = titles[view][0] + " · sgnlol";
  if (updateUrl && location.hash !== "#" + view) history.pushState(null, "", "#" + view);
  document.querySelector(".sidebar").classList.remove("nav-open"); $("menu-toggle").setAttribute("aria-expanded", "false");
  refresh();
}
for (const button of document.querySelectorAll("[data-view]")) button.addEventListener("click", () => navigate(button.dataset.view));
window.addEventListener("hashchange", () => {const view=location.hash.slice(1); if (Object.hasOwn(titles, view) && state.me) navigate(view, false);});
$("menu-toggle").addEventListener("click", () => {const open=document.querySelector(".sidebar").classList.toggle("nav-open");$("menu-toggle").setAttribute("aria-expanded",String(open));});
$("filters").addEventListener("submit", (e) => {
  e.preventDefault();
  state.offset = 0;
  refresh();
});
$("refresh").addEventListener("click", refresh);
$("previous").addEventListener("click", () => {
  state.offset = Math.max(0, state.offset - state.limit);
  refresh();
});
$("next").addEventListener("click", () => {
  state.offset += state.limit;
  refresh();
});
$("delivery-previous").addEventListener("click", () => {
  state.deliveryOffset = Math.max(0, state.deliveryOffset - state.limit);
  refresh();
});
$("delivery-next").addEventListener("click", () => {
  state.deliveryOffset += state.limit;
  refresh();
});
$("close-detail").addEventListener("click", () => $("detail").close());
$("detail").addEventListener("click", (e) => {
  const bounds = $("detail").getBoundingClientRect();
  if (
    e.target === $("detail") &&
    (e.clientX < bounds.left ||
      e.clientX > bounds.right ||
      e.clientY < bounds.top ||
      e.clientY > bounds.bottom)
  )
    $("detail").close();
});
async function initialize() {
  try {
    state.me = await api("me");
    $("account-label").textContent = state.me.username + " · " + state.me.role;
    for (const control of document.querySelectorAll("[data-permission]")) control.hidden = !state.me.permissions.includes(control.dataset.permission);
    const requested = location.hash.slice(1);
    navigate(Object.hasOwn(titles, requested) ? requested : state.view);

  } catch (err) { error(err); }
}
function resetUser() {
  state.dirty.delete("user-form");
  state.editingUser = false;
  $("user-form").reset(); $("user-name").readOnly = false;
  $("user-form-title").textContent = "Create user";
  $("user-password").required = true; $("user-feedback").textContent = "";
}
function renderUsers(data) {
  $("users").replaceChildren();
  for (const user of data.users) {
    const card = node("article", "route-card");
    const edit = node("button", "button secondary", "Edit " + user.username);
    edit.type = "button";
    edit.addEventListener("click", () => {
      if (state.dirty.has("user-form") && !window.confirm("Discard the unsaved account edits?")) return;
      state.dirty.delete("user-form");
      state.editingUser = true; $("user-name").value = user.username; $("user-name").readOnly = true;
      $("user-password").value = ""; $("user-password").required = false;
      $("user-role").value = user.role; $("user-orgs").value = user.orgs.join(", ");
      $("user-active").checked = user.active; $("user-form-title").textContent = "Edit " + user.username;
      $("user-feedback").textContent = "Leave password blank to keep it unchanged.";
      $("user-role").focus();
    });
    card.append(node("h3", "", user.username), node("p", "", user.role + " · " + (user.active ? "Enabled" : "Disabled")), node("p", "", user.role === "admin" ? "All organizations" : user.orgs.join(", ")), edit);
    $("users").append(card);
  }
  api("cache").then(info => { $("cache-status").textContent = "Redis cache: " + info.state + " · " + info.ttl_seconds + "s TTL · " + info.hits + " hits / " + info.misses + " misses"; }).catch(() => { $("cache-status").textContent = "Cache status unavailable"; });
}
$("new-user").addEventListener("click", () => {if (!state.dirty.has("user-form") || window.confirm("Discard the unsaved account edits?")) resetUser();});
$("user-form").addEventListener("submit", async (event) => {
  event.preventDefault(); $("save-user").disabled = true;
  const payload = {username: $("user-name").value.trim(), role: $("user-role").value, orgs: $("user-orgs").value.split(",").map(s => s.trim()).filter(Boolean), active: $("user-active").checked};
  if ($("user-password").value) payload.password = $("user-password").value;
  try {
    const response = await fetch("/api/dashboard/users", {method: state.editingUser ? "PUT" : "POST", headers: {"Content-Type": "application/json", "X-Sgnlol-Request": "dashboard"}, credentials: "same-origin", body: JSON.stringify(payload)});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Could not save user");
    resetUser(); $("user-feedback").textContent = "User saved. Permission changes apply to the next request.";
    await initialize();
  } catch (err) { $("user-password").value = ""; $("user-feedback").textContent = err.message; }
  finally { $("save-user").disabled = false; }
});
resetUser();
initialize();

document.getElementById("sign-out").addEventListener("click", async () => {
  const response = await fetch("/api/dashboard/logout", {method:"POST",headers:{"Content-Type":"application/json","X-Sgnlol-Request":"dashboard"},body:"{}"});
  if (response.ok) window.location.replace("/dashboard/login");
});

const sourceFields = ["label", "identity", "threshold", "recipients", "mentions", "include", "exclude"];
function sourceTypeChanged() {
  const github = $("source-type").value === "github";
  $("source-github-events").hidden = !github;
  $("source-identity-label").textContent = github ? "Repository (owner/name)" : "Slack channel ID";
  $("source-identity").placeholder = github ? "rmontero/repository" : "C0C18A105S7";
  $("source-help").textContent = github ? "Install the GitHub webhook on this repository first. Choose PR and comment events below. Saving settings alone does not connect GitHub." : "Invite the Slack app to this public channel. Use a channel destination for group mentions, or a user destination for a DM. Saving settings alone does not grant access.";
}
function resetSource() {
  state.dirty.delete("source-form");
  $("source-advanced").open = false;
  $("source-form").reset(); $("source-identity").readOnly = false;
  $("source-org").disabled = false; $("source-type").disabled = false;
  $("source-form-title").textContent = "Add source"; $("source-feedback").textContent = "";
  sourceTypeChanged();
}
function editSource(org, type, identity, rule) {
  if (state.dirty.has("source-form")) { $("source-editor").showModal(); $("source-feedback").textContent = "An unsaved draft is open. Save or discard it before editing another source."; return; }
  resetSource(); $("source-org").value = org.id; $("source-type").value = type;
  $("source-org").disabled = true; $("source-type").disabled = true; $("source-identity").readOnly = true;
  $("source-form-title").textContent = "Edit " + identity;
  const values = [rule.label || "", identity, rule.threshold == null ? "" : rule.threshold * 100, (rule.recipients || []).join(", "), (rule.mentions || []).join(", "), (rule.include_keywords || []).join(", "), (rule.exclude_keywords || []).join(", ")];
  sourceFields.forEach((field, i) => { $("source-" + field).value = values[i]; });
  $("source-enabled").checked = rule.enabled !== false;
  for (const box of document.querySelectorAll('[name="github-event"]')) box.checked = !rule.event_types || rule.event_types.includes(box.value);
  $("source-advanced").open = Boolean(rule.include_keywords?.length || rule.exclude_keywords?.length);
  sourceTypeChanged(); $("source-editor").showModal(); $("source-threshold").focus();
}
function renderSources(data) {
  // Keep the old edit revision with an unsaved draft so concurrent changes still conflict.
  if (!state.dirty.has("source-form")) state.sourceConfig = data;
  const selected = $("source-org").value;
  $("source-org").replaceChildren(); $("sources").replaceChildren();
  for (const org of data.orgs) {
    const option = node("option", "", org.id + " · GitHub owner: " + org.github_org); option.value = org.id; $("source-org").append(option);
    const entries = [...org.slack_channels.map(id => ["slack", id, org.slack_rules?.[id] || {}]), ...Object.entries(org.repos).map(([id, rule]) => ["github", id, rule])];
    for (const [type, sourceId, rule] of entries) {
      const card = node("article", "route-card");
      const edit = node("button", "button secondary", "Edit " + (rule.label || identity(sourceId))); edit.type = "button"; edit.addEventListener("click", () => editSource(org, type, sourceId, rule));
      const activity = (data.activity || []).find(a => a.org_id === org.id && a.source === type && a.identity?.toLowerCase() === sourceId.toLowerCase());
      const receipt = activity ? "Last accepted event: " + date(activity.last_received) + " · " + number(activity.event_count) + " stored" : "Awaiting first accepted event · check provider setup";
      const targets = rule.recipients?.length && (type === "slack" || rule.override_recipients || org.type === "enterprise") ? rule.recipients : [org.recipient];
      card.append(node("h3", "", rule.label || identity(sourceId).replace(/ \([A-Z0-9]+\)$/, "")), node("p", "source-id", sourceId), node("p", "", org.id + " · " + type + " · Monitoring " + (rule.enabled === false ? "disabled" : "enabled")), node("p", "source-activity", receipt), node("p", "", "Escalate at " + Math.round((rule.threshold ?? org.threshold) * 100) + "/100 → " + targets.filter(Boolean).map(identity).join(", ")), node("p", "", "Mentions: " + (rule.mentions?.map(identity).join(", ") || "None")), edit);
      $("sources").append(card);
    }
  }
  if (selected && data.orgs.some(o => o.id === selected)) $("source-org").value = selected;
  $("save-source").disabled = !data.orgs.length; $("add-source").disabled = !data.orgs.length;
  if (!data.orgs.length) $("source-list-feedback").textContent = "Configure an organization before adding sources.";
  sourceTypeChanged();
}
$("add-source").addEventListener("click", () => {if (!state.dirty.has("source-form")) resetSource(); $("source-editor").showModal();});
$("new-source").addEventListener("click", () => {resetSource();$("source-editor").close();$("source-list-feedback").textContent="Draft discarded.";});
$("close-source").addEventListener("click", () => $("source-editor").close());
$("source-editor").addEventListener("close", () => {if (state.dirty.has("source-form")) $("source-list-feedback").textContent="Unsaved draft kept. Select Add source to resume it.";});
$("source-type").addEventListener("change", sourceTypeChanged);
$("source-form").addEventListener("submit", async (event) => {
  event.preventDefault(); $("save-source").disabled = true;
  const list = id => $(id).value.split(",").map(v => v.trim()).filter(Boolean);
  const rule = {label:$("source-label").value.trim(), enabled:$("source-enabled").checked, threshold:$("source-threshold").value === "" ? null : Number($("source-threshold").value)/100, recipients:list("source-recipients"), mentions:list("source-mentions"), include_keywords:list("source-include"), exclude_keywords:list("source-exclude")};
  if ($("source-type").value === "github") rule.event_types = [...document.querySelectorAll('[name="github-event"]:checked')].map(box => box.value);
  try {
    const response = await fetch("/api/dashboard/sources", {method:"PUT",headers:{"Content-Type":"application/json","X-Sgnlol-Request":"dashboard"},body:JSON.stringify({revision:state.sourceConfig.revision,org_id:$("source-org").value,source:$("source-type").value,identity:$("source-identity").value.trim(),rule})});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Could not save source");
    const activity = state.sourceConfig?.activity || [];
    resetSource(); renderSources({...data,activity}); $("source-editor").close(); $("source-list-feedback").textContent = "Saved. Monitoring settings apply immediately. Check the last accepted event to confirm activity.";
  } catch (err) { $("source-feedback").textContent = err.message; }
  finally { $("save-source").disabled = false; }
});
sourceTypeChanged();

let pendingTestId = null;
$("test-alert-form").addEventListener("submit", async event => {
  event.preventDefault(); $("send-test-alert").disabled = true;
  pendingTestId ||= crypto.randomUUID();
  $("test-org").disabled = true; $("test-recipient").disabled = true;
  try {
    const response = await fetch("/api/dashboard/test-alert", {method:"POST",headers:{"Content-Type":"application/json","X-Sgnlol-Request":"dashboard"},body:JSON.stringify({request_id:pendingTestId,org_id:$("test-org").value.trim(),recipient:$("test-recipient").value.trim()})});
    if (!response.ok) throw new Error("Test request failed. Check the configured organization and destination.");
    const record = await response.json();
    state.testFinished = true;
    $("test-result").textContent = record.status === "sent" ? "Slack confirmed delivery. The test is recorded below." : "Recorded status: " + record.status + ". Check the delivery before sending another test.";
    await refresh();
  } catch (err) { $("test-result").textContent = err.message + " Retrying this form uses the same request ID to prevent duplicate delivery."; }
  finally { $("send-test-alert").disabled = Boolean(state.testFinished); }
});
$("new-test-alert").addEventListener("click", () => {pendingTestId=null;state.testFinished=false;$("send-test-alert").disabled=false;$("test-org").disabled=false;$("test-recipient").disabled=false;$("test-result").textContent="Ready for a new test alert.";});

function deliveryQuery() {
  const params = new URLSearchParams({limit: state.limit, offset: state.deliveryOffset});
  for (const key of ["status", "kind", "days"]) if ($("delivery-" + key).value) params.set(key, $("delivery-" + key).value);
  return "deliveries?" + params;
}
$("delivery-filters").addEventListener("submit", e => {e.preventDefault(); state.deliveryOffset=0; refresh();});
async function setupTestDestinations() {
  const routing = await api("routing");
  state.testOrgs = routing.orgs;
  const selected = $("test-org").value;
  $("test-org").replaceChildren();
  for (const org of routing.orgs) {const option=node("option","",org.id);option.value=org.id;$("test-org").append(option);}
  if (routing.orgs.some(o=>o.id===selected)) $("test-org").value=selected;
  fillTestRecipients();
}
function fillTestRecipients() {
  const selected=$("test-recipient").value;
  const org=(state.testOrgs || []).find(o=>o.id===$("test-org").value);
  const ids=org ? [...new Set([org.recipient,...Object.values(org.repos).flatMap(r=>r.recipients),...Object.values(org.slack_rules || {}).flatMap(r=>r.recipients)].filter(Boolean))] : [];
  $("test-recipient").replaceChildren();
  for (const id of ids) {const option=node("option","",identity(id));option.value=id;$("test-recipient").append(option);}
  if (ids.includes(selected)) $("test-recipient").value=selected;
  $("send-test-alert").disabled=!ids.length || Boolean(state.testFinished);
}
$("test-org").addEventListener("change",fillTestRecipients);
for (const id of ["source-form","user-form"]) {
  $(id).addEventListener("input",()=>state.dirty.add(id));
  $(id).addEventListener("change",()=>state.dirty.add(id));
}
window.addEventListener("beforeunload",event=>{if(state.dirty.size){event.preventDefault();event.returnValue="";}});

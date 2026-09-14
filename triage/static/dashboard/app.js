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
    "Routing",
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
      body.append(
        node("div", "event-meta", item.org_id || "Organization"),
        node(
          "strong",
          "event-title",
          identity(item.recipient || item.destination),
        ),
        node(
          "p",
          "event-summary",
          item.error ||
            item.summary ||
            (item.subject_key?.startsWith("test:") ? "Test alert · " + item.subject_key.slice(5) + (item.slack_ts ? " · Slack confirmed: " + item.slack_ts : "") : "") ||
            [
              item.subject_key,
              number(item.event_count) + " events",
              "Batch " + String(item.id || ""),
            ]
              .filter(Boolean)
              .join(" · "),
        ),
      );
      const status = node("div", "event-status");
      status.append(badge(item.status));
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
      api("overview"),
      target === "inbox"
        ? events()
        : target === "deliveries"
          ? api(
              "deliveries?limit=" +
                state.limit +
                "&offset=" +
                state.deliveryOffset,
            )
          : api(target),
    ]);
    if (generation !== state.generation) return;
    renderOverview(overview);
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
for (const button of document.querySelectorAll("[data-view]"))
  button.addEventListener("click", () => {
    state.view = button.dataset.view;
    for (const nav of document.querySelectorAll("[data-view]")) {
      const active = nav === button;
      nav.classList.toggle("active", active);
      if (active) nav.setAttribute("aria-current", "page");
      else nav.removeAttribute("aria-current");
    }
    for (const view of ["inbox", "deliveries", "routing", "users", "sources"])
      $(view + "-view").hidden = view !== state.view;
    $("page-title").textContent = titles[state.view][0];
    $("page-description").textContent = titles[state.view][1];
    refresh();
  });
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
    const selected = document.querySelector('[data-view="' + state.view + '"]');
    if (selected?.hidden) document.querySelector('[data-view="inbox"]').click();
    else await refresh();
  } catch (err) { error(err); }
}
function resetUser() {
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
$("new-user").addEventListener("click", resetUser);
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

const sourceFields = ["identity", "threshold", "recipients", "mentions", "include", "exclude"];
function sourceTypeChanged() { $("source-github-events").hidden = $("source-type").value !== "github"; }
function resetSource() {
  $("source-form").reset(); $("source-identity").readOnly = false;
  $("source-org").disabled = false; $("source-type").disabled = false;
  $("source-form-title").textContent = "Add source"; $("source-feedback").textContent = "";
  sourceTypeChanged();
}
function editSource(org, type, identity, rule) {
  resetSource(); $("source-org").value = org.id; $("source-type").value = type;
  $("source-org").disabled = true; $("source-type").disabled = true; $("source-identity").readOnly = true;
  $("source-form-title").textContent = "Edit " + identity;
  const values = [identity, rule.threshold == null ? "" : rule.threshold * 100, (rule.recipients || []).join(", "), (rule.mentions || []).join(", "), (rule.include_keywords || []).join(", "), (rule.exclude_keywords || []).join(", ")];
  sourceFields.forEach((field, i) => { $("source-" + field).value = values[i]; });
  $("source-enabled").checked = rule.enabled !== false;
  for (const box of document.querySelectorAll('[name="github-event"]')) box.checked = !rule.event_types || rule.event_types.includes(box.value);
  sourceTypeChanged(); $("source-threshold").focus();
}
function renderSources(data) {
  state.sourceConfig = data;
  const selected = $("source-org").value;
  $("source-org").replaceChildren(); $("sources").replaceChildren();
  for (const org of data.orgs) {
    const option = node("option", "", org.id + " · GitHub owner: " + org.github_org); option.value = org.id; $("source-org").append(option);
    const entries = [...org.slack_channels.map(id => ["slack", id, org.slack_rules?.[id] || {}]), ...Object.entries(org.repos).map(([id, rule]) => ["github", id, rule])];
    for (const [type, identity, rule] of entries) {
      const card = node("article", "route-card");
      const edit = node("button", "button secondary", "Edit " + identity); edit.type = "button"; edit.addEventListener("click", () => editSource(org, type, identity, rule));
      card.append(node("h3", "", identity), node("p", "", org.id + " · " + type + " · " + (rule.enabled === false ? "Disabled" : "Enabled")), node("p", "", "Escalate at " + Math.round((rule.threshold ?? org.threshold) * 100) + "/100 → " + (rule.recipients?.join(", ") || org.recipient || "No destination")), node("p", "", "Mentions: " + (rule.mentions?.join(", ") || "None")), edit);
      $("sources").append(card);
    }
  }
  if (selected && data.orgs.some(o => o.id === selected)) $("source-org").value = selected;
  $("save-source").disabled = !data.orgs.length;
  if (!data.orgs.length) $("source-feedback").textContent = "Configure an organization before adding sources.";
  sourceTypeChanged();
}
$("new-source").addEventListener("click", resetSource);
$("source-type").addEventListener("change", sourceTypeChanged);
$("source-form").addEventListener("submit", async (event) => {
  event.preventDefault(); $("save-source").disabled = true;
  const list = id => $(id).value.split(",").map(v => v.trim()).filter(Boolean);
  const rule = {enabled:$("source-enabled").checked, threshold:$("source-threshold").value === "" ? null : Number($("source-threshold").value)/100, recipients:list("source-recipients"), mentions:list("source-mentions"), include_keywords:list("source-include"), exclude_keywords:list("source-exclude")};
  if ($("source-type").value === "github") rule.event_types = [...document.querySelectorAll('[name="github-event"]:checked')].map(box => box.value);
  try {
    const response = await fetch("/api/dashboard/sources", {method:"PUT",headers:{"Content-Type":"application/json","X-Sgnlol-Request":"dashboard"},body:JSON.stringify({revision:state.sourceConfig.revision,org_id:$("source-org").value,source:$("source-type").value,identity:$("source-identity").value.trim(),rule})});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Could not save source");
    resetSource(); renderSources(data); $("source-feedback").textContent = "Saved. Monitoring uses these settings immediately and after redeployment.";
  } catch (err) { $("source-feedback").textContent = err.message; }
  finally { $("save-source").disabled = false; }
});
sourceTypeChanged();

let pendingTestId = null;
$("test-alert-form").addEventListener("submit", async event => {
  event.preventDefault(); $("send-test-alert").disabled = true;
  pendingTestId ||= crypto.randomUUID();
  try {
    const response = await fetch("/api/dashboard/test-alert", {method:"POST",headers:{"Content-Type":"application/json","X-Sgnlol-Request":"dashboard"},body:JSON.stringify({request_id:pendingTestId,org_id:$("test-org").value.trim(),recipient:$("test-recipient").value.trim()})});
    if (!response.ok) throw new Error("Test request failed. Check the configured organization and destination.");
    const record = await response.json();
    $("test-result").textContent = record.status === "sent" ? "Slack confirmed delivery. The test is recorded below." : "Recorded status: " + record.status + ". Check the delivery before sending another test.";
    await refresh();
  } catch (err) { $("test-result").textContent = err.message + " Retrying this form uses the same request ID to prevent duplicate delivery."; }
  finally { $("send-test-alert").disabled = false; }
});
$("new-test-alert").addEventListener("click", () => {pendingTestId=null;$("test-result").textContent="Ready for a new test alert.";});

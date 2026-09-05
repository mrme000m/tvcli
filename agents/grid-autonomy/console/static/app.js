/* grid/autonomy console — vanilla JS, no build step.
   Polls the console backend (same origin), renders the fleet, ledger,
   run cards, reliability, config editor and logs. */

"use strict";

/* ── tiny helpers ─────────────────────────────────────────────────── */

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v; // trusted templates only
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined) continue;
    node.append(c.nodeType ? c : document.createTextNode(c));
  }
  return node;
}

function fmtPrice(p) {
  if (p === null || p === undefined || isNaN(p)) return "—";
  const n = Number(p);
  if (n >= 1000) return n.toLocaleString("en-US", { maximumFractionDigits: 2 });
  if (n >= 10) return n.toFixed(3);
  if (n >= 0.1) return n.toFixed(4);
  return n.toPrecision(3);
}
const fmtUsd = (v) => (v === null || v === undefined || isNaN(v))
  ? "—" : `$${Number(v).toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
const fmtNum = (v, d = 2) => (v === null || v === undefined || isNaN(v))
  ? "—" : Number(v).toFixed(d);
const fmtPct = (v) => (v === null || v === undefined || isNaN(v))
  ? "—" : `${(Number(v) * 100).toFixed(1)}%`;

function relTime(iso) {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (isNaN(t)) return "—";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 90) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400 * 2) return `${(s / 3600).toFixed(1)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}
const heldFor = (iso) => {
  if (!iso) return null;
  const h = (Date.now() - Date.parse(iso)) / 3600000;
  return isNaN(h) ? null : (h >= 48 ? `${(h / 24).toFixed(1)}d` : `${h.toFixed(1)}h`);
};

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(data.error || res.statusText), { data });
  return data;
}

function toast(msg, bad = false, ms = 4200) {
  const t = el("div", { class: `toast${bad ? " toast--bad" : ""}` }, msg);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), ms);
}

/* ── confirm modal ────────────────────────────────────────────────── */

function confirmDialog({ title, body, label = "Confirm", danger = false, checkbox = null }) {
  return new Promise((resolve) => {
    const root = $("#modal-root");
    const box = el("div", { class: "modal-backdrop" });
    const checkRef = { input: null };
    const modal = el("div", { class: "modal", role: "dialog", "aria-modal": "true" },
      el("h3", {}, title),
      el("div", { class: "modal-body" }, ...body),
      checkbox ? el("label", { class: "check-line" },
        (checkRef.input = el("input", { type: "checkbox" })),
        el("span", {}, checkbox)) : null,
      el("div", { class: "modal-actions" },
        el("button", { class: "btn", onclick: () => done(false) }, "Cancel"),
        el("button", { class: `btn ${danger ? "btn--danger" : "btn--primary"}`, onclick: () => done(true) }, label)));
    function done(ok) {
      root.innerHTML = "";
      document.removeEventListener("keydown", onKey);
      resolve({ ok, checked: checkRef.input ? checkRef.input.checked : false });
    }
    function onKey(e) { if (e.key === "Escape") done(false); }
    document.addEventListener("keydown", onKey);
    box.append(modal);
    box.addEventListener("mousedown", (e) => { if (e.target === box) done(false); });
    root.append(box);
    modal.querySelector(".modal-actions .btn:last-child").focus();
  });
}

/* ── tabs ─────────────────────────────────────────────────────────── */

const VIEWS = ["fleet", "decisions", "reports", "reliability", "config", "logs"];
let activeView = "fleet";

function selectView(name) {
  activeView = name;
  for (const v of VIEWS) {
    $(`#tab-${v}`).setAttribute("aria-selected", String(v === name));
    $(`#view-${v}`).hidden = v !== name;
  }
  location.hash = name;
  if (name === "fleet") loadOverview(); // immediate render, don't wait for the poll tick
  if (name === "decisions") loadDecisions();
  if (name === "reports") loadReports();
  if (name === "reliability") loadReliability();
  if (name === "config") loadConfig();
  if (name === "logs") loadLogs(true);
}

for (const v of VIEWS) $(`#tab-${v}`).addEventListener("click", () => selectView(v));

/* ── overview / statusbar / fleet ─────────────────────────────────── */

let lastOverview = null;

async function loadOverview() {
  let ov;
  try {
    ov = await api("/api/overview");
  } catch (e) {
    renderStatusbar(null);
    return;
  }
  lastOverview = ov;
  renderStatusbar(ov);
  if (activeView === "fleet") {
    renderReadiness(ov);
    renderFleet(ov);
    renderFeed(ov.journal_tail || []);
    renderScreen(ov.screen);
    renderSummary(ov);
  }
}

function renderStatusbar(ov) {
  const bar = $("#statusbar");
  if (!ov) {
    bar.innerHTML = `<span class="chip chip--bad"><span class="dot"></span>console backend unreachable</span>`;
    return;
  }
  const d = ov.daemon || {};
  const chips = [];
  if (d.running) {
    chips.push(`<span class="chip chip--ok"><span class="dot pulse"></span>daemon <b>${esc(d.mode || "?")}</b> · ${esc(d.supervisor)} · pid ${esc(d.pid)}</span>`);
  } else {
    chips.push(`<span class="chip chip--bad"><span class="dot"></span>daemon stopped</span>`);
  }
  const lastCycle = (ov.ctl && ov.ctl.status && ov.ctl.status.last_cycle) || null;
  chips.push(`<span class="chip"><span class="dot"></span>loop <b>${esc(relTime(lastCycle))}</b></span>`);
  const slots = (ov.slots || []).length || 1;
  chips.push(`<span class="chip"><span class="dot"></span>fleet <b>${(ov.bots || []).length}/${slots}</b> · ${esc(fmtUsd(ov.committed_usd))} committed</span>`);
  chips.push(d.kill_file
    ? `<span class="chip chip--bad"><span class="dot"></span><b>KILL armed</b></span>`
    : `<span class="chip"><span class="dot"></span>KILL clear</span>`);
  chips.push(ov.pocketbase && ov.pocketbase.up
    ? `<span class="chip"><span class="dot"></span>PB mirror up</span>`
    : `<span class="chip"><span class="dot" style="background:var(--ink-faint)"></span>PB mirror down</span>`);
  const liveAllow = (ov.ctl && ov.ctl.status && ov.ctl.status.live_allow) ?? ov.live_allow;
  if (liveAllow) {
    chips.push(`<span class="chip chip--warn"><span class="dot"></span><b>live_allow=true</b></span>`);
  }
  bar.innerHTML = chips.join("");
}

/* the signature: an ATR-channel strip with geometric grid rungs and a
   live price cursor. Log-scaled so geometric rungs space evenly. */
function ladderHTML(bot) {
  const ch = bot.channel;
  const price = bot.observed && bot.observed.price;
  if (!ch || !ch.low || !ch.high || ch.high <= ch.low) {
    return `<div class="ladder" style="border:none;background:transparent;display:flex;align-items:center;justify-content:center;">
      <span class="mono" style="font-size:11px;color:var(--ink-faint)">${bot.adopted ? "adopted — no channel recorded" : "no channel"}</span>
    </div>`;
  }
  const lo = Number(ch.low), hi = Number(ch.high);
  const ln = (x) => Math.log(x);
  const span = ln(hi) - ln(lo);
  const posOf = (p) => ((ln(Math.min(Math.max(p, lo), hi)) - ln(lo)) / span) * 100;
  const n = Number(ch.grids) || 0;
  let rungs = "";
  if (n > 0 && n <= 60) {
    for (let i = 1; i < n; i++) {
      const top = 100 - (i / n) * 100; // geometric rungs, log axis → even
      rungs += `<div class="rung" style="top:${top.toFixed(2)}%"></div>`;
    }
  }
  const midTop = ch.mid ? (100 - posOf(ch.mid)).toFixed(2) : null;
  const hasPrice = price !== null && price !== undefined && !isNaN(price);
  const outside = hasPrice && (price < lo || price > hi);
  const cursorTop = hasPrice ? (100 - posOf(price)).toFixed(2) : null;
  return `<div class="ladder${outside ? " ladder--outside" : ""}" title="channel ${fmtPrice(lo)} – ${fmtPrice(hi)} · ${n || "?"} grids">
    ${rungs}
    ${midTop !== null ? `<div class="band-mid" style="top:${midTop}%" title="mid ${fmtPrice(ch.mid)}"></div>` : ""}
    ${hasPrice ? `<div class="cursor" style="top:${cursorTop}%"></div>
    <div class="price-flag" style="top:${cursorTop}%">${fmtPrice(price)}</div>` : ""}
    <span class="edge edge--lo">${fmtPrice(lo)}</span>
    <span class="edge edge--hi">${fmtPrice(hi)}</span>
  </div>`;
}

function slotCard(bot) {
  const obs = bot.observed || {};
  const pnl = obs.unrealized_pnl;
  const status = (obs.status || "unknown").toLowerCase();
  const dead = status !== "active" && status !== "unknown";
  const stagIf = (bot.stagnation_policy || {}).stagnant_if || {};
  const card = el("article", {
    class: `card slot-card${dead ? " slot-card--dead" : ""}`,
    "data-slot": String(bot.slot),
  });

  const flags = [];
  if (bot.adopted) flags.push(`<span class="badge badge--dim">adopted</span>`);
  if (bot.stagnant) flags.push(`<span class="badge badge--warn">stagnant</span>`);
  if (bot.needs_reanalysis) flags.push(`<span class="badge badge--warn">re-analysis</span>`);
  if (bot.force_rotate) flags.push(`<span class="badge badge--violet">rotate queued</span>`);
  if (dead) flags.push(`<span class="badge badge--bad">${esc(status)}</span>`);

  card.innerHTML = `
    <div class="slot-head">
      <span class="slot-no">SLOT ${esc(bot.slot)}</span>
      <span class="venue-tag venue-tag--${esc(bot.venue)}">${esc(bot.venue)}</span>
      <span class="slot-flags">${flags.join("")}</span>
    </div>
    <div class="symbol-line">
      <span class="symbol">${esc(bot.symbol || "?")}</span>
      <span class="gridtype">${esc(bot.grid_type || "—")} grid</span>
    </div>
    ${ladderHTML(bot)}
    <div class="slot-metrics">
      <div class="metric"><div class="m-label">price</div><div class="m-value">${fmtPrice(obs.price)}</div></div>
      <div class="metric"><div class="m-label">fills 24h</div>
        <div class="m-value ${bot.stagnant ? "m-value--warn" : ""}">${esc(obs.fills_24h ?? "—")}<span style="color:var(--ink-faint);font-weight:400"> /${esc(stagIf.min_fills_24h ?? "?")}</span></div></div>
      <div class="metric"><div class="m-label">unreal. pnl</div>
        <div class="m-value ${pnl > 0 ? "m-value--good" : pnl < 0 ? "m-value--bad" : "m-value--dim"}">${pnl === null || pnl === undefined ? "—" : fmtUsd(pnl)}</div></div>
      <div class="metric"><div class="m-label">committed</div><div class="m-value">${fmtUsd(bot.committed)}</div></div>
    </div>
    <div class="slot-foot">
      <span class="slot-since">held ${esc(heldFor(bot.since) ?? "—")}</span>
      <span style="margin-left:auto"></span>
      <button class="btn btn--sm btn--danger" data-rotate="${esc(bot.slot)}">Rotate</button>
    </div>`;

  card.querySelector("[data-rotate]").addEventListener("click", () => rotateSlot(bot));
  return card;
}

function emptySlotCard(slot) {
  const c = el("article", { class: "card slot-card slot-card--empty" });
  c.innerHTML = `
    <span class="slot-no">SLOT ${esc(slot.slot)}</span>
    <span class="venue-tag venue-tag--${esc(slot.venue)}">${esc(slot.venue)}</span>
    <span class="empty-title">awaiting deploy</span>
    <span class="empty-note">Free slot — the next rescreen fills it with the best eligible candidate.</span>`;
  return c;
}

/* the readiness strip: the daemon's own dependency + capacity diagnostics,
   surfaced as a row of glanceable instrument cells. Answers the three
   questions an operator asks before trusting the loop — is the LLM chain
   up, is the browser alive, is the venue capacity actually available. */
function renderReadiness(ov) {
  const el0 = $("#readiness");
  if (!el0) return;
  const r = ov.readiness;
  if (!r || !r.reachable) {
    el0.innerHTML = `<div class="readiness-head"><span class="card-title">Readiness</span></div>
      <div class="readiness-cells"><span class="ready-cell ready-cell--off">ctl plane unreachable — diagnostics offline</span></div>`;
    el0.hidden = false;
    return;
  }

  const cells = [];
  const env = r.llm_env || {};
  const on = (v) => v ? "ready-cell--on" : "ready-cell--off";
  const dot = (v) => v ? "●" : "○";

  // LLM providers — each is a distinct fallback in the resolve chain.
  for (const [k, present] of Object.entries(env)) {
    cells.push(`<div class="ready-cell ${on(present)}" title="${esc(k)} present in env">
      <span class="ready-dot">${dot(present)}</span><span class="ready-key">${esc(k)}</span><span class="ready-val">${present ? "up" : "down"}</span>
    </div>`);
  }
  if (!Object.keys(env).length) {
    cells.push(`<div class="ready-cell ready-cell--off"><span class="ready-dot">○</span><span class="ready-key">llm</span><span class="ready-val">none</span></div>`);
  }

  // browser CDP — the only path to live WunderTrading state.
  cells.push(`<div class="ready-cell ${on(r.browser_cdp)}" title="browser CDP for WunderTrading">
    <span class="ready-dot">${dot(r.browser_cdp)}</span><span class="ready-key">browser</span><span class="ready-val">${r.browser_cdp ? "up" : "down"}</span>
  </div>`);

  // PocketBase side channel.
  cells.push(`<div class="ready-cell ${on(r.pb_env)}" title="PocketBase side-channel env">
    <span class="ready-dot">${dot(r.pb_env)}</span><span class="ready-key">pocketbase</span><span class="ready-val">${r.pb_env ? "up" : "down"}</span>
  </div>`);

  // venue capacity — the real enforced caps.
  const c = r.capacity || {};
  const oth = c.other || {}, pre = c.premium || {};
  const otherFull = oth.max > 0 && oth.active >= oth.max;
  const prePct = pre.max > 0 ? Math.round((pre.active / pre.max) * 100) : 0;
  cells.push(`<div class="ready-cell ${otherFull ? "ready-cell--warn" : "ready-cell--on"}"
      title="grid-bot capacity — non-premium ${oth.active}/${oth.max} · premium ${pre.active}/${pre.max}">
    <span class="ready-dot">${otherFull ? "▲" : "●"}</span>
    <span class="ready-key">capacity</span>
    <span class="ready-val">${oth.active}/${oth.max} · ${pre.active}/${pre.max}prem</span>
  </div>`);

  // connected profiles — flag any real-money account.
  const real = r.real_profiles || [];
  const profCount = (r.profiles || []).length;
  const prof = real.length
    ? `<div class="ready-cell ready-cell--bad" title="${esc(real.map((p) => `${p.name || p.code} · ${fmtUsd(p.balance)}`).join(" · "))}">
         <span class="ready-dot">●</span><span class="ready-key">profile</span><span class="ready-val">${profCount} (${real.length} live)</span>
       </div>`
    : `<div class="ready-cell ready-cell--on" title="${profCount} paper profile(s)">
         <span class="ready-dot">●</span><span class="ready-key">profile</span><span class="ready-val">${profCount} paper</span>
       </div>`;
  cells.push(prof);

  // capabilities — the daemon's own self-report of worker modules.
  const caps = r.capabilities || {};
  const capKeys = Object.keys(caps).filter((k) => caps[k]);
  cells.push(`<div class="ready-cell ${capKeys.length ? "ready-cell--on" : "ready-cell--off"}" title="daemon worker capabilities">
    <span class="ready-dot">${capKeys.length ? "●" : "○"}</span>
    <span class="ready-key">caps</span><span class="ready-val">${esc(capKeys.join("·") || "none")}</span>
  </div>`);

  el0.innerHTML = `<div class="readiness-head"><span class="card-title">Readiness</span>
      <span class="mono readiness-at">${esc(relTime(ov.at))}</span></div>
    <div class="readiness-cells">${cells.join("")}</div>`;
  el0.hidden = false;
}

function renderFleet(ov) {
  const board = $("#slot-board");
  board.innerHTML = "";
  const bySlot = new Map((ov.bots || []).map((b) => [String(b.slot), b]));
  const slots = (ov.slots || []).length
    ? ov.slots
    : [...bySlot.keys()].map((s) => ({ slot: s, venue: (bySlot.get(s) || {}).venue || "" }));
  for (const s of slots) {
    const bot = bySlot.get(String(s.slot));
    board.append(bot ? slotCard(bot) : emptySlotCard(s));
  }
  for (const [slotKey, bot] of bySlot) {
    if (!slots.some((s) => String(s.slot) === slotKey)) board.append(slotCard(bot));
  }

  // banners
  const banners = [];
  const d = ov.daemon || {};
  if (!d.running) {
    banners.push(`<div class="banner banner--bad">
      <div><div class="banner-title">Daemon is not running</div>
      Last persisted state is shown below (stale ${esc(relTime((ov.ctl && ov.ctl.status && ov.ctl.status.last_cycle) || null))}).
      ${d.kill_file ? "The KILL file is armed — clear it before starting." : ""}</div>
      <span style="margin-left:auto;display:flex;gap:8px;flex:none">
        ${d.kill_file ? `<button class="btn" id="b-unkill">Clear KILL</button>` : ""}
        <button class="btn" id="b-start-dry">Start (dry-run)</button>
        <button class="btn btn--primary" id="b-start-paper">Start (live-paper)</button>
      </span></div>`);
  } else if (d.kill_file) {
    banners.push(`<div class="banner banner--bad">
      <div><div class="banner-title">KILL file armed</div>The daemon halts at the next loop tick. Clear it to keep the fleet running.</div>
      <button class="btn" id="b-unkill" style="margin-left:auto">Clear KILL</button></div>`);
  } else if (d.mode === "dry-run") {
    banners.push(`<div class="banner banner--info">
      <div><div class="banner-title">Dry-run mode</div>The daemon plans and journals everything but creates no bots. Restart with live-paper to deploy.</div></div>`);
  }
  const bn = $("#fleet-banner");
  bn.innerHTML = banners.join("");
  const wire = (id, fn) => { const n = bn.querySelector(id); if (n) n.addEventListener("click", fn); };
  wire("#b-unkill", ctlUnkill);
  wire("#b-start-dry", () => ctlStart(false));
  wire("#b-start-paper", () => ctlStart(true));
}

function renderFeed(journal) {
  const feed = $("#feed");
  const rows = [...journal].reverse().slice(0, 60); // newest first
  feed.innerHTML = rows.map((e) => {
    const at = String(e.at || "").slice(11, 19);
    const kind = String(e.kind || "?").replace(/_/g, "-");
    return `<li><span class="f-at">${esc(at)}</span><span class="f-kind k--${esc(kind)}">${esc(kind)}</span><span class="f-msg">${esc(e.msg || "")}</span></li>`;
  }).join("") || `<li><span class="f-msg">No events yet — the journal fills as the daemon cycles.</span></li>`;
  $("#feed-age").textContent = relTime(rows[0] && rows[0].at);
}

function renderScreen(screen) {
  const box = $("#screen-list");
  $("#screen-at").textContent = screen ? relTime(screen.at) : "";
  if (!screen || !(screen.top || []).length) {
    box.innerHTML = `<div class="empty-note" style="padding:14px;">No rescreen run card yet — wait for the next cycle or force one.</div>`;
    return;
  }
  box.innerHTML = (screen.top || []).slice(0, 5).map((c, i) => `
    <div class="candidate">
      <span class="rank">${String(i + 1).padStart(2, "0")}</span>
      <span class="venue-tag venue-tag--${esc(c.venue)}">${esc(c.venue)}</span>
      <span class="sym">${esc(c.symbol)}</span>
      <span class="badge badge--dim">${esc(c.regime || "?")}</span>
      <span class="score">${esc(fmtNum(c.score_final, 1))}</span>
    </div>`).join("");
}

function renderSummary(ov) {
  const d = ov.daemon || {};
  const cd = ov.config_digest || {};
  const r = ov.readiness || {};
  const c = r.capacity || {};
  const oth = c.other || {}, pre = c.premium || {};
  const lim = r.account_limits || {};
  const dashGrid = lim.gridBots || {};
  const capRow = r.reachable
    ? `<div class="row"><span class="k">Capacity</span><span class="v" title="enforced by grid_bots/upsert per exchange tier">${oth.active}/${oth.max} non-prem · ${pre.active}/${pre.max} prem</span></div>
       <div class="row"><span class="k">Dashboard gridBots</span><span class="v" title="dashboard view — does not reflect the per-tier cap">${esc(dashGrid.active ?? "—")}/${esc(dashGrid.max ?? "—")}</span></div>`
    : `<div class="row"><span class="k">Capacity</span><span class="v">ctl offline</span></div>`;
  $("#fleet-summary").innerHTML = `
    <div class="row"><span class="k">Mode</span><span class="v">${esc(d.mode || "—")}${d.supervisor === "launchd" ? " · launchd" : ""}</span></div>
    <div class="row"><span class="k">Fund size</span><span class="v">${fmtUsd(cd.total_usd)}</span></div>
    <div class="row"><span class="k">Committed</span><span class="v">${fmtUsd(ov.committed_usd)}</span></div>
    <div class="row"><span class="k">Rescreen cadence</span><span class="v">${esc(cd.rescreen_minutes ?? "—")} min</span></div>
    <div class="row"><span class="k">Health poll</span><span class="v">${esc(cd.watch_interval_s ?? "—")} s</span></div>
    <div class="row"><span class="k">Archetypes tracked</span><span class="v">${Object.keys((ov.reliability || {}).archetypes || {}).length}</span></div>
    ${capRow}`;
}

/* ── decisions ────────────────────────────────────────────────────── */

let decisions = [];

async function loadDecisions() {
  try {
    decisions = (await api("/api/decisions?limit=400")).decisions || [];
  } catch (e) { toast(`decisions: ${e.message}`, true); return; }
  renderDecisions();
}

function renderDecisions() {
  const q = ($("#dec-filter").value || "").toLowerCase();
  const state = $("#dec-state").value;
  const rows = decisions.filter((r) => {
    if (state === "open" && r.outcome) return false;
    if (state === "closed" && !r.outcome) return false;
    if (!q) return true;
    return [r.symbol, r.venue, r.regime, r.grid_type, r.decision, r.id]
      .some((v) => String(v || "").toLowerCase().includes(q));
  });
  $("#dec-count").textContent = `${rows.length} shown · ${decisions.length} total`;
  $("#dec-body").innerHTML = rows.map((r) => {
    const outcome = r.outcome;
    const stateBadge = outcome
      ? `<span class="badge ${outcome.realized_pnl >= 0 ? "badge--ok" : "badge--bad"}" title="${esc(outcome.reason || "")}">closed ${fmtUsd(outcome.realized_pnl)}</span>`
      : `<span class="badge badge--dim">open</span>`;
    const go = String(r.decision || "").toUpperCase().includes("GO");
    return `<tr>
      <td class="td-mono">${esc(r.id)}</td>
      <td class="td-mono">${esc(String(r.at || "").replace("T", " ").slice(5, 16))}</td>
      <td class="td-mono"><span class="venue-tag venue-tag--${esc(r.venue)}">${esc(r.venue)}</span>:${esc(r.symbol)}</td>
      <td><div class="regime-cell"><span>${esc(r.regime || "—")}</span>${r.llm_degraded
        ? '<span class="badge badge--warn" title="LLM chain unavailable; rule fallback">degraded</span>' : ""}</div></td>
      <td class="td-mono">${esc(r.grid_type || "—")}</td>
      <td><span class="badge ${go ? "badge--ok" : "badge--bad"}">${esc(r.decision || "?")}</span></td>
      <td class="td-mono">${esc(fmtNum(r.score_final, 1))}</td>
      <td class="td-mono">${esc(fmtNum(r.step_pct, 3))}%</td>
      <td class="td-mono">${esc(r.slot ?? "—")}</td>
      <td>${stateBadge}</td>
      <td><div class="rationale" title="${esc(r.rationale || "")}">${esc(r.rationale || "—")}</div></td>
    </tr>`;
  }).join("") || `<tr><td colspan="11"><div class="empty-note">No decisions match. The ledger fills as the daemon deliberates.</div></td></tr>`;
}
$("#dec-filter").addEventListener("input", renderDecisions);
$("#dec-state").addEventListener("change", renderDecisions);

/* ── run cards ────────────────────────────────────────────────────── */

async function loadReports() {
  let list;
  try { list = (await api("/api/reports")).reports || []; }
  catch (e) { toast(`run cards: ${e.message}`, true); return; }
  const box = $("#rc-list");
  box.innerHTML = list.map((r) => `
    <div class="runcard-item" data-stem="${esc(r.stem)}" role="button" tabindex="0">
      <span class="rc-kind">${esc(r.kind)}</span>
      <span class="rc-stamp">${esc(String(r.at || r.stem).replace("T", " ").slice(0, 16))}</span>
      <span style="margin-left:auto" class="mono">${r.json ? "json" : ""}${r.md ? " md" : ""}</span>
    </div>`).join("") || `<div class="empty-note">No run cards yet — one lands here after every cycle.</div>`;
  for (const item of box.querySelectorAll(".runcard-item")) {
    const open = () => openRunCard(item.dataset.stem);
    item.addEventListener("click", open);
    item.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
  }
}

async function openRunCard(stem) {
  let card;
  try { card = await api(`/api/reports/${encodeURIComponent(stem)}`); }
  catch (e) { toast(`run card: ${e.message}`, true); return; }
  $("#rc-list").hidden = true;
  $("#rc-back").hidden = false;
  $("#rc-detail").hidden = false;
  $("#rc-md").innerHTML = renderMarkdown(card.md || "*(no markdown body)*");
  $("#rc-json").textContent = JSON.stringify(card.json, null, 2);
}
$("#rc-back").addEventListener("click", () => {
  $("#rc-list").hidden = false;
  $("#rc-back").hidden = true;
  $("#rc-detail").hidden = true;
});

/* minimal markdown: headers, tables, lists, bold, code, hr */
function renderMarkdown(md) {
  const lines = md.split("\n");
  const out = [];
  let table = [];
  const flushTable = () => {
    if (!table.length) return;
    const [head, , ...body] = table;
    const cells = (r) => r.split("|").slice(1, -1).map((c) => c.trim());
    out.push(`<table><thead><tr>${cells(head).map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead>
      <tbody>${body.map((r) => `<tr>${cells(r).map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
    table = [];
  };
  const inline = (s) => esc(s)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
  for (const line of lines) {
    if (/^\|.*\|$/.test(line.trim())) { table.push(line.trim()); continue; }
    flushTable();
    const t = line.trim();
    if (!t) continue;
    if (t.startsWith("### ")) out.push(`<h3>${inline(t.slice(4))}</h3>`);
    else if (t.startsWith("## ")) out.push(`<h2>${inline(t.slice(3))}</h2>`);
    else if (t.startsWith("# ")) out.push(`<h1>${inline(t.slice(2))}</h1>`);
    else if (t.startsWith("- ")) out.push(`<ul><li>${inline(t.slice(2))}</li></ul>`);
    else out.push(`<p>${inline(t)}</p>`);
  }
  flushTable();
  // merge consecutive lists
  return out.join("").replace(/<\/ul><ul>/g, "");
}

/* ── reliability ──────────────────────────────────────────────────── */

async function loadReliability() {
  let rel;
  try { rel = await api("/api/reliability"); }
  catch (e) { toast(`reliability: ${e.message}`, true); return; }
  const ladder = rel.ladder || {};
  const archs = Object.entries(rel.archetypes || {}).sort((a, b) =>
    (b[1].samples || 0) - (a[1].samples || 0));
  $("#rel-body").innerHTML = archs.map(([name, s]) => {
    const full = ladder.full_samples || 30, probe = ladder.probe_samples || 10;
    const pctFull = Math.min(100, ((s.samples || 0) / full) * 100);
    const tierBadge = {
      base: "badge--dim", probe: "badge--violet",
      full: "badge--ok", killed: "badge--bad",
    }[s.tier] || "badge--dim";
    return `<tr>
      <td><b>${esc(name)}</b></td>
      <td class="td-mono">${esc(s.samples ?? 0)}</td>
      <td><div class="tier-track" title="${esc(s.samples)} / ${full} samples to full">
        <div class="fill${s.tier === "killed" ? " fill--killed" : ""}" style="width:${pctFull.toFixed(1)}%"></div>
        <div class="mark" style="left:${(probe / full * 100).toFixed(1)}%" title="probe @${probe}"></div>
      </div></td>
      <td class="td-mono ${(s.profit_factor || 0) >= (ladder.pf_pass || 1.3) ? "m-value--good" : ""}">${esc(fmtNum(s.profit_factor, 2))}</td>
      <td class="td-mono ${(s.recent_pf || 0) < (ladder.pf_kill || 1.0) ? "m-value--bad" : ""}">${esc(fmtNum(s.recent_pf, 2))}</td>
      <td class="td-mono">${esc(fmtPct(s.win_rate))}</td>
      <td class="td-mono">${fmtUsd(s.expectancy_usd)}</td>
      <td class="td-mono">${fmtUsd(s.max_dd_usd)}</td>
      <td><span class="badge ${tierBadge}">${esc(s.tier)}</span></td>
    </tr>`;
  }).join("") || `<tr><td colspan="9"><div class="empty-note">No closed round-trips yet — the ledger fills as bots complete trades (24h refresh, or force one from Fleet).</div></td></tr>`;
}

/* ── config ───────────────────────────────────────────────────────── */

let configBaseline = {}; // path -> original value (numbers)

async function loadConfig() {
  let payload;
  try { payload = await api("/api/config"); }
  catch (e) { toast(`config: ${e.message}`, true); return; }
  const editable = payload.editable || {};
  configBaseline = {};
  const groups = new Map();
  for (const [path, rule] of Object.entries(editable)) {
    if (!groups.has(rule.group)) groups.set(rule.group, []);
    groups.get(rule.group).push([path, rule]);
    configBaseline[path] = rule.value;
  }
  const fields = $("#cfg-fields");
  fields.innerHTML = "";
  for (const [group, items] of groups) {
    fields.append(el("div", { class: "field-group-title" }, group));
    for (const [path, rule] of items) {
      configBaseline[path] = rule.value;
      const input = el("input", {
        type: "number", step: "any", value: rule.value ?? "",
        min: rule.min, max: rule.max, "data-path": path,
        id: `cfg-${path.replace(/\./g, "-")}`,
      });
      input.addEventListener("input", () => input.classList.toggle("dirty",
        Number(input.value) !== Number(configBaseline[path])));
      fields.append(el("div", { class: "field" },
        el("div", { class: "f-name" }, rule.label,
          el("span", { class: "f-path" }, path)),
        input,
        el("div", { class: "f-range" }, `${rule.min} – ${rule.max}${rule.unit ? " " + rule.unit : ""}`)));
    }
  }
  renderConfigReadonly(payload.config || {});
}

function renderConfigReadonly(cfg) {
  const skip = new Set(Object.keys(configBaseline));
  const rows = [];
  const flatten = (obj, prefix) => {
    for (const [k, v] of Object.entries(obj || {})) {
      const path = prefix ? `${prefix}.${k}` : k;
      if (v && typeof v === "object" && !Array.isArray(v)) { flatten(v, path); continue; }
      if (skip.has(path)) continue;
      rows.push([path, Array.isArray(v) ? JSON.stringify(v) : v]);
    }
  };
  flatten(cfg, "");
  const live = ((cfg.autonomy || {}).live_profiles) || [];
  let html = "";
  if (live.length) {
    html += `<div class="banner banner--bad" style="margin:0 0 12px;">
      <div><div class="banner-title">live_profiles is non-empty</div>
      Real-money deployment is armed in config. The daemon still needs live_allow + reliability gates, but double-check this is intended.</div></div>`;
  }
  html += rows.map(([k, v]) =>
    `<div class="kv-row"><span class="k">${esc(k)}</span><span class="v">${esc(v === null || v === undefined ? "—" : v)}</span></div>`).join("");
  $("#cfg-readonly").innerHTML = html;
}

$("#cfg-save").addEventListener("click", async () => {
  const edits = {};
  for (const input of $("#cfg-fields").querySelectorAll("input[data-path]")) {
    if (input.classList.contains("dirty") && input.value !== "") {
      edits[input.dataset.path] = Number(input.value);
    }
  }
  const keys = Object.keys(edits);
  if (!keys.length) { toast("No changes to save."); return; }
  const { ok } = await confirmDialog({
    title: "Apply config changes",
    body: [el("div", {}, `Writing `, el("code", {}, "config.yaml"),
      ` — ${keys.length} value${keys.length > 1 ? "s" : ""}: `),
      el("div", { class: "mono", style: "font-size:12px;margin-top:6px;" },
        keys.map((k) => `${k} → ${edits[k]}`).join(", "))],
    label: "Write config",
  });
  if (!ok) return;
  try {
    const resp = await api("/api/config", { method: "POST", body: { edits } });
    const applied = (resp.applied || []).length;
    const rejected = resp.rejected || [];
    toast(`Wrote ${applied} value${applied === 1 ? "" : "s"} (backup kept).`);
    for (const r of rejected) toast(`rejected ${r.path}: ${r.reason}`, true);
    $("#config-banner").innerHTML = `<div class="banner banner--info">
      <div><div class="banner-title">Config written — restart required</div>
      The daemon reads config.yaml at startup. Restart it to apply.</div>
      <button class="btn btn--primary" id="cfg-restart" style="margin-left:auto">Restart daemon</button></div>`;
    $("#cfg-restart").addEventListener("click", ctlRestart);
    loadConfig();
  } catch (e) {
    toast(`config save failed: ${e.message}`, true);
  }
});

/* ── logs ─────────────────────────────────────────────────────────── */

let logStick = true;

async function loadLogs(force = false) {
  if (activeView !== "logs" && !force) return;
  const follow = $("#log-follow").checked;
  const params = new URLSearchParams({
    lines: $("#log-lines").value,
    ...( ($("#log-grep").value || "").trim() ? { grep: $("#log-grep").value.trim() } : {}),
  });
  let data;
  try { data = await api(`/api/logs?${params}`); }
  catch (e) { return; }
  const box = $("#logbox");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  box.textContent = (data.lines || []).join("\n") || "— no matching lines —";
  $("#log-count").textContent = `${data.total} line(s)`;
  if (follow && (logStick || atBottom)) {
    box.scrollTop = box.scrollHeight;
  }
}
$("#log-grep").addEventListener("input", () => loadLogs());
$("#log-lines").addEventListener("change", () => loadLogs());
$("#log-follow").addEventListener("change", () => loadLogs());
$("#logbox").addEventListener("scroll", () => {
  const box = $("#logbox");
  logStick = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
});

/* ── controls ─────────────────────────────────────────────────────── */

async function rotateSlot(bot) {
  const { ok } = await confirmDialog({
    title: `Rotate slot ${bot.slot}`,
    body: [
      el("div", {}, `Stop, close and delete `, el("code", {}, `${bot.venue}:${bot.symbol}`),
        `, then deploy the best challenger on the next rescreen. Per-token cooldown applies.`),
    ],
    label: "Queue rotation", danger: true,
  });
  if (!ok) return;
  try {
    await api("/api/ctl/rotate", { method: "POST", body: { slot: bot.slot } });
    toast(`Rotation queued for slot ${bot.slot} — applied on next rescreen.`);
    loadOverview();
  } catch (e) { toast(`rotate failed: ${e.data && e.data.error || e.message}`, true); }
}

$("#ctl-rescreen").addEventListener("click", async () => {
  const { ok } = await confirmDialog({
    title: "Force rescreen",
    body: [el("div", {}, "Runs screen → deliberate (LLM calls) → guard → deploy now, outside the hourly cadence. Deployments still respect every guardrail.")],
    label: "Run rescreen",
  });
  if (!ok) return;
  try {
    await api("/api/ctl/rescreen", { method: "POST", body: {} });
    toast("Rescreen queued.");
    loadOverview();
  } catch (e) { toast(`rescreen failed: ${e.message}`, true); }
});

$("#ctl-reliability").addEventListener("click", async () => {
  try {
    await api("/api/ctl/reliability", { method: "POST", body: {} });
    toast("Reliability refresh queued.");
  } catch (e) { toast(`reliability failed: ${e.message}`, true); }
});

$("#ctl-halt").addEventListener("click", async () => {
  const { ok } = await confirmDialog({
    title: "Halt the daemon",
    body: [el("div", {}, "Writes the ", el("code", {}, "KILL"), " file. The daemon halts at the next loop tick; running bots keep running on WunderTrading until you stop them.")],
    label: "Arm KILL", danger: true,
  });
  if (!ok) return;
  try {
    await api("/api/ctl/kill", { method: "POST", body: { confirm: true } });
    toast("KILL armed — daemon halts at the next tick.", true);
    loadOverview();
  } catch (e) { toast(`kill failed: ${e.message}`, true); }
});

async function ctlUnkill() {
  const { ok } = await confirmDialog({
    title: "Clear the KILL file",
    body: [el("div", {}, "Allows the daemon to keep running / start again.")],
    label: "Clear KILL",
  });
  if (!ok) return;
  try {
    await api("/api/ctl/unkill", { method: "POST", body: { confirm: true } });
    toast("KILL cleared.");
    loadOverview();
  } catch (e) { toast(`unkill failed: ${e.message}`, true); }
}
$("#ctl-unkill").addEventListener("click", ctlUnkill);

async function ctlStart(livePaper) {
  const killArmed = lastOverview && lastOverview.daemon && lastOverview.daemon.kill_file;
  const { ok, checked } = await confirmDialog({
    title: livePaper ? "Start daemon (live-paper)" : "Start daemon (dry-run)",
    body: [el("div", {}, livePaper
      ? "Creates and manages real WunderTrading paper bots (no real money — paper profiles only)."
      : "Plans and journals everything, creates nothing.")],
    label: "Start", danger: false,
    checkbox: killArmed ? "Clear the KILL file first" : null,
  });
  if (!ok) return;
  try {
    await api("/api/daemon/start", {
      method: "POST",
      body: { confirm: true, live_paper: livePaper, clear_kill: checked },
    });
    toast("Daemon starting…");
    setTimeout(loadOverview, 2500);
  } catch (e) { toast(`start failed: ${e.data && e.data.error || e.message}`, true, 6500); }
}

async function ctlRestart() {
  const killArmed = lastOverview && lastOverview.daemon && lastOverview.daemon.kill_file;
  const { ok, checked } = await confirmDialog({
    title: "Restart daemon",
    body: [el("div", {}, "Under launchd this is a supervised kickstart; otherwise stop + start. Unapplied config takes effect after restart.")],
    label: "Restart",
    checkbox: killArmed ? "Clear the KILL file first" : null,
  });
  if (!ok) return;
  try {
    await api("/api/daemon/restart", {
      method: "POST", body: { confirm: true, clear_kill: checked },
    });
    toast("Restarting — daemon back within ~30s.");
    setTimeout(loadOverview, 4000);
  } catch (e) { toast(`restart failed: ${e.data && e.data.error || e.message}`, true, 6500); }
}
$("#ctl-restart").addEventListener("click", ctlRestart);

$("#ctl-stop").addEventListener("click", async () => {
  const { ok, checked } = await confirmDialog({
    title: "Stop the daemon",
    body: [el("div", {}, "Arms KILL, sends SIGTERM, waits up to 8s. Running bots are left as-is on WunderTrading.")],
    label: "Stop", danger: true,
    checkbox: "Force-kill (SIGKILL) if it ignores SIGTERM",
  });
  if (!ok) return;
  try {
    const r = await api("/api/daemon/stop", {
      method: "POST", body: { confirm: true, force: checked },
    });
    toast(r.stopped ? "Daemon stopped. KILL file stays armed." : "Stop timed out — check Logs.", !r.stopped);
    loadOverview();
  } catch (e) { toast(`stop failed: ${e.data && e.data.error || e.message}`, true); }
});

/* ── boot + polling ───────────────────────────────────────────────── */

let tick = 0;
setInterval(() => {
  if (document.hidden) return;
  tick++;
  loadOverview(); // cheap local reads; keeps the statusbar honest everywhere
  if (activeView === "decisions" && tick % 4 === 0) loadDecisions();
  if (activeView === "logs" && tick % 2 === 0) loadLogs();
}, 5000);

async function boot() {
  const hash = (location.hash || "#fleet").slice(1);
  selectView(VIEWS.includes(hash) ? hash : "fleet");
  loadOverview();
  try {
    const meta = await api("/api/meta");
    $("#footnote").textContent =
      `grid/autonomy console · paper fleet · console :${meta.console_port} · ctl :${meta.ctl_port} · pb ${meta.pocketbase.replace("http://", "")}`;
  } catch { /* footnote stays default */ }
}
boot();

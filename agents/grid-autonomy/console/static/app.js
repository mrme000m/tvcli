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
const fmtSignedUsd = (v) => (v === null || v === undefined || isNaN(v))
  ? "—" : `${Number(v) >= 0 ? "+" : "−"}$${Math.abs(Number(v)).toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
const isNum = (v) => typeof v === "number" && isFinite(v) ||
  (typeof v === "string" && v !== "" && !isNaN(Number(v)));
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

function confirmDialog({ title, body, label = "Confirm", danger = false, checkbox = null, checkbox2 = null }) {
  return new Promise((resolve) => {
    const root = $("#modal-root");
    const box = el("div", { class: "modal-backdrop" });
    const checkRef = { input: null };
    const checkRef2 = { input: null };
    const modal = el("div", { class: "modal", role: "dialog", "aria-modal": "true" },
      el("h3", {}, title),
      el("div", { class: "modal-body" }, ...body),
      checkbox ? el("label", { class: "check-line" },
        (checkRef.input = el("input", { type: "checkbox" })),
        el("span", {}, checkbox)) : null,
      checkbox2 ? el("label", { class: "check-line" },
        (checkRef2.input = el("input", { type: "checkbox" })),
        el("span", {}, checkbox2)) : null,
      el("div", { class: "modal-actions" },
        el("button", { class: "btn", onclick: () => done(false) }, "Cancel"),
        el("button", { class: `btn ${danger ? "btn--danger" : "btn--primary"}`, onclick: () => done(true) }, label)));
    function done(ok) {
      root.innerHTML = "";
      document.removeEventListener("keydown", onKey);
      resolve({ ok, checked: checkRef.input ? checkRef.input.checked : false,
                checked2: checkRef2.input ? checkRef2.input.checked : false });
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

const VIEWS = ["fleet", "decisions", "reports", "optimizer", "reliability", "config", "logs"];
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
  if (name === "optimizer") loadOptimizer();
  if (name === "reliability") loadReliability();
  if (name === "config") { loadConfig(); loadLlm(); }
  if (name === "logs") loadLogs(true);
}

for (const v of VIEWS) $(`#tab-${v}`).addEventListener("click", () => selectView(v));

/* ── overview / statusbar / fleet ─────────────────────────────────── */

let lastOverview = null;
let lastStatus = null;       // proxied daemon /status (fail-soft: null when down)
let lastPnlPoints = null;   // /api/pnl points (newest-first)

async function loadOverview() {
  let ov;
  try {
    ov = await api("/api/overview");
  } catch (e) {
    renderStatusbar(null);
    return;
  }
  lastOverview = ov;
  let st = null;
  try {
    st = await api("/api/status");   // fail-soft proxy — 200 + {"error"} when down
  } catch (e) { st = null; }
  if (!st || st.error) st = null;
  lastStatus = st;
  renderStatusbar(ov);
  if (activeView === "fleet") {
    renderReadiness(ov);
    renderFleet(ov, st);
    renderFleetHeader(ov, st);
    renderVetoStrip(ov, st);
    renderFeed(ov.journal_tail || []);
    renderScreen(ov.screen);
    renderSummary(ov);
    drawPnlChart(lastPnlPoints || []);
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
  const unrl = obs.unrealized_pnl;
  const real = obs.realized_pnl;
  const realC = obs.realized_pnl_completed;
  const realP = obs.realized_pnl_panic;
  const net = (isNum(real) ? real : 0) + (isNum(unrl) ? unrl : 0);
  const hasNet = isNum(real) || isNum(unrl);
  const status = (obs.status || "unknown").toLowerCase();
  const dead = status !== "active" && status !== "unknown";
  const stagIf = (bot.stagnation_policy || {}).stagnant_if || {};
  const dd = obs.dd_vs_atr_band;
  const outsideBand = isNum(dd) && dd > 1;
  const card = el("article", {
    class: `card slot-card${dead ? " slot-card--dead" : ""}`,
    "data-slot": String(bot.slot),
  });

  const flags = [];
  if (bot.adopted) flags.push(`<span class="badge badge--dim">adopted</span>`);
  if (bot.stagnant) flags.push(`<span class="badge badge--warn">stagnant</span>`);
  if (bot.needs_reanalysis) flags.push(`<span class="badge badge--warn">re-analysis</span>`);
  if (bot.force_rotate) flags.push(`<span class="badge badge--violet">rotate queued</span>`);
  if (obs.loss_veto) flags.push(`<span class="badge badge--bad">loss veto</span>`);
  if (obs.exit_queued) flags.push(`<span class="badge badge--violet">exit queued</span>`);
  if (outsideBand) flags.push(`<span class="badge badge--bad" title="drawdown ${fmtNum(dd, 2)}× the ATR band — price is outside the channel the grid was built for">outside band</span>`);
  if (dead) flags.push(`<span class="badge badge--bad">${esc(status)}</span>`);

  const realizedCell = isNum(real)
    ? `<div class="m-value ${real > 0 ? "m-value--good" : real < 0 ? "m-value--bad" : "m-value--dim"}">${fmtSignedUsd(real)}</div>
       ${(isNum(realC) || isNum(realP)) ? `<div class="m-sub">${isNum(realC) ? `comp ${fmtUsd(realC)}` : ""}${isNum(realC) && isNum(realP) ? " · " : ""}${isNum(realP) ? `panic ${fmtUsd(realP)}` : ""}</div>` : ""}`
    : `<div class="m-value m-value--dim" title="realized PnL not reported by this daemon build yet">—</div>`;

  const trips = (isNum(obs.trips_completed) || isNum(obs.trips_panic))
    ? `${obs.trips_completed ?? 0}<span class="m-sub-inline">${isNum(obs.trips_panic) ? ` (${obs.trips_panic} panic)` : ""}</span>`
    : `<span class="m-value--dim" title="trip counters not reported yet">—</span>`;

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
      <div class="metric"><div class="m-label">realized</div>${realizedCell}</div>
      <div class="metric"><div class="m-label">unrealized</div>
        <div class="m-value ${unrl > 0 ? "m-value--good" : unrl < 0 ? "m-value--bad" : "m-value--dim"}">${isNum(unrl) ? fmtSignedUsd(unrl) : "—"}</div></div>
      <div class="metric"><div class="m-label">net pnl</div>
        <div class="m-value ${hasNet ? (net > 0 ? "m-value--good" : net < 0 ? "m-value--bad" : "m-value--dim") : "m-value--dim"}">${hasNet ? fmtSignedUsd(net) : "—"}</div></div>
      <div class="metric"><div class="m-label">trips</div><div class="m-value">${trips}</div></div>
      <div class="metric"><div class="m-label">open lines</div>
        <div class="m-value ${isNum(obs.open_losing) && obs.open_losing > 0 ? "m-value--warn" : ""}" title="${isNum(obs.open_losing) ? `${obs.open_losing} of ${obs.open_lines ?? "?"} lines losing` : ""}">${esc(obs.open_lines ?? "—")}<span style="color:var(--ink-faint);font-weight:400"> (${esc(obs.open_losing ?? "?")} losing)</span></div></div>
      <div class="metric"><div class="m-label">dd vs band</div>
        <div class="m-value ${outsideBand ? "m-value--bad" : ""}">${isNum(dd) ? `${fmtNum(dd, 2)}×` : "—"}</div></div>
      <div class="metric"><div class="m-label">budget</div><div class="m-value">${fmtUsd(bot.committed)}</div></div>
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

function renderFleet(ov, st) {
  const board = $("#slot-board");
  board.innerHTML = "";
  // prefer the live ctl /status observations over state.json's last snapshot
  const liveObs = (st && st.active_bots) || {};
  const bots = (ov.bots || []).map((b) => {
    const lo = liveObs[String(b.slot)];
    return (lo && lo.observed && Object.keys(lo.observed).length)
      ? { ...b, observed: { ...b.observed, ...lo.observed } } : b;
  });
  const bySlot = new Map(bots.map((b) => [String(b.slot), b]));
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

/* ── fleet PnL header + veto strip + pnl timeline ─────────────────── */

function fleetPnlData(ov, st) {
  /* True PnL per the audit contract: daemon /status "pnl" block when the
     restarted daemon provides it, otherwise derived from per-bot observed
     fields (realized incl. panic + unrealized). Every field degrades to
     null when the running daemon predates the field. */
  const out = { source: null, realized: null, unrealized: null, net: null,
    completed: null, panic: null, fills: null,
    committed: null, idle: null, total: null };
  const ab = (st && st.active_bots) || {};
  const obsList = Object.values(ab).map((b) => (b && b.observed) || {});
  const has = (f) => obsList.some((o) => isNum(o[f]));
  const sum = (f) => obsList.reduce((a, o) => a + (isNum(o[f]) ? Number(o[f]) : 0), 0);
  const p = (st && typeof st.pnl === "object" && st.pnl) || null;
  if (p && (isNum(p.realized) || isNum(p.net))) out.source = "ctl pnl block";
  else if (obsList.length && (has("realized_pnl") || has("unrealized_pnl"))) out.source = "derived from bots";
  out.realized = p && isNum(p.realized) ? p.realized : (has("realized_pnl") ? sum("realized_pnl") : null);
  out.unrealized = p && isNum(p.unrealized) ? p.unrealized : (has("unrealized_pnl") ? sum("unrealized_pnl") : null);
  out.completed = has("realized_pnl_completed") ? sum("realized_pnl_completed") : null;
  out.panic = has("realized_pnl_panic") ? sum("realized_pnl_panic") : null;
  out.net = p && isNum(p.net) ? p.net
    : (out.realized !== null || out.unrealized !== null)
      ? (out.realized || 0) + (out.unrealized || 0) : null;
  out.fills = p && isNum(p.fills_24h) ? p.fills_24h : (has("fills_24h") ? sum("fills_24h") : null);
  const committedMap = (st && st.committed) || {};
  out.committed = p && isNum(p.committed_usd) ? p.committed_usd
    : Object.values(committedMap).reduce((a, v) => a + (isNum(v) ? Number(v) : 0), 0) || null;
  out.total = (ov.config_digest || {}).total_usd;
  if (out.total != null && isNum(out.total)) out.total = Number(out.total);
  out.idle = p && isNum(p.idle_usd) ? p.idle_usd
    : (out.total != null && out.committed != null) ? Number(out.total) - out.committed : null;
  return out;
}

function demoCapData(st, ov) {
  /* daemon demo_cap block, else parsed from the demo-cap-veto journal
     message ("cap 5/5"), else counted actives with unknown cap. */
  const d = (st && typeof st.demo_cap === "object" && st.demo_cap) || null;
  if (d && isNum(d.active)) {
    return { active: Number(d.active),
      cap: isNum(d.cap) ? Number(d.cap) : null,
      headroom: isNum(d.headroom) ? Number(d.headroom) : null };
  }
  const active = Object.keys((st && st.active_bots) || {}).length;
  const tail = (st && st.journal_tail) || (ov && ov.journal_tail) || [];
  for (let i = tail.length - 1; i >= 0; i--) {
    const e = tail[i] || {};
    if (e.kind === "demo-cap-veto") {
      const m = /(\d+)\s*\/\s*(\d+)/.exec(String(e.msg || ""));
      if (m) return { active: Number(m[1]), cap: Number(m[2]), headroom: Number(m[2]) - Number(m[1]), vetoed: true };
    }
  }
  return { active, cap: null, headroom: null, vetoed: false };
}

function renderFleetHeader(ov, st) {
  const box = $("#pnl-header");
  if (!box) return;
  const p = fleetPnlData(ov, st);
  const cap = demoCapData(st, ov);
  const nBots = Object.keys((st && st.active_bots) || {}).length || (ov.bots || []).length;

  const netCls = p.net == null ? "m-value--dim" : p.net > 0 ? "m-value--good" : p.net < 0 ? "m-value--bad" : "m-value--dim";
  const idlePct = (p.idle == null || !p.total) ? null : (p.idle / p.total) * 100;

  let realizedSub;
  if (p.completed != null || p.panic != null) {
    realizedSub = `<div class="pnl-sub">${p.completed != null ? `completed ${fmtUsd(p.completed)}` : ""}${p.completed != null && p.panic != null ? " \u00b7 " : ""}${p.panic != null ? `panic ${fmtUsd(p.panic)}` : ""}</div>`;
  } else {
    realizedSub = `<div class="pnl-sub pnl-sub--faint" title="per-bot completed/panic split arrives with the daemon restart">split (completed/panic) not reported \u2014 daemon pre-restart</div>`;
  }

  // demo-cap meter: 5/5 means every new deploy is vetoed at the platform cap
  let capCell;
  if (cap.cap != null && cap.cap > 0) {
    const full = cap.active >= cap.cap;
    const w = Math.min(100, (cap.active / cap.cap) * 100);
    capCell = `<div class="pnl-cell cap-meter${full ? " cap-meter--full" : ""}" title="paper/demo grid-bot platform cap${cap.headroom != null ? ` \u00b7 headroom ${cap.headroom}` : ""}">
      <div class="m-label">demo cap</div>
      <div class="cap-bar"><div class="cap-fill" style="width:${w.toFixed(1)}%"></div></div>
      <div class="cap-label">${full ? `<b>demo bots ${cap.active}/${cap.cap} \u2014 deploys blocked</b>` : `demo bots ${cap.active}/${cap.cap}`}</div>
    </div>`;
  } else {
    capCell = `<div class="pnl-cell" title="cap not reported by this daemon build">
      <div class="m-label">demo cap</div>
      <div class="m-value m-value--dim">${cap.active} paper bots \u00b7 cap unknown</div>
      <div class="pnl-sub pnl-sub--faint">demo_cap block arrives with the daemon restart</div></div>`;
  }

  box.innerHTML = `
    <div class="pnl-header-grid">
      <div class="pnl-hero">
        <div class="pnl-hero-label">TRUE NET PnL <span class="pnl-hero-note" title="realized (incl. panic exits) + unrealized mark">${p.source ? `\u00b7 ${esc(p.source)}` : "\u00b7 no pnl fields yet"}</span></div>
        <div class="pnl-hero-value ${netCls}">${p.net == null ? "\u2014" : fmtSignedUsd(p.net)}</div>
      </div>
      <div class="pnl-cells">
        <div class="pnl-cell"><div class="m-label">realized</div>
          <div class="m-value ${p.realized > 0 ? "m-value--good" : p.realized < 0 ? "m-value--bad" : "m-value--dim"}">${p.realized == null ? "\u2014" : fmtSignedUsd(p.realized)}</div>
          ${realizedSub}</div>
        <div class="pnl-cell"><div class="m-label">unrealized</div>
          <div class="m-value ${p.unrealized > 0 ? "m-value--good" : p.unrealized < 0 ? "m-value--bad" : "m-value--dim"}">${p.unrealized == null ? "\u2014" : fmtSignedUsd(p.unrealized)}</div></div>
        <div class="pnl-cell"><div class="m-label">committed / idle</div>
          <div class="m-value">${fmtUsd(p.committed)} <span class="pnl-pct">(${idlePct == null ? "?" : idlePct.toFixed(0) + "% idle"})</span></div>
          <div class="pnl-sub">${fmtUsd(p.idle)} idle of ${fmtUsd(p.total)} fund${idlePct != null ? ` \u00b7 ${(100 - idlePct).toFixed(0)}% committed` : ""}</div></div>
        <div class="pnl-cell"><div class="m-label">fills 24h</div>
          <div class="m-value">${p.fills == null ? "\u2014" : p.fills}</div>
          <div class="pnl-sub">${nBots} active bot${nBots === 1 ? "" : "s"}</div></div>
        ${capCell}
      </div>
      <div class="pnl-chart">
        <div class="m-label">net \u00b7 realized \u2014 <span id="pnl-chart-meta">no history yet</span></div>
        <canvas id="pnl-canvas" width="360" height="96" role="img" aria-label="fleet PnL timeline"></canvas>
      </div>
    </div>`;
  drawPnlChart(lastPnlPoints || []);
}

function renderVetoStrip(ov, st) {
  const box = $("#veto-strip");
  if (!box) return;
  const tail = (st && st.journal_tail) || ov.journal_tail || [];
  const count = (kind) => tail.filter((e) => e && e.kind === kind).length;
  const demo = count("demo-cap-veto"), guard = count("guard-veto"), capac = count("capacity-veto");
  const top = (ov.screen && ov.screen.top && ov.screen.top[0]) || null;
  const last = tail.length ? tail[tail.length - 1] : null;
  const chips = [];
  const vetoTotal = demo + guard + capac;
  chips.push(`<span class="veto-chip${vetoTotal ? " veto-chip--warn" : ""}" title="vetoes in the current journal tail (\u2248 last few hours)">vetoes <b>${vetoTotal}</b></span>`);
  if (demo) chips.push(`<span class="veto-chip veto-chip--warn" title="new deploys skipped at the paper grid-bot platform cap">demo-cap ${demo}</span>`);
  if (guard) chips.push(`<span class="veto-chip veto-chip--warn" title="a guardrail refused a candidate">guard ${guard}</span>`);
  if (capac) chips.push(`<span class="veto-chip veto-chip--warn" title="plan/venue capacity refused a deploy">capacity ${capac}</span>`);
  chips.push(`<span class="veto-chip" title="top of the last screen board">screen top <b>${top ? `${esc(top.venue)}:${esc(top.symbol)} ${fmtNum(top.score_final, 1)}` : "\u2014"}</b></span>`);
  chips.push(`<span class="veto-chip veto-chip--dim" title="last journal event">last ${esc(relTime(last && last.at))} \u00b7 ${esc((last && last.kind) || "\u2014")}</span>`);
  box.innerHTML = `<div class="veto-cells">${chips.join("")}</div>`;
}

/* PnL timeline — inline canvas (no CDN, works offline). Two series:
   net (solid + area) and realized (thin), zero line, newest on the right. */
function drawPnlChart(points) {
  const canvas = document.getElementById("pnl-canvas");
  const meta = document.getElementById("pnl-chart-meta");
  if (!canvas) return;
  const pts = (points || []).slice().reverse().filter((p) => p && p.at); // oldest → newest
  if (meta) meta.textContent = pts.length
    ? `${pts.length} snapshot${pts.length === 1 ? "" : "s"} \u00b7 ${relTime(pts[pts.length - 1].at)}`
    : "no history yet";
  const ctx = canvas.getContext && canvas.getContext("2d");
  if (!ctx) return;
  const dpr = window.devicePixelRatio || 1;
  const W = 360, H = 96;
  if (canvas.width !== W * dpr) { canvas.width = W * dpr; canvas.height = H * dpr; }
  canvas.style.width = `${W}px`; canvas.style.height = `${H}px`;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  if (pts.length < 1) {
    ctx.fillStyle = "#7C8B83";
    ctx.font = "11px 'IBM Plex Mono', monospace";
    ctx.fillText("no pnl-snapshot history (daemon pre-restart?)", 8, H / 2);
    return;
  }
  const val = (p, k) => {
    const f = p.fleet || {};
    return isNum(f[k]) ? Number(f[k]) : null;
  };
  const series = [
    { key: "net", color: "#0A7E6D", fill: "rgba(10,126,109,0.10)", width: 2 },
    { key: "realized", color: "#9A5B04", fill: null, width: 1.25 },
  ];
  const vals = [];
  for (const p of pts) for (const s of series) { const v = val(p, s.key); if (v !== null) vals.push(v); }
  if (!vals.length) {
    ctx.fillStyle = "#7C8B83";
    ctx.font = "11px 'IBM Plex Mono', monospace";
    ctx.fillText("snapshots present but no fleet values", 8, H / 2);
    return;
  }
  let min = Math.min(0, ...vals), max = Math.max(0, ...vals);
  if (max - min < 1e-9) { max += 0.5; min -= 0.5; }
  const pad = (max - min) * 0.12;
  min -= pad; max += pad;
  const padL = 8, padR = 8, padT = 6, padB = 6;
  const X = (i) => padL + (pts.length === 1 ? (W - padL - padR) / 2
    : (i / (pts.length - 1)) * (W - padL - padR));
  const Y = (v) => padT + (1 - (v - min) / (max - min)) * (H - padT - padB);
  // zero line
  if (min < 0 && max > 0) {
    ctx.strokeStyle = "rgba(24,36,32,0.25)";
    ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(padL, Y(0)); ctx.lineTo(W - padR, Y(0)); ctx.stroke();
    ctx.setLineDash([]);
  }
  for (const s of series) {
    const xy = [];
    pts.forEach((p, i) => { const v = val(p, s.key); if (v !== null) xy.push([X(i), Y(v)]); });
    if (!xy.length) continue;
    if (s.fill) {
      ctx.beginPath();
      ctx.moveTo(xy[0][0], H - padB);
      for (const [x, y] of xy) ctx.lineTo(x, y);
      ctx.lineTo(xy[xy.length - 1][0], H - padB);
      ctx.closePath();
      ctx.fillStyle = s.fill; ctx.fill();
    }
    ctx.beginPath();
    xy.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    ctx.strokeStyle = s.color; ctx.lineWidth = s.width;
    ctx.lineJoin = "round"; ctx.lineCap = "round";
    ctx.stroke();
    const lastPt = xy[xy.length - 1];
    ctx.fillStyle = s.color;
    ctx.beginPath(); ctx.arc(lastPt[0], lastPt[1], 2.4, 0, Math.PI * 2); ctx.fill();
  }
}

async function loadPnlTimeline() {
  let data;
  try { data = await api("/api/pnl"); }
  catch (e) { lastPnlPoints = null; drawPnlChart([]); return; }
  lastPnlPoints = (data && data.points) || [];
  drawPnlChart(lastPnlPoints);
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

/* ── position optimizer (advisory recommendations + apply gate) ─────── */

let optimizerData = null;

async function loadOptimizer() {
  let d;
  try { d = await api("/api/recommendations?limit=200"); }
  catch (e) { toast(`optimizer: ${e.message}`, true); d = null; }
  if (d) { optimizerData = d; renderOptimizer(d); }
  // fast slot optimizer (ctl /optimizer): fail-soft — render a quiet
  // note when the daemon is down, never a toast storm
  let f = null;
  try { f = await api("/api/optimizer"); }
  catch (e) { f = null; }
  renderFastOptimizer(f);
}

function blockedByBadge(b) {
  if (b === "applied") return `<span class="badge badge--ok">applied</span>`;
  if (b === "apply disabled") return `<span class="badge badge--dim" title="position_optimizer.apply is false in config.yaml — advisory mode, recs never auto-edit WunderTrading">apply disabled</span>`;
  if (b === "rate limit") return `<span class="badge badge--warn" title="max_apply_per_day persisted recommendations for today already reached">rate limit</span>`;
  return `<span class="badge badge--violet" title="would apply on its next eligibility check">eligible</span>`;
}

function renderOptimizer(d) {
  const recs = (d && d.recommendations) || [];
  const applyEnabled = !!(d && d.apply);
  const maxDay = (d && d.max_apply_per_day) ?? "?";
  const persistedToday = (d && d.persisted_today) ?? "?";

  const bn = $("#opt-banner");
  if (bn) {
    if (applyEnabled) {
      bn.innerHTML = `<div class="banner banner--info"><div>
        <div class="banner-title">Autonomous apply enabled</div>
        position_optimizer.apply is true — the daemon may edit WunderTrading grids directly. ${persistedToday}/${maxDay} recommendations persisted today.</div></div>`;
    } else {
      bn.innerHTML = `<div class="banner banner--warn"><div>
        <div class="banner-title">Advisory mode — recommendations are NOT applied</div>
        position_optimizer.apply is false in config.yaml. Every recommendation below is journaled/persisted only; nothing auto-edits WunderTrading. ${persistedToday}/${maxDay} persisted today (cap: max_apply_per_day).</div></div>`;
    }
  }

  const pending = recs.filter((r) => !r.applied);
  const applied = recs.filter((r) => !!r.applied);
  $("#opt-pending-count").textContent = `${pending.length} pending \u00b7 ${applied.length} applied`;
  $("#opt-applied-count").textContent = `${applied.length} applied`;

  const row = (r, appliedMode) => {
    const delta = r.expected_delta_pct;
    const at = appliedMode ? (r.applied_at || r.at) : r.at;
    return `<tr>
      <td class="td-mono" title="${esc(r.at || "")}">${esc(String(at || "").replace("T", " ").slice(5, 16))}</td>
      <td class="td-mono">${esc(r.slot ?? "\u2014")}</td>
      <td class="td-mono">${esc(r.venue || "")}:${esc(r.symbol || "?")}</td>
      <td><span class="badge badge--violet">${esc(r.recommendation || "?")}</span></td>
      <td class="td-mono ${(delta || 0) >= 0 ? "m-value--good" : "m-value--bad"}" title="expected 24h profit improvement">${delta == null ? "\u2014" : `${delta >= 0 ? "+" : ""}${fmtNum(delta, 2)}%`}</td>
      <td class="td-mono">${r.confidence == null ? "\u2014" : fmtNum(r.confidence, 2)}</td>
      <td class="td-mono">${esc(r.trigger || "\u2014")}</td>
      ${appliedMode ? "" : `<td>${blockedByBadge(r.blocked_by)}</td>`}
      <td><div class="rationale" title="${esc(r.rationale || "")}">${esc(r.rationale || "\u2014")}</div></td>
    </tr>`;
  };

  $("#opt-pending-body").innerHTML = pending.map((r) => row(r, false)).join("") ||
    `<tr><td colspan="9"><div class="empty-note">No pending recommendations \u2014 the position optimizer emits one when a bot\u2019s grid is off-price by more than the drift threshold (15 min cadence).</div></td></tr>`;
  $("#opt-applied-body").innerHTML = applied.map((r) => row(r, true)).join("") ||
    `<tr><td colspan="8"><div class="empty-note">Nothing applied yet${applyEnabled ? "" : " \u2014 apply is disabled in config (advisory mode)"}.</div></td></tr>`;
}

/* ── fast slot optimizer (2–5m cadence capital reallocation) ───────── */

function renderFastOptimizer(f) {
  const box = $("#opt-fast");
  if (!box) return;
  const o = f && f.optimizer;
  if (!o) {
    // ctl plane down or the fetch itself failed — quiet fail-soft note
    const why = (f && (f.error || f.detail)) || "unreachable";
    box.innerHTML = `
      <div class="card-head"><span class="card-title">Fast slot optimizer</span>
        <span class="spacer"></span><span class="badge badge--warn" title="daemon ctl plane not responding">offline</span></div>
      <div class="card-body"><div class="empty-note">Fast-optimizer status unavailable (${esc(why)}) — fail-soft: this panel refills automatically once the daemon ctl plane is reachable again. Swaps paused while it is down.</div></div>`;
    return;
  }
  const rep = o.last_report || {};
  const hunt = rep.hunt || {};
  const idle = rep.idle || [];
  const vetoes = rep.vetoes || [];
  const cap = rep.capital || {};
  const cacheAge = f.screen_cache_age_s == null ? null : `${fmtNum(f.screen_cache_age_s, 0)}s`;
  const kv = (k, v, title = "") =>
    `<div class="row"><span class="k"${title ? ` title="${esc(title)}"` : ""}>${esc(k)}</span><span class="v">${v}</span></div>`;

  box.innerHTML = `
    <div class="card-head"><span class="card-title">Fast slot optimizer</span>
      <span class="spacer"></span><span class="mono" style="font-size:10.5px;color:var(--ink-faint)" title="last report ${esc(rep.at || "—")}">report ${esc(relTime(rep.at))}${cacheAge ? ` · screen cache ${esc(cacheAge)}` : ""}</span></div>
    <div class="card-body"><div class="mini-kv">
      ${kv("State", `${o.enabled ? '<span class="badge badge--ok">enabled</span>' : '<span class="badge badge--dim">disabled</span>'} · every ${esc(o.interval_min ?? "—")} min`, "fast capital-reallocation loop (optimizer.py)")}
      ${kv("Cycles", `${esc(o.cycles ?? "—")} · swaps ${esc(o.swaps_total ?? 0)}`, "completed cycles; total slot swaps executed through the guard/churn machinery")}
      ${kv("Last cycle", esc(relTime(o.last_at)))}
      ${kv("Capital", `${fmtUsd(cap.committed_usd)} committed / ${fmtUsd(cap.deployable_ceiling_usd)} ceiling · ${fmtUsd(cap.idle_committed_usd)} idle · ${esc(cap.free_slots ?? "—")} free slot(s)`, "deployable ceiling = free capital available to commit to challengers")}
    </div></div>
    <div class="card-body--tight table-wrap">
      <table class="ledger">
        <thead><tr><th>idle slot</th><th>market</th><th>reasons</th></tr></thead>
        <tbody>
          ${idle.map((s) => `<tr>
            <td class="td-mono">${esc(s.slot ?? "—")}</td>
            <td class="td-mono">${esc(s.venue || "")}:${esc(s.symbol || "?")}</td>
            <td>${(s.reasons || []).map((r) => `<span class="badge badge--warn">${esc(r)}</span>`).join(" ") || "—"}</td>
          </tr>`).join("") || `<tr><td colspan="3"><div class="empty-note">No idle slots in the last report — every slot is pulling its weight.</div></td></tr>`}
        </tbody>
      </table>
    </div>
    <div class="card-body--tight table-wrap">
      <table class="ledger">
        <thead><tr><th>challenger</th><th>regime</th><th>score</th><th>harvest 24h</th></tr></thead>
        <tbody>
          ${(hunt.top3 || []).map((c) => `<tr>
            <td class="td-mono"><span class="venue-tag venue-tag--${esc(c.venue)}">${esc(c.venue)}</span>:${esc(c.symbol)}</td>
            <td><span class="badge badge--dim">${esc(c.regime || "?")}</span></td>
            <td class="td-mono">${esc(fmtNum(c.score_final, 1))}</td>
            <td class="td-mono ${(c.harvest_net_pct_24h || 0) >= 0 ? "m-value--good" : "m-value--bad"}">${c.harvest_net_pct_24h == null ? "—" : `${Number(c.harvest_net_pct_24h) >= 0 ? "+" : ""}${fmtNum(c.harvest_net_pct_24h, 2)}%`}</td>
          </tr>`).join("") || `<tr><td colspan="4"><div class="empty-note">No challenger hunt yet — the first cycle populates the top-3.</div></td></tr>`}
        </tbody>
      </table>
    </div>
    <div class="card-body--tight table-wrap">
      <table class="ledger">
        <thead><tr><th>vetoed slot</th><th>reason</th></tr></thead>
        <tbody>
          ${vetoes.map((v) => `<tr>
            <td class="td-mono">${esc(v.slot ?? "—")}</td>
            <td><div class="rationale" title="${esc(v.reason || "")}">${esc(v.reason || "—")}</div></td>
          </tr>`).join("") || `<tr><td colspan="2"><div class="empty-note">No recent vetoes — nothing blocked by the guard/churn bounds.</div></td></tr>`}
        </tbody>
      </table>
    </div>`;
}

/* ── reliability ──────────────────────────────────────────────────── */

async function loadReliability() {
  let rel;
  try { rel = await api("/api/reliability"); }
  catch (e) { toast(`reliability: ${e.message}`, true); return; }
  const ladder = rel.ladder || {};
  const archs = Object.entries(rel.archetypes || {}).sort((a, b) =>
    (b[1].samples || 0) - (a[1].samples || 0));

  // snapshot-staleness note: the ledger is a file snapshot refreshed by the
  // daemon's 24h health cycle — past that (+grace) it is stale evidence.
  const noteBox = $("#rel-note");
  if (noteBox) {
    const age = rel.ledger_age_h;
    const notes = [];
    if (rel.stale) {
      notes.push(`<div class="banner banner--warn"><div><div class="banner-title">Reliability ledger is a stale snapshot (${fmtNum(age, 1)}h old)</div>
        The 24h refresh cadence has been missed — the daemon may be down or its health cycle has not run. Treat every aggregate below as last-known, not live.</div></div>`);
    } else if (age != null) {
      notes.push(`<div class="banner banner--info"><div>Ledger snapshot age: <b>${fmtNum(age, 1)}h</b> (refresh cadence ${esc(rel.refresh_cadence_h ?? 24)}h).</div></div>`);
    }
    const anySynth = archs.some(([, s]) => (s.synthetic_samples || 0) > 0);
    if (anySynth) {
      notes.push(`<div class="banner banner--bad"><div><div class="banner-title">Synthetic/seeded samples pollute the ledger</div>
        Archetypes below carry seeded or backfilled samples (see the “real / synth” column). Expectancy and profit factor include them — they are not evidence from live round-trips.</div></div>`);
    }
    noteBox.innerHTML = notes.join("");
  }
  $("#rel-body").innerHTML = archs.map(([name, s]) => {
    const full = ladder.full_samples || 30, probe = ladder.probe_samples || 10;
    const pctFull = Math.min(100, ((s.samples || 0) / full) * 100);
    const tierBadge = {
      base: "badge--dim", probe: "badge--violet",
      full: "badge--ok", killed: "badge--bad",
    }[s.tier] || "badge--dim";
    const synth = s.synthetic_samples || 0;
    const real = s.real_samples ?? s.samples ?? 0;
    const synthCell = synth > 0
      ? `<span class="m-value--bad" title="${synth} synthetic/seeded samples pollute the aggregates">${real} / <b>${synth}</b></span>`
      : `${real} / 0`;
    const pfReal = s.profit_factor_real ?? s.profit_factor;
    const recentReal = s.recent_pf_real ?? s.recent_pf;
    const expReal = s.expectancy_usd_real ?? s.expectancy_usd;
    return `<tr>
      <td><b>${esc(name)}</b></td>
      <td class="td-mono">${esc(s.samples ?? 0)}</td>
      <td class="td-mono">${synthCell}</td>
      <td><div class="tier-track" title="${esc(s.samples)} / ${full} samples to full">
        <div class="fill${s.tier === "killed" ? " fill--killed" : ""}" style="width:${pctFull.toFixed(1)}%"></div>
        <div class="mark" style="left:${(probe / full * 100).toFixed(1)}%" title="probe @${probe}"></div>
      </div></td>
      <td class="td-mono ${(pfReal || 0) >= (ladder.pf_pass || 1.3) ? "m-value--good" : ""}"${synth ? ` title="includes ${synth} synthetic samples"` : ""}>${esc(fmtNum(pfReal, 2))}${synth ? "†" : ""}</td>
      <td class="td-mono ${(recentReal || 0) < (ladder.pf_kill || 1.0) ? "m-value--bad" : ""}">${esc(fmtNum(recentReal, 2))}</td>
      <td class="td-mono">${esc(fmtPct(s.win_rate))}</td>
      <td class="td-mono">${fmtUsd(expReal)}${synth ? "†" : ""}</td>
      <td class="td-mono">${fmtUsd(s.max_dd_usd)}</td>
      <td><span class="badge ${tierBadge}">${esc(s.tier)}</span></td>
    </tr>`;
  }).join("") || `<tr><td colspan="10"><div class="empty-note">No closed round-trips yet — the ledger fills as bots complete trades (24h refresh, or force one from Fleet).</div></td></tr>`;
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

/* ── llm providers ────────────────────────────────────────────────── */

const LLM_PROVIDER_LABELS = { cf: "Cloudflare", nvidia: "NVIDIA", openrouter: "OpenRouter", mistral: "Mistral" };
const LLM_MASK = "•"; // never a real key; empty/sentinel means "keep existing"

let llmState = null; // last GET /api/llm payload (providers, chain, roles)

async function loadLlm() {
  let p;
  try { p = await api("/api/llm"); }
  catch (e) { toast(`llm: ${e.message}`, true); return; }
  llmState = p;
  renderLlmLadder(p);
  renderLlmProviders(p);
  renderLlmMatrix(p);
  $("#llm-sidecar-note").textContent = p.sidecar
    ? "sidecar: state/llm.env present"
    : "sidecar: none yet — save to create state/llm.env";
}

function renderLlmLadder(p) {
  const chain = p.chain || [];
  const ladder = $("#llm-ladder");
  ladder.innerHTML = "";
  chain.forEach((name, i) => {
    const prov = p.providers[name] || {};
    const chip = el("span", { class: "llm-chip" },
      el("span", { class: "llm-chip-idx" }, String(i + 1)),
      el("span", { class: "llm-chip-name" }, LLM_PROVIDER_LABELS[name] || name),
      el("span", { class: "llm-chip-key " + (prov.key_present ? "has-key" : "no-key") },
        prov.key_present ? "key" : "no key"),
      el("button", { class: "llm-chip-btn", title: "move up", onclick: () => moveChain(i, -1) }, "↑"),
      el("button", { class: "llm-chip-btn", title: "move down", onclick: () => moveChain(i, 1) }, "↓"));
    ladder.append(chip);
  });
}

function moveChain(i, delta) {
  if (!llmState) return;
  const chain = (llmState.chain || []).slice();
  const j = i + delta;
  if (j < 0 || j >= chain.length) return;
  [chain[i], chain[j]] = [chain[j], chain[i]];
  llmState.chain = chain;
  renderLlmLadder(llmState);
}

function renderLlmProviders(p) {
  const wrap = $("#llm-providers");
  wrap.innerHTML = "";
  for (const name of ["cf", "nvidia", "openrouter", "mistral"]) {
    const prov = p.providers[name] || {};
    const enabled = (p.chain || []).includes(name);
    const modelInput = el("input", { type: "text", class: "llm-model",
      value: prov.model || "", spellcheck: "false", "data-prov": name,
      placeholder: "model id" });
    const keyInput = el("input", { type: "password", class: "llm-key",
      value: prov.key_present ? LLM_MASK : "", "data-prov": name,
      placeholder: prov.key_present ? "key set (leave to keep)" : "paste API key" });
    // typing anything (except the mask sentinel) marks the key as "will set".
    keyInput.addEventListener("input", () => {
      const v = keyInput.value;
      keyInput.dataset.dirty = (v && v !== LLM_MASK) ? "1" : "";
    });
    const validateBtn = el("button", { class: "btn btn--ghost llm-validate-btn",
      onclick: () => validateProvider(name) }, "validate");
    const status = el("span", { class: "mono llm-prov-status", id: `llm-status-${name}` }, "");
    const row = el("div", { class: "llm-prov-row" + (enabled ? "" : " is-off") },
      el("div", { class: "llm-prov-head" },
        el("span", { class: "llm-prov-name" }, LLM_PROVIDER_LABELS[name]),
        el("span", { class: "badge " + (prov.key_present ? "badge--ok" : "badge--warn") },
          prov.key_present ? "key set" : "no key"),
        el("label", { class: "llm-toggle" },
          el("input", { type: "checkbox", "data-enable": name, checked: enabled,
            onchange: (e) => toggleProvider(name, e.target.checked) }),
          el("span", {}, "enabled")),
        el("span", { class: "spacer" }),
        validateBtn, status),
      el("div", { class: "llm-prov-fields" },
        el("label", { class: "llm-field" }, el("span", { class: "llm-field-l" }, "model"),
          modelInput),
        el("label", { class: "llm-field" }, el("span", { class: "llm-field-l" }, "API key"),
          keyInput)));
    wrap.append(row);
  }
}

function toggleProvider(name, on) {
  if (!llmState) return;
  let chain = (llmState.chain || []).slice();
  if (on && !chain.includes(name)) chain.push(name);
  if (!on) chain = chain.filter((x) => x !== name);
  llmState.chain = chain;
  renderLlmLadder(llmState);
  // reflect enabled styling without a full re-render (keeps input values)
  document.querySelectorAll(".llm-prov-row").forEach((row) => {
    const en = row.querySelector(`[data-enable]`);
    if (en && en.dataset.enable === name) row.classList.toggle("is-off", !on);
  });
}

function renderLlmMatrix(p) {
  const roles = p.roles || {};
  const matrix = $("#llm-matrix");
  matrix.innerHTML = "";
  const opts = ["", "cf", "nvidia", "openrouter", "mistral"]; // "" = follow chain
  for (const role of (p.role_keys || [])) {
    const select = el("select", { class: "llm-role-select", "data-role": role });
    for (const o of opts) {
      const opt = el("option", { value: o }, o === "" ? "follow chain" : (LLM_PROVIDER_LABELS[o] || o));
      if ((roles[role] || "") === o) opt.selected = true;
      select.append(opt);
    }
    select.addEventListener("change", () => {
      if (!llmState) return;
      llmState.roles = llmState.roles || {};
      if (select.value) llmState.roles[role] = select.value;
      else delete llmState.roles[role];
    });
    const cell = el("div", { class: "llm-role-cell" },
      el("span", { class: "llm-role-name mono" }, role.replace(/_/g, " ")),
      select);
    matrix.append(cell);
  }
}

async function validateProvider(name) {
  const status = $(`#llm-status-${name}`);
  if (status) status.textContent = "pinging…";
  try {
    const resp = await api("/api/llm/validate", { method: "POST", body: {} });
    const r = (resp.results || []).find((x) => x.provider === name);
    if (!r) { toast(`no result for ${name}`); return; }
    setStatus(name, r.ok, r.latency_ms, r.error);
    // update all rows' statuses we received, and the validate-all note
    for (const res of resp.results || []) {
      if (res.provider !== name) setStatus(res.provider, res.ok, res.latency_ms, res.error);
    }
    const okCount = (resp.results || []).filter((x) => x.ok).length;
    $("#llm-validate-note").textContent =
      `${okCount}/${(resp.results || []).length} ok`;
  } catch (e) {
    if (status) status.textContent = "failed";
    toast(`validate ${name}: ${e.message}`, true);
  }
}

function setStatus(name, ok, latency, err) {
  const elm = $(`#llm-status-${name}`);
  if (!elm) return;
  elm.textContent = ok ? `ok ${latency}ms` : "FAIL";
  elm.className = "mono llm-prov-status " + (ok ? "ok" : "fail");
  elm.title = ok ? "" : (err || "");
}

$("#llm-validate-all").addEventListener("click", async () => {
  const note = $("#llm-validate-note");
  note.textContent = "pinging…";
  for (const name of ["cf", "nvidia", "openrouter", "mistral"]) {
    await validateProvider(name);
  }
});

$("#llm-save").addEventListener("click", async () => {
  if (!llmState) { toast("load LLM config first"); return; }
  const providers = {};
  for (const name of ["cf", "nvidia", "openrouter", "mistral"]) {
    const prov = llmState.providers[name] || {};
    const entry = { model: prov.model };
    const keyInput = document.querySelector(`.llm-key[data-prov="${name}"]`);
    if (keyInput && keyInput.dataset.dirty === "1") entry.key = keyInput.value;
    providers[name] = entry;
  }
  const body = {
    providers,
    chain: llmState.chain,
    roles: llmState.roles || {},
  };
  try {
    const resp = await api("/api/llm", { method: "POST", body });
    toast("LLM config saved — applied at the next LLM call (no restart).");
    await loadLlm();
  } catch (e) {
    toast(`llm save failed: ${e.message}`, true);
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

$("#ctl-optimize").addEventListener("click", async () => {
  const { ok } = await confirmDialog({
    title: "Run slot optimizer",
    body: [el("div", {}, "Runs the fast capital-reallocation cycle now — idle-slot detection, challenger hunt on live 15m candles, Mistral-pinned arbiter. Swaps only pass through the full guard/churn machinery.")],
    label: "Run optimizer",
  });
  if (!ok) return;
  try {
    await api("/api/ctl/optimize", { method: "POST", body: {} });
    toast("Optimizer cycle queued.");
    loadOptimizer();
    loadOverview();
  } catch (e) { toast(`optimize failed: ${e.message}`, true); }
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

/* ── dev maintenance (the single `dev` script, run detached by the backend) ── */

async function devAction(action, body, title, lines, label) {
  const { ok } = await confirmDialog({
    title,
    body: lines.map((t) => el("div", {}, t)),
    label,
    danger: true,
  });
  if (!ok) return;
  try {
    const r = await api(`/api/dev/${action}`, { method: "POST", body: { confirm: true, ...body } });
    toast(`${action} started — output in state/logs/dev.log; console may restart.`, true, 6500);
    setTimeout(() => location.reload(), 6000);
    return r;
  } catch (e) { toast(`${action} failed: ${e.data && e.data.error || e.message}`, true, 6500); }
}

$("#dev-clean").addEventListener("click", () => devAction("clean", {},
  "Clear logs & runtime artifacts",
  ["Run cards, market-map caches, watch specs, daemon/console/PB logs.",
   "Daemon state, decisions and the reliability ledger are kept."],
  "Clean"));

$("#dev-reset-wt").addEventListener("click", () => devAction("reset-wt", {},
  "Reset WunderTrading paper accounts",
  ["Stops the daemon, then stops + deletes EVERY paper grid bot on",
   "WunderTrading (clears plan capacity and positions).",
   "Real-money bots and profiles are never touched; paper profiles are kept.",
   "The daemon must be started again afterwards."],
  "Reset WT paper bots"));

$("#dev-reset").addEventListener("click", devResetDialog);

async function devResetDialog() {
  const { ok, checked } = await confirmDialog({
    title: "Reset the system",
    body: [
      el("div", {}, "Stops the whole stack and wipes daemon runtime state:"),
      el("div", {}, "state.json, decisions journal, reliability ledger + archive, run cards, market caches, watch specs, logs, PocketBase data."),
      el("div", {}, "config.yaml is NOT touched. A backup is kept under state/backups/."),
    ],
    label: "Reset system",
    danger: true,
    checkbox: "Keep the learning journal (decisions + reliability)",
    checkbox2: "Also reset WunderTrading (delete all paper bots)",
  });
  if (!ok) return;
  try {
    await api("/api/dev/reset", {
      method: "POST",
      body: { confirm: true, keep_decisions: !!checked, wt: !!checked2, start: false },
    });
    toast("reset started — the console and daemon are stopping; run `dev start` (or wait) and reload.", true, 8000);
    setTimeout(() => location.reload(), 6000);
  } catch (e) { toast(`reset failed: ${e.data && e.data.error || e.message}`, true, 6500); }
}

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
  if (tick % 6 === 0) loadPnlTimeline(); // PnL history (PB query) — every 30s
  if (activeView === "optimizer" && tick % 4 === 0) loadOptimizer();
  if (activeView === "decisions" && tick % 4 === 0) loadDecisions();
  if (activeView === "logs" && tick % 2 === 0) loadLogs();
}, 5000);

async function boot() {
  const hash = (location.hash || "#fleet").slice(1);
  selectView(VIEWS.includes(hash) ? hash : "fleet");
  loadOverview();
  loadPnlTimeline();
  try {
    const meta = await api("/api/meta");
    $("#footnote").textContent =
      `grid/autonomy console · paper fleet · console :${meta.console_port} · ctl :${meta.ctl_port} · pb ${meta.pocketbase.replace("http://", "")}`;
  } catch { /* footnote stays default */ }
}
boot();

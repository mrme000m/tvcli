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

/* epoch-seconds variant (daemon state uses epoch floats, not ISO) */
function relTimeEpoch(ts) {
  const n = Number(ts);
  if (!isFinite(n) || n <= 0) return "\u2014";
  return relTime(new Date(n * 1000).toISOString());
}
/* Human-readable "how long has this bot been held" — H:MM for under 48h,
   "Nd" above. Mirrors the on-the-hour precision an operator wants when
   deciding whether a bot is stale (24h+) without pulling out a calculator
   for the decimal "24.3h" the old version emitted. */
const heldFor = (iso) => {
  if (!iso) return null;
  const ms = Date.now() - Date.parse(iso);
  if (isNaN(ms) || ms < 0) return null;
  const m = Math.floor(ms / 60000);
  if (m < 1) return "<1m";
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}:${String(m % 60).padStart(2, "0")}`;
  return `${Math.floor(h / 24)}d`;
};

/* Rough "expected close by" for a slot: how many hours until net PnL
   (realized + mark) reaches the profit-exit target, assuming the model's
   projected /24h grid-income rate holds. "Rough" is the point — the exit
   is daemon-owned (take_profit_pct × budget), not a fixed clock, so this
   is a trend estimate, not a promise. Falls back to "—" when any input is
   missing or the projected rate isn't positive (a rate of zero or negative
   means the model can't see income, so no honest ETA exists). */
function estimateCloseBy(bot) {
  const obs = bot.observed || {};
  const net = (isNum(obs.realized_pnl) ? Number(obs.realized_pnl) : 0)
            + (isNum(obs.unrealized_pnl) ? Number(obs.unrealized_pnl) : 0);
  const rate = isNum(bot.projected_24h_usd) ? Number(bot.projected_24h_usd) / 24 : null;
  if (rate == null || rate <= 0) return "—";
  const budget = isNum(bot.committed) ? Number(bot.committed) : null;
  if (budget == null || budget <= 0) return "—";
  const tp = isNum(bot.take_profit_pct) ? Number(bot.take_profit_pct)
    : isNum((lastOverview || {}).config_digest && (lastOverview || {}).config_digest.take_profit_pct)
      ? Number((lastOverview || {}).config_digest.take_profit_pct) : 0.10;
  const target = tp * budget;
  const missing = target - net;
  if (missing <= 0) return "now";
  const hrs = missing / rate;
  if (!isFinite(hrs) || hrs <= 0) return "—";
  if (hrs < 24) return `${Math.round(hrs)}h`;
  return `${Math.round(hrs / 24)}d`;
}

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

/* navigator.clipboard with a one-shot fallback (file:// or older WebViews
   reject it; execCommand("copy") on a temporary textarea still works). */
async function copyText(s) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(s);
      return true;
    }
  } catch (_) { /* fall through */ }
  try {
    const ta = document.createElement("textarea");
    ta.value = s;
    ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.append(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch (_) { return false; }
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
    renderHeartbeatCard(st);
    renderFeed(ov.journal_tail || []);
    renderScreen(ov.screen);
    renderMarketBrief(st);
    renderSummary(ov);
    drawPnlChart(lastPnlPoints || []);
    loadSlotCharts(ov);
    // fire-and-forget — server caches the ping for 60s; awaiting it would
    // block the rest of the overview render for up to 9s on a slow provider.
    renderLlmBrains();
  }
}

function renderStatusbar(ov) {
  const bar = $("#statusbar");
  if (!ov) {
    bar.innerHTML = `<span class="chip chip--bad"><span class="dot"></span>console backend unreachable</span>`;
    return;
  }
  const d = ov.daemon || {};
  const st = lastStatus;
  const livePnl = (st && typeof st.pnl === "object" && st.pnl) || null;
  const chips = [];
  if (d.running) {
    chips.push(`<span class="chip chip--ok"><span class="dot pulse"></span>daemon <b>${esc(d.mode || "?")}</b> · ${esc(d.supervisor)} · pid ${esc(d.pid)}</span>`);
  } else {
    chips.push(`<span class="chip chip--bad"><span class="dot"></span>daemon stopped</span>`);
  }
  if (!st) {
    chips.push(`<span class="chip chip--bad" title="daemon ctl plane not responding (every proxy panel below will fail-soft to 'offline' until this recovers)"><span class="dot"></span><b>ctl down</b></span>`);
  }
  const lastCycle = (ov.ctl && ov.ctl.status && ov.ctl.status.last_cycle) || null;
  chips.push(`<span class="chip"><span class="dot"></span>loop <b>${esc(relTime(lastCycle))}</b></span>`);
  const slots = (ov.slots || []).length || 1;
  // prefer the daemon's own committed-usd from the live ctl /status pnl
  // block (rounded to 2dp) over the per-cycle state.json snapshot — same
  // pattern the statusbar already uses for liveAllow
  const committed = (livePnl && isNum(livePnl.committed_usd))
    ? Number(livePnl.committed_usd) : ov.committed_usd;
  chips.push(`<span class="chip"><span class="dot"></span>fleet <b>${(ov.bots || []).length}/${slots}</b> · ${esc(fmtUsd(committed))} committed</span>`);
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
  // always-on heartbeat score (green ≥90 / amber 70–89 / red <70). The
  // dedicated heartbeat card only shows when a check fails; surfacing the
  // score here means a healthy loop is visible without scrolling.
  const hb = (st && typeof st.heartbeat === "object" && st.heartbeat) || null;
  if (hb && isNum(hb.score)) {
    const s = Number(hb.score);
    const col = s >= 90 ? "var(--teal)" : s >= 70 ? "var(--amber)" : "var(--crimson)";
    const failed = Object.entries(hb.checks || {})
      .filter(([, c]) => c && !c.ok).map(([k]) => k);
    const title = failed.length
      ? `loop-health heartbeat — failed: ${failed.join(", ")}`
      : "loop-health heartbeat — all checks passing";
    chips.push(`<span class="chip" style="border-color:${col};color:${col}" title="${esc(title)}">♥ ${s}</span>`);
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

/* tvcli confluence strip — the screen-time fitness read that decided
   THIS bot's deploy. Empty when the bot isn't in the latest screen cache
   (adopted bots, or bots from an older rescreen). Designed to make the
   slot card answer "what did tvcli say at deploy time?" without leaving
   the Fleet view. */
function tvcliFitHTML(bot) {
  const fit = bot.tvcli_fit;
  if (!fit || typeof fit !== "object") return "";
  const notes = (bot.tvcli_notes || []).slice(0, 3);
  const bonus = isNum(bot.tvcli_bonus) ? Number(bot.tvcli_bonus) : null;
  const ok = isNum(bot.tvcli_ok) ? Number(bot.tvcli_ok) : null;
  const age = isNum(bot.screen_age_min) ? Number(bot.screen_age_min) : null;
  const stale = age != null && age > 120;
  const vah = isNum(fit.vp_vah) ? fmtPrice(fit.vp_vah) : null;
  const val = isNum(fit.vp_val) ? fmtPrice(fit.vp_val) : null;
  const poc = isNum(fit.vp_poc) ? fmtPrice(fit.vp_poc) : null;
  const mtf = isNum(fit.mtf_composite) ? fmtNum(fit.mtf_composite, 1) : null;
  const chop = isNum(fit.chop) ? fmtNum(fit.chop, 1) : null;
  const sqmom = isNum(fit.squeeze_momentum_pct) ? `${fmtNum(fit.squeeze_momentum_pct, 2)}%` : null;
  const sr = fit.sr_last_break && isNum(fit.sr_break_bars_ago)
    ? `${fit.sr_last_break === "bullish" ? "↑" : "↓"} ${fmtNum(fit.sr_break_bars_ago, 0)}b`
    : null;
  const tipParts = [];
  if (vah && val && poc) tipParts.push(`VA ${val}–${vah} (POC ${poc})`);
  if (mtf) tipParts.push(`MTF ${mtf}`);
  if (chop) tipParts.push(`CHOP ${chop}`);
  if (sqmom) tipParts.push(`SqMom ${sqmom}`);
  if (sr) tipParts.push(`S/R ${sr}`);
  const tip = tipParts.join(" · ");
  const chips = notes.map((n) =>
    `<span class="badge badge--violet" title="tvcli confluence note">${esc(n)}</span>`).join(" ");
  const bonusChip = bonus != null
    ? `<span class="badge ${bonus > 0 ? "badge--ok" : bonus < 0 ? "badge--bad" : "badge--dim"}" title="confluence bonus applied to score_final">tvcli ${bonus >= 0 ? "+" : ""}${fmtNum(bonus, 1)}</span>`
    : "";
  const okChip = ok != null
    ? `<span class="badge badge--dim" title="tvcli skills that returned a result">${ok}/6</span>` : "";
  const ageChip = age != null
    ? `<span class="badge ${stale ? "badge--warn" : "badge--dim"}" title="screen-cache age">${fmtNum(age, 0)}m old</span>` : "";
  return `<div class="slot-tvcli" title="${esc(tip)}">
    <div class="slot-tvcli-row">${bonusChip} ${okChip} ${ageChip}</div>
    ${chips ? `<div class="slot-tvcli-notes">${chips}</div>` : ""}
  </div>`;
}

/* position_optimizer last-pass summary — slow lane (15 min + on-entry)
   revalues each active bot. Rec / Δ% / confidence / when / candle hop. */
function positionOptimizerHTML(bot) {
  const po = bot.position_optimizer;
  if (!po || typeof po !== "object") return "";
  const rec = po.last_recommendation;
  const delta = isNum(po.last_delta_pct) ? Number(po.last_delta_pct) : null;
  const conf = isNum(po.last_confidence) ? Number(po.last_confidence) : null;
  const trig = po.last_trigger;
  const at = po.last_analyzed_at;
  const hop = po.last_fetch_hop;
  if (!rec && !at) return "";
  const recBadge = rec === "keep"
    ? `<span class="badge badge--dim">keep</span>`
    : `<span class="badge badge--violet">${esc(rec || "?")}</span>`;
  const deltaCls = delta == null ? "m-value--dim"
    : delta > 0 ? "m-value--good" : delta < 0 ? "m-value--bad" : "m-value--dim";
  const deltaStr = delta == null ? "\u2014"
    : `${delta >= 0 ? "+" : ""}${fmtNum(delta, 2)}%`;
  return `<div class="slot-po" title="position-optimizer slow lane (15m cadence + on-entry pass)">
    <div class="slot-po-row">${recBadge}
      <span class="m-value ${deltaCls}">${deltaStr}</span>
      <span class="mono" style="color:var(--ink-faint)">conf ${conf == null ? "—" : fmtNum(conf, 2)}</span>
      <span class="spacer"></span>
      <span class="mono" style="color:var(--ink-faint);font-size:10.5px">${esc(relTimeEpoch(at))}${trig ? ` · ${esc(trig)}` : ""}${hop ? ` · ${esc(hop)}` : ""}</span>
    </div>
  </div>`;
}

/* optimizer fast-lane idle tracker — when did this slot last see fills?
   empty when the slot was just opened and no health poll has populated
   the tracker yet. */
function optimizerTrackerHTML(bot) {
  const tr = bot.optimizer_tracker;
  if (!tr || typeof tr !== "object") return "";
  const at = tr.last_increase_at;
  const fills = tr.last_fills;
  if (!at && fills == null) return "";
  const idleMin = isNum(at) && at > 0
    ? Math.round((Date.now() / 1000 - Number(at)) / 60) : null;
  const idleCls = idleMin == null ? "m-value--dim"
    : idleMin >= 60 ? "m-value--bad"
    : idleMin >= 15 ? "m-value--warn" : "m-value--dim";
  return `<div class="slot-opt" title="fast-optimizer idle tracker — minutes since this slot's last fill">
    <span class="mono" style="color:var(--ink-faint);font-size:10.5px">opt idle</span>
    <span class="m-value ${idleCls}">${idleMin == null ? "—" : `${idleMin}m`}</span>
    ${isNum(fills) ? `<span class="mono" style="color:var(--ink-faint);font-size:10.5px">fills ${fmtNum(fills, 0)}</span>` : ""}
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
      ${bot.decision_id ? `<span class="slot-decision-link mono" data-did="${esc(bot.decision_id)}" data-copy="${esc(bot.decision_id)}" role="button" tabindex="0" title="View the deliberation evidence the bot was deployed on (alt-click to copy id)">${esc(bot.decision_id)} →</span>` : ""}
    </div>
    <div class="slot-spark" data-key="${esc(bot.venue)}:${esc(bot.symbol)}"
         data-slot="${esc(bot.slot)}" role="button" tabindex="0"
         title="1h price window — click to enlarge">
      <span class="spark-delta mono">Δ —</span>
      <span class="spark-slot"><span class="spark-ph">chart…</span></span>
    </div>
    ${ladderHTML(bot)}
    ${tvcliFitHTML(bot)}
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
      <div class="metric"><div class="m-label" title="average unrealized loss per losing line (negative mark, backstop when per-line mark isn't reported)">worst avg</div>
        <div class="m-value">${(() => {
          const los = isNum(obs.open_losing) ? obs.open_losing : 0;
          const u = isNum(unrl) ? Number(unrl) : null;
          if (!los || u == null || u >= 0) return "—";
          // per-line avg assumes the backstop aggregate `unrealized_pnl`
          // is the only honest number we have when per-line marks are
          // unavailable; this surfaces the magnitude of a held-under-water
          // loss without claiming per-line precision
          return fmtSignedUsd(u / los);
        })()}</div></div>
      <div class="metric"><div class="m-label">dd vs band</div>
        <div class="m-value ${outsideBand ? "m-value--bad" : ""}">${isNum(dd) ? `${fmtNum(dd, 2)}×` : "—"}</div></div>
      <div class="metric"><div class="m-label">budget</div><div class="m-value">${fmtUsd(bot.committed)}</div></div>
      <div class="metric"><div class="m-label" title="model-based expected grid income per 24h, net of round-trip fees">proj /24h</div>
        <div class="m-value ${bot.projected_24h_usd > 0 ? "m-value--good" : "m-value--dim"}">${bot.projected_24h_usd == null ? "\u2014" : fmtUsd(bot.projected_24h_usd)}</div></div>
    </div>
    ${positionOptimizerHTML(bot)}
    ${optimizerTrackerHTML(bot)}
    <div class="slot-foot">
      <span class="slot-since">held ${esc(heldFor(bot.since) ?? "—")}</span>
      <span class="slot-since" title="model-based estimate: hours until net PnL reaches the profit-exit target (take_profit_pct × budget) at the projected grid-income rate — assume income, not a guarantee">expected close ${esc(estimateCloseBy(bot))}</span>
      <span style="margin-left:auto"></span>
      <button class="btn btn--sm btn--danger" data-rotate="${esc(bot.slot)}">Rotate</button>
    </div>`;

  card.querySelector("[data-rotate]").addEventListener("click", () => rotateSlot(bot));
  const spark = card.querySelector(".slot-spark");
  if (spark) spark.addEventListener("click", () =>
    openMarketModal(spark.dataset.key, spark.dataset.slot));
  if (spark) spark.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      openMarketModal(spark.dataset.key, spark.dataset.slot);
    }
  });
  // decision-evidence deep link — opens the Decisions tab with the row
  // pre-expanded so the operator can audit what the swarm evaluated.
  // alt/meta-click copies the id to the clipboard instead (cheaper than
  // switching tabs just to grab the UUID).
  const decLink = card.querySelector(".slot-decision-link");
  if (decLink) {
    const open = () => openDecisionFromSlot(decLink.dataset.did);
    const copy = async (e) => {
      e.preventDefault(); e.stopPropagation();
      const id = decLink.dataset.copy;
      const ok = await copyText(id);
      toast(ok ? `copied ${id}` : "copy failed", !ok);
    };
    decLink.addEventListener("click", (e) => {
      if (e.altKey || e.metaKey || e.ctrlKey) return copy(e);
      open();
    });
    decLink.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
    });
  }
  return card;
}

/* jump to Decisions tab + auto-expand the row for this decision id.
   Lazy-loads the decisions ledger if the operator hasn't opened it yet. */
async function openDecisionFromSlot(did) {
  selectView("decisions");
  // ensure the ledger is populated before we try to scroll/expand
  if (!decisions.length) await loadDecisions();
  const row = document.querySelector(`tr.dec-row[data-id="${CSS.escape(did)}"]`);
  if (!row) {
    toast(`decision ${did} not in the loaded ledger`, true);
    return;
  }
  row.scrollIntoView({ block: "center", behavior: "smooth" });
  // expand if not already
  const next = row.nextElementSibling;
  if (!(next && next.classList.contains("dec-detail"))) {
    const det = document.createElement("tr");
    det.className = "dec-detail";
    det.innerHTML = `<td colspan="12">${decEvidenceHTML(decisions.find((d) => d.id === did))}</td>`;
    row.after(det);
  }
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

/* ── slot market charts (tvcli /api/chart proxy) ───────────────────── */

const chartCache = {};   // "venue:symbol:interval" -> {at (epoch ms), data}
const CHART_TTL_MS = 5 * 60 * 1000;
const chartInflight = {};   // same key -> promise, dedupes parallel polls
let slotChartsBusy = false;

async function fetchChart(venue, symbol, interval = "1h", bars = 96) {
  const key = `${venue}:${symbol}:${interval}`;
  const hit = chartCache[key];
  if (hit && Date.now() - hit.at < CHART_TTL_MS) return hit.data;
  if (chartInflight[key]) return chartInflight[key];
  chartInflight[key] = (async () => {
    try {
      const data = await api(`/api/chart?venue=${encodeURIComponent(venue)}`
        + `&symbol=${encodeURIComponent(symbol)}`
        + `&interval=${encodeURIComponent(interval)}&bars=${bars}`);
      if (!data || data.error || !Array.isArray(data.bars) || !data.bars.length)
        throw new Error((data && data.error) || "no bars");
      chartCache[key] = { at: Date.now(), data };
      return data;
    } finally { delete chartInflight[key]; }
  })();
  return chartInflight[key];
}

/* fetch /api/chart (1h × 96 bars) for each distinct venue:symbol on the
   fleet in parallel; then paint whatever is cached. Fail-soft: a rejected
   fetch keeps the previous sparkline / placeholder untouched. */
async function loadSlotCharts(ov) {
  if (slotChartsBusy) return;
  slotChartsBusy = true;
  try {
    const seen = new Set();
    const jobs = [];
    for (const b of (ov.bots || [])) {
      if (!b || !b.venue || !b.symbol) continue;
      const key = `${b.venue}:${b.symbol}`;
      if (seen.has(key)) continue;
      seen.add(key);
      jobs.push(fetchChart(b.venue, b.symbol));
    }
    await Promise.allSettled(jobs);
    renderSlotSparklines();
  } finally { slotChartsBusy = false; }
}

/* inline sparkline per .slot-spark node: closes min-max scaled (3px pad),
   teal when the window is up, crimson when down, plus dashed channel
   high/low refs when the bot carries a channel. Idempotent per data
   epoch (dataset.at) so re-renders don't thrash. */
function renderSlotSparklines() {
  const bots = (lastOverview && lastOverview.bots) || [];
  for (const node of document.querySelectorAll(".slot-spark")) {
    const key = node.dataset.key || "";
    const hit = chartCache[`${key}:1h`];
    if (!hit || !hit.data) continue;
    const bars = hit.data.bars || [];
    if (bars.length < 2) continue;
    const stamp = String(hit.at);
    if (node.dataset.at === stamp) continue;
    node.dataset.at = stamp;
    const closes = [];
    for (const b of bars) { const c = Number(b && b.c); if (isFinite(c)) closes.push(c); }
    if (closes.length < 2) continue;
    const W = 220, H = 48, P = 3;
    let lo = Math.min(...closes), hi = Math.max(...closes);
    if (hi - lo < 1e-12) { const e = Math.abs(hi) * 0.001 || 0.001; hi += e; lo -= e; }
    const X = (i) => P + (i / (bars.length - 1)) * (W - 2 * P);
    const Y = (v) => P + (1 - (v - lo) / (hi - lo)) * (H - 2 * P);
    let pts = "";
    bars.forEach((b, i) => {
      const c = Number(b && b.c);
      if (isFinite(c)) pts += `${X(i).toFixed(2)},${Y(c).toFixed(2)} `;
    });
    const first = closes[0], last = closes[closes.length - 1];
    const up = last >= first;
    const delta = first ? ((last - first) / first) * 100 : 0;
    let refs = "";
    const bot = bots.find((b) => String(b.slot) === String(node.dataset.slot));
    const ch = (bot && bot.channel) || null;
    if (ch && isNum(ch.high) && isNum(ch.low)) {
      const yH = Math.max(P, Math.min(H - P, Y(Number(ch.high)))).toFixed(1);
      const yL = Math.max(P, Math.min(H - P, Y(Number(ch.low)))).toFixed(1);
      refs = `<line x1="0" y1="${yH}" x2="${W}" y2="${yH}" class="spark-ref"/>`
        + `<line x1="0" y1="${yL}" x2="${W}" y2="${yL}" class="spark-ref"/>`;
    }
    const slot = node.querySelector(".spark-slot");
    if (slot) slot.innerHTML = `
      <svg class="spark-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">
        ${refs}
        <polyline class="spark-line${up ? "" : " spark-line--down"}" points="${pts.trim()}"/>
      </svg>`;
    const d = node.querySelector(".spark-delta");
    if (d) {
      d.className = `spark-delta mono ${up ? "spark-delta--up" : "spark-delta--down"}`;
      d.textContent = `\u0394 ${up ? "+" : "\u2212"}${Math.abs(delta).toFixed(2)}%`;
    }
  }
}

/* the big chart modal: 96×1h closes as a line + light area fill, dashed
   channel high/mid/low with right-edge labels, lo/hi/last captions and
   the bar window. Reuses #modal-root; Escape/backdrop/Close all clean
   the key listener up (confirmDialog's onKey pattern).

   Cold-cache: a slot card with a not-yet-fetched sparkline still has a
   clickable .slot-spark. The old version rendered the modal with
   "no chart data cached … yet" while the fetch was in flight in the
   background, then left the operator staring at it. We now show a
   spinner, wait for the in-flight fetch (with a 12s timeout — tvcli is
   normally <3s for a cached Binance candle window), and re-render once
   data lands. */
function openMarketModal(key, slot) {
  const parts = String(key).split(":");
  const venue = parts[0] || "?", symbol = parts.slice(1).join(":") || "?";
  const root = $("#modal-root");
  const box = el("div", { class: "modal-backdrop" });

  const W = Math.max(320, Math.min(window.innerWidth * 0.9, 900)), H = 360;
  const RX = 62;   // right gutter for channel labels

  const modal = el("div", { class: "modal modal--chart", role: "dialog", "aria-modal": "true" },
    el("h3", {}, `MARKET — ${esc(venue)}:${esc(symbol)}`),
    el("div", { class: "modal-body" },
      el("div", { class: "mk-chart" })),
    el("div", { class: "modal-actions" },
      el("button", { class: "btn", onclick: () => done() }, "Close")));
  const chartHost = modal.querySelector(".mk-chart");

  function paint(bars) {
    const bots = (lastOverview && lastOverview.bots) || [];
    const bot = bots.find((b) => String(b.slot) === String(slot)) || null;
    const ch = (bot && bot.channel) || null;
    let chartHTML = `<div class="mk-empty">no chart data cached for ${esc(venue)}:${esc(symbol)} yet</div>`;
    let captions = "";
    if (bars.length >= 2) {
      const closes = [];
      for (const b of bars) { const c = Number(b && b.c); if (isFinite(c)) closes.push(c); }
      if (closes.length >= 2) {
        let lo = Math.min(...closes), hi = Math.max(...closes);
        if (isNum(ch && ch.low)) lo = Math.min(lo, Number(ch.low));
        if (isNum(ch && ch.high)) hi = Math.max(hi, Number(ch.high));
        if (hi - lo < 1e-12) { const e = Math.abs(hi) * 0.001 || 0.001; hi += e; lo -= e; }
        const padY = (hi - lo) * 0.08;
        lo -= padY; hi += padY;
        const T = 10, B = 10;
        const X = (i) => 8 + (i / (bars.length - 1)) * (W - RX - 14);
        const Y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
        let pts = "", area = `M ${X(0).toFixed(1)},${(H - B).toFixed(1)}`;
        bars.forEach((b, i) => {
          const c = Number(b && b.c);
          if (!isFinite(c)) return;
          pts += `${X(i).toFixed(1)},${Y(c).toFixed(1)} `;
          area += ` L ${X(i).toFixed(1)},${Y(c).toFixed(1)}`;
        });
        area += ` L ${X(bars.length - 1).toFixed(1)},${(H - B).toFixed(1)} Z`;
        const first = closes[0], last = closes[closes.length - 1];
        const up = last >= first;
        let refs = "";
        const chLine = (v, label) => {
          if (!isNum(v)) return "";
          const y = Y(Number(v)).toFixed(1);
          const lbl = label ? `${esc(label)} ${fmtPrice(v)}` : fmtPrice(v);
          return `<line x1="8" y1="${y}" x2="${W - RX}" y2="${y}" class="mk-ch"/>`
            + `<text x="${W - RX + 6}" y="${Number(y) + 3}" class="mk-label">${lbl}</text>`;
        };
        if (ch) refs = chLine(ch.high, "hi") + chLine(ch.mid, "mid") + chLine(ch.low, "lo");
        chartHTML = `
          <svg class="mk-svg" viewBox="0 0 ${W.toFixed(0)} ${H}" role="img"
               aria-label="1h closes for ${esc(venue)}:${esc(symbol)}">
            <path class="mk-area${up ? "" : " mk-area--down"}" d="${area}"/>
            ${refs}
            <polyline class="mk-line${up ? "" : " mk-line--down"}" points="${pts.trim()}"/>
          </svg>`;
        const t0 = Number(bars[0] && bars[0].t), tN = Number(bars[bars.length - 1] && bars[bars.length - 1].t);
        captions = `
          <div class="mk-captions mono">
            <span>lo <b>${fmtPrice(Math.min(...closes))}</b></span>
            <span>hi <b>${fmtPrice(Math.max(...closes))}</b></span>
            <span>last <b>${fmtPrice(last)}</b></span>
            <span class="mk-delta ${up ? "mk-delta--up" : "mk-delta--down"}">
              \u0394 ${up ? "+" : "\u2212"}${Math.abs(first ? ((last - first) / first) * 100 : 0).toFixed(2)}%</span>
            <span class="mk-window">${bars.length} \u00d7 1h bars \u00b7 ${relTimeEpoch(t0)} \u2192 ${relTimeEpoch(tN)}</span>
          </div>`;
      }
    }
    chartHost.innerHTML = chartHTML + captions;
  }

  // First paint from the cache; if cold, request a fetch + show a spinner.
  const cached = chartCache[`${key}:1h`];
  const cachedBars = (cached && cached.data && cached.data.bars) || [];
  if (cachedBars.length >= 2) {
    paint(cachedBars);
  } else {
    chartHost.innerHTML = `<div class="mk-empty"><span class="spinner"></span> fetching ${esc(venue)}:${esc(symbol)} 1h bars…</div>`;
    // Fire (or wait on an in-flight) fetch with a bounded timeout — never
    // strand the operator on a spinner if tvcli is down.
    let timer;
    const timeout = new Promise((_, rej) => { timer = setTimeout(() => rej(new Error("timeout")), 12000); });
    const work = fetchChart(venue, symbol, "1h", 96)
      .then((d) => (d && d.bars) || [])
      .catch(() => null);
    Promise.race([work, timeout]).then((bars) => {
      clearTimeout(timer);
      // If the user closed the modal in the meantime, chartHost is no
      // longer in the DOM — skip the second paint.
      if (!chartHost.isConnected) return;
      paint(bars && bars.length >= 2 ? bars : []);
    });
  }

  function done() {
    root.innerHTML = "";
    document.removeEventListener("keydown", onKey);
  }
  function onKey(e) { if (e.key === "Escape") done(); }
  document.addEventListener("keydown", onKey);
  box.append(modal);
  box.addEventListener("mousedown", (e) => { if (e.target === box) done(); });
  root.append(box);
  modal.querySelector(".modal-actions .btn").focus();
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
  const livePnl = (st && st.pnl && typeof st.pnl.bots === "object" && st.pnl.bots) || {};
  const bots = (ov.bots || []).map((b) => {
    const lo = liveObs[String(b.slot)];
    const out = (lo && lo.observed && Object.keys(lo.observed).length)
      ? { ...b, observed: { ...b.observed, ...lo.observed } } : { ...b };
    // per-bot projected /24h income from the daemon's pnl snapshot
    const pb = livePnl[String(b.slot)];
    if (pb && isNum(pb.projected_24h_usd))
      out.projected_24h_usd = Number(pb.projected_24h_usd);
    return out;
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

  // Populate the controls-panel "force rotate one slot" select whenever
  // the fleet is non-empty. Hidden otherwise — pointless to expose
  // rotation when there are no active bots to rotate.
  const rotRow = $("#ctl-rotate-row");
  const rotSel = $("#ctl-rotate-slot");
  if (rotRow && rotSel) {
    const bots = (ov.bots || []).filter((b) => b && b.slot != null);
    if (!bots.length) {
      rotRow.hidden = true;
    } else {
      rotRow.hidden = false;
      const cur = rotSel.value;
      rotSel.innerHTML = bots.map((b) => {
        const lbl = `slot ${b.slot} — ${esc(b.venue || "")}:${esc(b.symbol || "?")} (${esc(b.grid_type || "—")})`;
        return `<option value="${esc(b.slot)}">${lbl}</option>`;
      }).join("");
      if (cur && bots.some((b) => String(b.slot) === cur)) rotSel.value = cur;
    }
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

/* Wire the global "Rotate slot" control once — the button is a fixed
   element in the controls panel, so we attach a single delegated
   handler at boot. The dropdown is repopulated by renderFleet(). */
async function ctlRotateFromPanel() {
  const sel = $("#ctl-rotate-slot");
  if (!sel || !sel.value) { toast("no slot selected", true); return; }
  const slot = Number(sel.value);
  const bot = (lastOverview && lastOverview.bots || []).find((b) => Number(b.slot) === slot);
  if (!bot) { toast(`slot ${slot} not active`, true); return; }
  const { ok } = await confirmDialog({
    title: `Rotate slot ${slot}`,
    body: [el("div", {}, `Stop, close and delete `,
      el("code", {}, `${bot.venue || ""}:${bot.symbol || ""}`),
      `, then deploy the best challenger on the next rescreen. Per-token cooldown applies.`)],
    label: "Queue rotation", danger: true,
  });
  if (!ok) return;
  try {
    await api("/api/ctl/rotate", { method: "POST", body: { slot } });
    toast(`rotation queued for slot ${slot} — applied on next rescreen.`);
    loadOverview();
  } catch (e) { toast(`rotate failed: ${e.data && e.data.error || e.message}`, true); }
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
  // the latest rescreen's run-card stem (if reports/ is populated). The
  // rail cards are buttons that jump to the Run cards tab and open it.
  const stem = screen.run_card_stem || null;
  // score history sparkline: small line of the last N cycles' top score,
  // oldest → newest. Renders inside the rail head as a chip-sized band so
  // a glance answers "is screening improving or degrading?" without
  // leaving the Fleet view.
  const hist = Array.isArray(screen.score_history) ? screen.score_history : [];
  const histSvg = hist.length >= 2 ? scoreSparklineSVG(hist) : "";
  const itemsHtml = (screen.top || []).slice(0, 5).map((c, i) => {
    const bonus = c.confluence_bonus;
    const okSkills = c.confluence_ok;
    const tvcliChip = (bonus != null || okSkills != null)
      ? `<span class="badge ${(bonus || 0) > 0 ? "badge--ok" : "badge--dim"}" title="${okSkills != null ? `${okSkills}/6 tvcli skills returned a result` : "tvcli confluence"} · +${fmtNum(bonus ?? 0, 1)} to score">tvcli +${fmtNum(bonus ?? 0, 1)}</span>`
      : "";
    const click = stem ? `data-stem="${esc(stem)}" data-rank="${i + 1}" role="button" tabindex="0"` : "";
    const cursor = stem ? "cursor:pointer" : "";
    return `
    <div class="candidate" ${click} style="${cursor}" title="${stem ? `open latest rescreen run card (rank #${i + 1})` : "no run card available"}">
      <span class="rank">${String(i + 1).padStart(2, "0")}</span>
      <span class="venue-tag venue-tag--${esc(c.venue)}">${esc(c.venue)}</span>
      <span class="sym">${esc(c.symbol)}</span>
      <span class="badge badge--dim">${esc(c.regime || "?")}</span>
      ${tvcliChip}
      <span class="score">${esc(fmtNum(c.score_final, 1))}</span>
    </div>`;
  }).join("");
  box.innerHTML =
    (histSvg ? `<div class="screen-trend" title="top-of-screen score over the last ${hist.length} rescreen cycles">${histSvg}<span class="mono" style="color:var(--ink-faint);font-size:10.5px">${fmtNum(hist[0].score, 1)} → ${fmtNum(hist[hist.length - 1].score, 1)}</span></div>` : "") +
    itemsHtml;
  if (stem) {
    for (const node of box.querySelectorAll(".candidate[data-stem]")) {
      const open = () => jumpToRunCard(node.dataset.stem);
      node.addEventListener("click", open);
      node.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
      });
    }
  }
}

/* Jump to the Run cards tab and open one stem. The tab's normal load
   path is fire-and-forget; we await it so the run card is rendered
   before we tell the page to scroll. */
async function jumpToRunCard(stem) {
  selectView("reports");
  if (typeof openRunCard === "function") {
    try { await openRunCard(stem); } catch (_) { /* toast already shown */ }
  }
}

/* Tiny inline SVG sparkline for the "last screen" rail — top-of-screen
   score over the last N rescreen cycles. Green when trending up, crimson
   when trending down (last vs first), gray when flat. */
function scoreSparklineSVG(history) {
  if (!Array.isArray(history) || history.length < 2) return "";
  const W = 110, H = 18, P = 2;
  const scores = history.map((h) => Number(h.score)).filter(isFinite);
  if (scores.length < 2) return "";
  const lo = Math.min(...scores), hi = Math.max(...scores);
  const span = Math.max(1e-6, hi - lo);
  const X = (i) => P + (i / (scores.length - 1)) * (W - 2 * P);
  const Y = (v) => P + (1 - (v - lo) / span) * (H - 2 * P);
  const pts = scores.map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(" ");
  const first = scores[0], last = scores[scores.length - 1];
  const up = last > first, flat = last === first;
  const stroke = flat ? "var(--ink-faint)" : up ? "var(--teal)" : "var(--crimson)";
  return `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" aria-hidden="true" class="screen-spark">
    <polyline fill="none" stroke="${stroke}" stroke-width="1.2" points="${pts}"/>
  </svg>`;
}

/* the LLM intelligence lane: the daemon's periodic market brief over the
   fresh screen + live fleet evidence (Mistral-first chain). Advisory
   only — it never gates or deploys; this is the "what does the model
   think right now" companion to the numeric boards. */
function renderMarketBrief(st) {
  const box = $("#market-brief");
  if (!box) return;
  const prov = $("#mb-prov"), provName = $("#mb-prov-name");
  const mb = (st && typeof st.market_brief === "object" && st.market_brief) || null;
  if (!mb) {
    if (st === null) {
      box.innerHTML = `<div class="empty-note">Daemon ctl plane down — the last brief is unavailable (fail-soft).</div>`;
    } else {
      box.innerHTML = `<div class="empty-note">No brief yet — one lands per rescreen (llm.brief_interval_min cadence).</div>`;
    }
    if (prov) prov.hidden = true;
    return;
  }
  const bias = String(mb.bias || "mixed");
  const biasBadge = bias === "risk-on"
    ? '<span class="badge badge--ok">risk-on</span>'
    : bias === "risk-off"
      ? '<span class="badge badge--bad">risk-off</span>'
      : '<span class="badge badge--dim">mixed</span>';
  const watch = (mb.watch || []).map((w) =>
    `<li><span class="f-kind">watch</span><span class="f-msg">${esc(w)}</span></li>`).join("");
  const risks = (mb.risks || []).map((w) =>
    `<li><span class="f-kind k--health-warn">risk</span><span class="f-msg">${esc(w)}</span></li>`).join("");
  box.innerHTML = `
    <div class="mb-top">${biasBadge}
      <span class="mb-summary">${esc(mb.summary || "—")}</span></div>
    ${(watch || risks) ? `<ul class="feed" style="margin-top:8px">${watch}${risks}</ul>` : ""}
    <div class="mono" style="font-size:10.5px;color:var(--ink-faint);margin-top:6px" title="advisory only — never gates, deploys or edits">${esc(relTime(mb.at))} · advisory</div>`;
  if (prov && provName) {
    provName.textContent = String(mb.provider || "llm");
    prov.hidden = false;
  }
}

/* "LLM brains" — small panel that maps the operator's headline question
   ("is Mistral actually driving the fast lane right now?") onto one
   block. Live ping + role routing, updated on every overview poll but
   lazy-loaded so the first paint isn't blocked on a 9s subprocess. */
async function renderLlmBrains() {
  const box = $("#llm-brains");
  const atEl = $("#llm-brains-at");
  if (!box) return;
  let d;
  try { d = await api("/api/llm/health"); }
  catch (e) { box.innerHTML = `<div class="empty-note">Provider health unreachable.</div>`; return; }
  const results = d.results || [];
  const roles = d.roles || {};
  const arbProv = d.arbiter_provider || "mistral";
  if (atEl && d.at) atEl.textContent = `pinged ${relTime(d.at)}`;
  const dot = (ok) => ok ? "●" : "○";
  const cls = (ok) => ok ? "ready-cell--on" : "ready-cell--off";
  const provRow = results.map((r) => {
    const name = r.provider;
    const labelMap = { cf: "CF Workers AI", nvidia: "NVIDIA", openrouter: "OpenRouter", mistral: "Mistral" };
    const lbl = labelMap[name] || name;
    const err = r.ok ? "" : (r.error || "FAIL");
    return `<div class="ready-cell ${cls(r.ok)}" title="${esc(err || 'ok')}">
      <span class="ready-dot">${dot(r.ok)}</span>
      <span class="ready-key">${esc(lbl)}</span>
      <span class="ready-val">${r.ok ? `${r.latency_ms}ms` : "down"}</span>
    </div>`;
  }).join("");
  // Role → provider pin matrix (which agent uses which model). The arbiter
  // (fast-lane capital reallocation) is NOT in the swarm role list — it is
  // driven by `config.optimizer.llm_provider` and surfaced separately as
  // `arbiter_provider` so the operator can answer "is Mistral actually
  // doing the fast lane right now?" at a glance.
  const swarmRoles = d.role_keys || [];
  const arbPinned = roles.optimizer;
  const arbUsing = arbPinned || arbProv || "mistral";
  const roleRow = (label, roleKey) => {
    const using = roles[roleKey] || "follow chain";
    return `<div class="row"><span class="k" title="Pinned provider for ${roleKey}. Default follows the chain.">${esc(label)}</span>
      <span class="v">${using === "follow chain"
        ? `<span class="badge badge--dim">follow chain</span>`
        : `<span class="badge badge--violet">${esc(using)}</span>`}</span></div>`;
  };
  const arbRow = `<div class="row"><span class="k" title="Pinned provider for the fast-lane arbiter. Default = ${esc(arbProv || 'mistral')}. Override: config.optimizer.llm_provider.">arbiter (fast lane)</span>
    <span class="v"><span class="badge badge--violet">${esc(arbUsing)}</span>${arbPinned ? "" : ` <span class="mono" style="color:var(--ink-faint);font-size:10.5px">default</span>`}</span></div>`;
  const swarmRows = swarmRoles.map((r) => roleRow(r.replace(/_/g, " "), r)).join("");
  box.innerHTML = `
    <div class="readiness-cells" style="margin-bottom:8px">${provRow}</div>
    <div class="mini-kv">${arbRow}${swarmRows}</div>`;
}

function renderSummary(ov) {
  const d = ov.daemon || {};
  const cd = ov.config_digest || {};
  const r = ov.readiness || {};
  const c = r.capacity || {};
  const oth = c.other || {}, pre = c.premium || {};
  const lim = r.account_limits || {};
  const dashGrid = lim.gridBots || {};
  const real = r.real_profiles || [];
  const realLine = real.length
    ? `<div class="row"><span class="k" style="color:var(--crimson)">Real-money profiles</span>
        <span class="v" style="color:var(--crimson)" title="real-money profile(s) loaded — daemon must never route a paper decision here">${real.length} <b>${real.map((p) => esc(p.name || p.code || "?"))}</b></span></div>`
    : `<div class="row"><span class="k">Real-money profiles</span><span class="v" title="no real-money profile loaded — safe to operate">0 (paper only)</span></div>`;
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
    ${realLine}
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
    committed: null, idle: null, total: null, projected: null };
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
  // projected /24h has no per-bot fallback derivation (it needs the
  // stagnation-policy model inputs) — present only when the daemon reports it
  out.projected = p && isNum(p.projected_24h_usd) ? p.projected_24h_usd : null;
  const committedMap = (st && st.committed) || {};
  out.committed = p && isNum(p.committed_usd) ? p.committed_usd
    : Object.values(committedMap).reduce((a, v) => a + (isNum(v) ? Number(v) : 0), 0) || null;
  out.total = (ov.config_digest || {}).total_usd;
  if (out.total != null && isNum(out.total)) out.total = Number(out.total);
  out.idle = p && isNum(p.idle_usd) ? p.idle_usd
    : (out.total != null && out.committed != null) ? Number(out.total) - out.committed : null;
  return out;
}

/* max age (ms) we'll trust a journal-derived demo-cap read for. Veto
   events older than this are ignored so the meter doesn't keep saying
   "5/5 — deploys blocked" ten minutes after the daemon unstuck. */
const DEMO_CAP_VETO_TTL_MS = 10 * 60 * 1000;

function demoCapData(st, ov) {
  /* daemon demo_cap block, else parsed from a RECENT demo-cap-veto journal
     message ("cap 5/5"), else counted actives with unknown cap. The veto
     source is tagged "veto" + a `veto_age_min` so the UI can label the
     meter "live" vs "veto (Nm ago)" instead of mis-rendering a stale
     veto as the current cap. */
  const d = (st && typeof st.demo_cap === "object" && st.demo_cap) || null;
  if (d && isNum(d.active)) {
    return { active: Number(d.active),
      cap: isNum(d.cap) ? Number(d.cap) : null,
      headroom: isNum(d.headroom) ? Number(d.headroom) : null,
      source: "live" };
  }
  const active = Object.keys((st && st.active_bots) || {}).length;
  const tail = (st && st.journal_tail) || (ov && ov.journal_tail) || [];
  for (let i = tail.length - 1; i >= 0; i--) {
    const e = tail[i] || {};
    if (e.kind === "demo-cap-veto") {
      const m = /(\d+)\s*\/\s*(\d+)/.exec(String(e.msg || ""));
      if (!m) continue;
      const at = e.at ? Date.parse(e.at) : 0;
      if (!at || (Date.now() - at) > DEMO_CAP_VETO_TTL_MS) continue;
      return { active: Number(m[1]), cap: Number(m[2]),
        headroom: Number(m[2]) - Number(m[1]), vetoed: true,
        source: "veto",
        veto_age_min: Math.round((Date.now() - at) / 60000) };
    }
  }
  return { active, cap: null, headroom: null, vetoed: false, source: "count" };
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

  // projected /24h: model-based expected grid income (net of round-trip
  // fees) from the daemon's /status pnl block — the instantaneous
  // performance measure beside the realized mark
  const proj = isNum(p.projected) ? Number(p.projected) : null;
  const projCell = `<div class="pnl-cell" title="model-based expected grid income per 24h, net of round-trip fees">
      <div class="m-label">proj /24h</div>
      <div class="m-value ${proj != null && proj > 0 ? "m-value--good" : "m-value--dim"}">${proj == null ? "\u2014" : `\u2248 ${fmtUsd(proj)}`}</div>
      <div class="pnl-sub pnl-sub--faint">expected grid income (model)</div></div>`;

  // demo-cap meter: 5/5 means every new deploy is vetoed at the platform cap
  let capCell;
  const sourceTag = cap.source === "veto"
    ? `<span class="mono" style="color:var(--ink-faint);font-size:10.5px"> · veto ${cap.veto_age_min}m ago</span>`
    : cap.source === "count"
      ? `<span class="mono" style="color:var(--ink-faint);font-size:10.5px"> · count only</span>`
      : "";
  if (cap.cap != null && cap.cap > 0) {
    const full = cap.active >= cap.cap;
    const w = Math.min(100, (cap.active / cap.cap) * 100);
    capCell = `<div class="pnl-cell cap-meter${full ? " cap-meter--full" : ""}" title="paper/demo grid-bot platform cap${cap.headroom != null ? ` · headroom ${cap.headroom}` : ""}${cap.source === "veto" ? " · last demo-cap-veto in journal tail" : ""}">
      <div class="m-label">demo cap</div>
      <div class="cap-bar"><div class="cap-fill" style="width:${w.toFixed(1)}%"></div></div>
      <div class="cap-label">${full ? `<b>demo bots ${cap.active}/${cap.cap} — deploys blocked</b>` : `demo bots ${cap.active}/${cap.cap}`}${sourceTag}</div>
    </div>`;
  } else {
    capCell = `<div class="pnl-cell" title="cap not reported by this daemon build">
      <div class="m-label">demo cap</div>
      <div class="m-value m-value--dim">${cap.active} paper bots · cap unknown</div>
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
        ${projCell}
        ${capCell}
      </div>
      <div class="pnl-chart">
        <div class="m-label">net \u00b7 realized \u2014 <span id="pnl-chart-meta">no history yet</span></div>
        <canvas id="pnl-canvas" width="360" height="96" role="img" aria-label="fleet PnL timeline"></canvas>
      </div>
    </div>`;
  drawPnlChart(lastPnlPoints || []);
}

/* heartbeat card — rendered ONLY when a check fails (a healthy heartbeat
   is already visible as the ♥ chip in the veto strip). Lists the failed
   checks with details + the last improving nudges. */
function renderHeartbeatCard(st) {
  const wrap = $("#heartbeat-wrap");
  if (!wrap) return;
  const hb = (st && typeof st.heartbeat === "object" && st.heartbeat) || null;
  const failed = (hb && Object.entries(hb.checks || {})
    .filter(([, c]) => c && !c.ok)) || [];
  if (!hb || !failed.length) { wrap.hidden = true; wrap.innerHTML = ""; return; }
  const score = isNum(hb.score) ? Number(hb.score) : null;
  const nudges = (hb.nudges || []).slice(-3);
  wrap.innerHTML = `
    <div class="card" style="border-color:#E8C2BE">
      <div class="card-head"><span class="card-title">Heartbeat \u2014 ${score == null ? "degraded" : `${score}/100`}</span>
        <span class="spacer"></span><span class="mono" style="font-size:10.5px;color:var(--ink-faint)" title="last heartbeat">${esc(relTime(hb.at))}</span></div>
      <div class="card-body"><div class="mini-kv">
        ${failed.map(([name, c]) => `
        <div class="row"><span class="k"><span class="badge badge--bad">${esc(name)}</span></span>
          <span class="v" title="${esc(c.detail || "")}">${esc(c.detail || "\u2014")}</span></div>`).join("")}
        ${nudges.length ? `
        <div class="row"><span class="k">nudges</span>
          <span class="v" title="improving actions taken by the heartbeat cycle">${nudges.map((n) => esc(n)).join(" \u00b7 ")}</span></div>` : ""}
      </div></div>
    </div>`;
  wrap.hidden = false;
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
  // heartbeat: daemon loop-health score (state.heartbeat, ctl /status) —
  // green ≥90, amber 70–89, red <70; the title lists failed checks
  const hb = (st && typeof st.heartbeat === "object" && st.heartbeat) || null;
  if (hb && isNum(hb.score)) {
    const s = Number(hb.score);
    const col = s >= 90 ? "var(--teal)" : s >= 70 ? "var(--amber)" : "var(--crimson)";
    const failed = Object.entries(hb.checks || {})
      .filter(([, c]) => c && !c.ok).map(([k]) => k);
    chips.push(`<span class="veto-chip" style="border-color:${col};color:${col}" title="loop-health heartbeat (8 fail-soft checks)${failed.length ? ` \u00b7 failed: ${esc(failed.join(", "))}` : " \u00b7 all checks passing"}">\u2665 ${s} \u00b7 ${esc(relTime(hb.at))}</span>`);
  }
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
let decSort = { key: "at", dir: -1 };   // default: newest first

async function loadDecisions() {
  try {
    decisions = (await api("/api/decisions?limit=400")).decisions || [];
  } catch (e) { toast(`decisions: ${e.message}`, true); return; }
  renderDecisions();
}

function _decValue(r, key) {
  if (key === "realized") {
    const o = r.outcome;
    return (o && isNum(o.realized_pnl)) ? Number(o.realized_pnl) : null;
  }
  if (key === "conf") return (r.evidence && isNum(r.evidence.confidence))
    ? Number(r.evidence.confidence) : null;
  const v = r[key];
  return isNum(v) ? Number(v) : (v == null ? null : String(v).toLowerCase());
}

function _decCompare(a, b, key, dir) {
  const va = _decValue(a, key);
  const vb = _decValue(b, key);
  // nulls always last, regardless of direction
  if (va == null && vb == null) return 0;
  if (va == null) return 1;
  if (vb == null) return -1;
  if (va < vb) return -1 * dir;
  if (va > vb) return 1 * dir;
  return 0;
}

function renderDecisions() {
  const q = ($("#dec-filter").value || "").toLowerCase();
  const state = $("#dec-state").value;
  let rows = decisions.filter((r) => {
    if (state === "open" && r.outcome) return false;
    if (state === "closed" && !r.outcome) return false;
    if (!q) return true;
    return [r.symbol, r.venue, r.regime, r.grid_type, r.decision, r.id]
      .some((v) => String(v || "").toLowerCase().includes(q));
  });
  rows = rows.slice().sort((a, b) => _decCompare(a, b, decSort.key, decSort.dir));
  $("#dec-count").textContent = `${rows.length} shown · ${decisions.length} total`;
  const arrow = (k) => decSort.key === k
    ? `<span style="margin-left:4px;color:var(--teal)">${decSort.dir < 0 ? "▾" : "▴"}</span>` : "";
  const sortable = (k, label) => `<th class="dec-sort" data-key="${k}" role="button" tabindex="0">${label}${arrow(k)}</th>`;
  const thead = document.querySelector("#view-decisions thead");
  if (thead) {
    thead.innerHTML = `<tr>
      ${sortable("id", "id")}${sortable("at", "at")}${sortable("symbol", "market")}
      ${sortable("regime", "regime")}${sortable("grid_type", "grid")}
      ${sortable("decision", "call")}${sortable("score_final", "score")}
      ${sortable("step_pct", "step")}${sortable("slot", "slot")}${sortable("state", "state")}
      ${sortable("realized", "realized")}<th>rationale</th></tr>`;
    for (const th of thead.querySelectorAll(".dec-sort")) {
      const k = th.dataset.key;
      const onSort = () => {
        if (decSort.key === k) decSort.dir = -decSort.dir;
        else { decSort.key = k; decSort.dir = (k === "at" || k === "id") ? -1 : 1; }
        renderDecisions();
      };
      th.addEventListener("click", onSort);
      th.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSort(); }
      });
    }
  }
  $("#dec-body").innerHTML = rows.map((r) => {
    const outcome = r.outcome;
    const stateBadge = outcome
      ? `<span class="badge ${outcome.realized_pnl >= 0 ? "badge--ok" : "badge--bad"}" title="${esc(outcome.reason || "")}">closed ${fmtUsd(outcome.realized_pnl)}</span>`
      : `<span class="badge badge--dim">open</span>`;
    const go = String(r.decision || "").toUpperCase().includes("GO");
    const noGo = String(r.decision || "").toUpperCase().includes("NO_GO");
    const conf = (r.evidence || {}).confidence;
    const confChip = conf != null
      ? `<span class="badge badge--dim" title="facilitator confidence">c ${fmtNum(conf, 2)}</span>` : "";
    return `<tr class="dec-row${noGo ? " dec-row--nogo" : ""}" data-id="${esc(r.id)}" title="click to expand the evidence the agents evaluated against — alt-click on id to copy">
      <td class="td-mono"><span class="dec-id mono" data-copy="${esc(r.id)}" role="button" tabindex="0" title="alt-click to copy">${esc(r.id)}</span></td>
      <td class="td-mono">${esc(String(r.at || "").replace("T", " ").slice(5, 16))}</td>
      <td class="td-mono"><span class="venue-tag venue-tag--${esc(r.venue)}">${esc(r.venue)}</span>:${esc(r.symbol)}</td>
      <td><div class="regime-cell"><span>${esc(r.regime || "—")}</span>${r.llm_degraded
        ? '<span class="badge badge--warn" title="LLM chain unavailable; rule fallback">degraded</span>' : ""}</div></td>
      <td class="td-mono">${esc(r.grid_type || "—")}</td>
      <td><span class="badge ${go ? "badge--ok" : noGo ? "badge--bad" : "badge--dim"}">${esc(r.decision || "?")}</span>${confChip ? ` ${confChip}` : ""}</td>
      <td class="td-mono">${esc(fmtNum(r.score_final, 1))}</td>
      <td class="td-mono">${esc(fmtNum(r.step_pct, 3))}%</td>
      <td class="td-mono">${esc(r.slot ?? "—")}</td>
      <td>${stateBadge}</td>
      <td class="td-mono ${outcome && isNum(outcome.realized_pnl) ? (outcome.realized_pnl >= 0 ? "m-value--good" : "m-value--bad") : "m-value--dim"}">${outcome && isNum(outcome.realized_pnl) ? fmtSignedUsd(outcome.realized_pnl) : "—"}</td>
      <td><div class="rationale" title="${esc(r.rationale || "")}">${esc(r.rationale || "—")}</div></td>
    </tr>`;
  }).join("") || `<tr><td colspan="12"><div class="empty-note">No decisions match. The ledger fills as the daemon deliberates.</div></td></tr>`;
}

/* CSV export of the currently-filtered (and currently-sorted) decision
   list. Browser-side Blob so a 400-row export doesn't round-trip the
   server; the data is already in the in-memory `decisions` array. */
function exportDecisionsCSV() {
  const q = ($("#dec-filter").value || "").toLowerCase();
  const state = $("#dec-state").value;
  let rows = decisions.filter((r) => {
    if (state === "open" && r.outcome) return false;
    if (state === "closed" && !r.outcome) return false;
    if (!q) return true;
    return [r.symbol, r.venue, r.regime, r.grid_type, r.decision, r.id]
      .some((v) => String(v || "").toLowerCase().includes(q));
  });
  rows = rows.slice().sort((a, b) => _decCompare(a, b, decSort.key, decSort.dir));
  const cols = ["id", "at", "venue", "symbol", "regime", "grid_type",
    "decision", "score_final", "step_pct", "slot",
    "rationale", "outcome_realized", "outcome_reason"];
  const escCsv = (v) => {
    if (v == null) return "";
    const s = String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [cols.join(",")];
  for (const r of rows) {
    lines.push(cols.map((c) => {
      if (c === "outcome_realized") return escCsv((r.outcome || {}).realized_pnl);
      if (c === "outcome_reason") return escCsv((r.outcome || {}).reason);
      return escCsv(r[c]);
    }).join(","));
  }
  const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `decisions-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "")}.csv`;
  document.body.append(a); a.click(); document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  toast(`exported ${rows.length} decision${rows.length === 1 ? "" : "s"} to ${a.download}`);
}
$("#dec-filter").addEventListener("input", renderDecisions);
$("#dec-state").addEventListener("change", renderDecisions);
$("#dec-export").addEventListener("click", exportDecisionsCSV);

/* ── decision evidence panel (row expansion) ──────────────────────── */

/* What the agents actually evaluated the decision against: debate
   theses + per-agent LLM provider, risk-team stances, the tvcli /hunt
   confluence in hand, injected memories, and the fill/harvest model.
   Rows predating the evidence block degrade to a note. */
function decEvidenceHTML(r) {
  const ev = r.evidence;
  if (!ev || typeof ev !== "object") {
    return `<div class="empty-note">No evidence recorded — this decision predates the evidence block (rows after the daemon restart carry it).</div>`;
  }
  // Provider tag: violets for LLMs, dim for "no provider", amber for
  // rule-fallback. Mistral deserves its own tinted variant so the
  // operator can spot it at a glance.
  const provBadge = (p) => {
    if (!p) return `<span class="badge badge--dim">—</span>`;
    if (p === "rule-fallback") return `<span class="badge badge--warn" title="LLM chain unavailable; rules answered">rule-fallback</span>`;
    const klass = p === "mistral" ? "badge--mistral" : "badge--violet";
    const title = p === "mistral" ? "Mistral served this agent"
      : p === "cf" ? "Cloudflare Workers AI"
      : p === "nvidia" ? "NVIDIA NIM" : "OpenRouter";
    return `<span class="badge ${klass}" title="${esc(title)}">${esc(p)}</span>`;
  };
  const llm = ev.llm || {};
  const debate = ev.debate || {};
  const con = ev.confluence || {};
  const fit = con.fit || {};
  const fitKeys = ["atr_pct", "chop", "squeeze_momentum_pct", "squeeze_on",
    "squeeze_bars", "mtf_composite", "vol_ratio", "dvi_trend", "vp_poc",
    "vp_vah", "vp_val", "sr_last_break", "sr_break_bars_ago"];
  const fitChips = fitKeys.filter((k) => fit[k] !== undefined && fit[k] !== null)
    .map((k) => `<span class="badge badge--dim mono" title="tvcli /hunt read">${esc(k)} ${esc(typeof fit[k] === "boolean" ? (fit[k] ? "on" : "off") : fmtNum(fit[k], 2))}</span>`)
    .join(" ");
  const stances = Object.entries(ev.risk_stances || {}).map(([s, st]) => `
    <div class="row"><span class="k">${esc(s)}</span>
      <span class="v">${st.approve ? '<span class="badge badge--ok">approve</span>' : '<span class="badge badge--bad">veto</span>'}
        ${provBadge(st.llm)}
        <span class="mono" style="font-size:11px">mult ${esc(fmtNum(st.max_alloc_mult, 2))} · step ×${esc(fmtNum(st.step_mult, 2))}</span>
        ${st.note ? `<span class="rationale" title="${esc(st.note)}">— ${esc(st.note.slice(0, 90))}${st.note.length > 90 ? "…" : ""}</span>` : ""}</span></div>`).join("");
  const memories = (ev.memories || []).map((m) => `
    <li><span class="f-kind">${esc(String(m.at || "").slice(5, 10))}</span>
        <span class="f-msg">${esc(m.venue || "")}:${esc(m.symbol || "?")} ${esc(m.regime || "")} → ${esc(m.reason || "?")}
        ${m.outcome_pnl != null ? `<b class="${Number(m.outcome_pnl) >= 0 ? "m-value--good" : "m-value--bad"}">${fmtSignedUsd(m.outcome_pnl)}</b>` : ""}</span></li>`).join("");
  // Cohort context: same symbol+regime decisions, surfaced by the
  // /api/decisions/<id> endpoint. Empty for a fresh symbol; otherwise
  // it shows whether prior trips at this archetype made money.
  const cohort = r.cohort;
  const cohortLine = cohort && isNum(cohort.cohort_size) && cohort.cohort_size > 0
    ? `<div class="dec-ev-h" style="margin-top:8px">Cohort (same ${esc(r.symbol)} ${esc(r.regime || "")} prior decisions)</div>
       <div class="mini-kv">
         <div class="row"><span class="k">count</span><span class="v">${esc(cohort.cohort_size)} prior decision(s)</span></div>
         <div class="row"><span class="k">cohort realized PnL</span><span class="v"><span class="${(cohort.cohort_realized || 0) >= 0 ? "m-value--good" : "m-value--bad"}">${fmtSignedUsd(cohort.cohort_realized || 0)}</span></span></div>
       </div>` : "";
  return `
    <div class="dec-ev">
      <div class="dec-ev-grid">
        <div>
          <div class="dec-ev-h">Deliberation</div>
          <div class="dec-ev-agents">
            <span class="badge badge--dim">bull</span>${provBadge(llm.bull)}
            <span class="badge badge--dim">bear</span>${provBadge(llm.bear)}
            <span class="badge badge--dim">facilitator</span>${provBadge(llm.facilitator)}
            ${llm.degraded ? '<span class="badge badge--warn" title="one or more agents fell back to rules">degraded</span>' : ""}
          </div>
          ${debate.bull_thesis ? `<div class="dec-ev-quote"><b>bull</b> ${esc(debate.bull_thesis)}</div>` : ""}
          ${(debate.bear_risks || []).length ? `<div class="dec-ev-quote"><b>bear</b> ${esc(debate.bear_risks.join(" · "))}</div>` : ""}
          ${(debate.kill_triggers || []).length ? `<div class="dec-ev-quote"><b>kill</b> ${esc(debate.kill_triggers.join(" · "))}</div>` : ""}
          ${stances ? `<div class="dec-ev-h" style="margin-top:8px">Risk team</div><div class="mini-kv">${stances}</div>` : ""}
          ${cohortLine}
        </div>
        <div>
          <div class="dec-ev-h">tvcli confluence in hand</div>
          <div class="dec-ev-agents">
            <span class="badge ${Number(con.bonus) > 0 ? "badge--ok" : "badge--dim"}" title="score bonus from the tvcli /hunt pass">bonus +${fmtNum(con.bonus ?? 0, 1)}</span>
            <span class="badge badge--dim" title="skills that returned a result / hunted">${esc(con.ok ?? "?")} ok</span>
            ${(con.notes || []).map((n) => `<span class="badge badge--violet">${esc(n)}</span>`).join(" ")}
          </div>
          ${fitChips ? `<div class="dec-ev-agents" style="margin-top:6px">${fitChips}</div>` : ""}
          <div class="dec-ev-h" style="margin-top:8px">Fill / harvest model</div>
          <div class="mini-kv">
            <div class="row"><span class="k">expected fills /24h</span><span class="v">${ev.expected_fills_24h == null ? "—" : esc(fmtNum(ev.expected_fills_24h, 2))}</span></div>
            <div class="row"><span class="k">harvest net /24h</span><span class="v">${ev.harvest_net_pct_24h == null ? "—" : `${esc(fmtNum(ev.harvest_net_pct_24h, 2))}%`}</span></div>
          </div>
        </div>
      </div>
      ${memories ? `<div class="dec-ev-h">Memories injected (past outcomes)</div><ul class="feed">${memories}</ul>` : ""}
    </div>`;
}

$("#dec-body").addEventListener("click", async (e) => {
  if (e.target.closest("a,button")) return;
  // alt/meta-click on a decision id copies it; otherwise fall through to
  // the row-expansion logic below.
  const idNode = e.target.closest("[data-copy]");
  if (idNode && (e.altKey || e.metaKey || e.ctrlKey)) {
    e.preventDefault(); e.stopPropagation();
    const id = idNode.dataset.copy;
    const ok = await copyText(id);
    toast(ok ? `copied ${id}` : "copy failed", !ok);
    return;
  }
  const tr = e.target.closest("tr.dec-row");
  if (!tr) return;
  const next = tr.nextElementSibling;
  if (next && next.classList.contains("dec-detail")) { next.remove(); return; }
  let row = decisions.find((d) => d.id === tr.dataset.id);
  if (!row) return;
  // First expansion: lazy-fetch the cohort context from /api/decisions/<id>
  // so the evidence panel can show "prior trips at this archetype". After
  // that the cohort lives on `row.cohort` and the panel renders offline.
  if (row.cohort === undefined) {
    try {
      const detail = await api(`/api/decisions/${encodeURIComponent(row.id)}`);
      if (detail && detail.decision) {
        row = { ...row, cohort: { cohort_size: detail.cohort_size,
                                  cohort_realized: detail.cohort_realized } };
        const idx = decisions.findIndex((d) => d.id === row.id);
        if (idx >= 0) decisions[idx] = row;
      } else {
        row.cohort = { cohort_size: 0, cohort_realized: 0 };
      }
    } catch (e) { row.cohort = { cohort_size: 0, cohort_realized: 0 }; }
  }
  const det = document.createElement("tr");
  det.className = "dec-detail";
  det.innerHTML = `<td colspan="12">${decEvidenceHTML(row)}</td>`;
  tr.after(det);
});

/* ── run cards ────────────────────────────────────────────────────── */

let rcKind = "all";   // active kind filter for the run-card list

async function loadReports() {
  let list;
  try { list = (await api("/api/reports")).reports || []; }
  catch (e) { toast(`run cards: ${e.message}`, true); return; }
  // kind chips: rescreen (hourly-ish), optimizer (fast-loop, interesting
  // cycles only), audits + anything else — the mix tells the operator at
  // a glance which lanes are actually producing evidence
  const kinds = [...new Set(list.map((r) => r.kind || "other"))].sort();
  const chipBox = $("#rc-kinds");
  if (chipBox) {
    const counts = {};
    for (const r of list) counts[r.kind || "other"] = (counts[r.kind || "other"] || 0) + 1;
    if (!kinds.includes(rcKind)) rcKind = "all";
    chipBox.innerHTML = ["all", ...kinds].map((k) =>
      `<button class="rc-chip${rcKind === k ? " rc-chip--on" : ""}" data-kind="${esc(k)}">${esc(k)}${k === "all" ? ` (${list.length})` : ` (${counts[k] || 0})`}</button>`).join("");
    for (const chip of chipBox.querySelectorAll(".rc-chip")) {
      chip.addEventListener("click", () => { rcKind = chip.dataset.kind; loadReports(); });
    }
  }
  const shown = list.filter((r) => rcKind === "all" || (r.kind || "other") === rcKind);
  const box = $("#rc-list");
  box.innerHTML = shown.map((r) => `
    <div class="runcard-item" data-stem="${esc(r.stem)}" role="button" tabindex="0">
      <span class="rc-kind">${esc(r.kind)}</span>
      <span class="rc-stamp">${esc(String(r.at || r.stem).replace("T", " ").slice(0, 16))}</span>
      <span style="margin-left:auto" class="mono">${r.json ? "json" : ""}${r.md ? " md" : ""}</span>
    </div>`).join("") || `<div class="empty-note">No run cards of this kind yet — one lands here after every cycle.</div>`;
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
  // Stats header — surfaces hunt_stats + deliberation verdict + guard
  // vetoes at a glance so the operator doesn't have to scroll the JSON
  // just to confirm what tvcli/skills were queried and whether the
  // swarm actually returned a decision.
  const statsEl = $("#rc-stats");
  if (statsEl) {
    statsEl.innerHTML = renderRunCardStats(card.json || {});
  }
  $("#rc-json").textContent = JSON.stringify(card.json, null, 2);
}

/* compact "what did the loop DO this cycle?" header for a run card.
   Walks known-alias key paths so the same code renders both the rescreen
   and optimizer card schemas (and survives daemon-side renames). The
   alias table is the only place to update when a new schema lands. */
const RUN_CARD_KEYS = {
  hunt_stats:        ["screen.hunt_stats", "hunt.stats"],
  hunt_top:          ["hunt.top3", "screen.top3"],
  capital_committed: ["capital.committed_usd", "screen.capital.committed_usd"],
  capital_ceiling:   ["capital.deployable_ceiling_usd", "screen.capital.deployable_ceiling_usd"],
  capital_idle:      ["capital.idle_committed_usd", "screen.capital.idle_committed_usd"],
  capital_free:      ["capital.free_slots", "screen.capital.free_slots"],
  arbiter:           ["arbiter", "fast_arbiter.arbiter"],
  swaps:             ["swaps", "fast_arbiter.swaps"],
  vetoes:            ["vetoes", "fast_arbiter.vetoes"],
};
function _pick(obj, paths) {
  for (const p of paths) {
    const parts = p.split(".");
    let v = obj;
    for (const k of parts) {
      if (v == null || typeof v !== "object") { v = undefined; break; }
      v = v[k];
    }
    if (v !== undefined) return v;
  }
  return null;
}

function renderRunCardStats(j) {
  const parts = [];
  // tvcli /hunt skill stats — schema-agnostic via _pick
  const hs = _pick(j, RUN_CARD_KEYS.hunt_stats) || {};
  const skills = (hs && typeof hs.skills === "object") ? hs.skills : {};
  const sk = Object.entries(skills);
  if (sk.length) {
    const ok = sk.filter(([, s]) => s.hunted > 0 && s.hunted === s.ok).length;
    const fail = sk.filter(([, s]) => s.hunted > 0 && s.ok < s.hunted).length;
    const missed = sk.filter(([, s]) => s.hunted === 0).length;
    parts.push(`<div class="row"><span class="k">tvcli /hunt</span><span class="v">
      <span class="badge badge--ok">${ok}/${sk.length} skills all-ok</span>
      ${fail ? `<span class="badge badge--warn">${fail} failed</span>` : ""}
      ${missed ? `<span class="badge badge--dim">${missed} not hunted</span>` : ""}
      ${hs.candidates_boosted != null ? ` · <span class="mono">${esc(hs.candidates_boosted)} candidate(s) boosted</span>` : ""}
    </span></div>`);
  }
  // rescreen deliberations — bull/bear/facilitator verdicts
  const dels = j.deliberations || [];
  if (dels.length) {
    const go = dels.filter((d) => (d && (d.decision || "")).includes("GO")).length;
    parts.push(`<div class="row"><span class="k">deliberation</span><span class="v">
      <span class="badge badge--ok">${go} GO</span>
      <span class="mono" style="color:var(--ink-faint)">${dels.length} candidate(s)</span>
      ${dels.some((d) => d.llm_degraded) ? '<span class="badge badge--warn">degraded</span>' : ""}
    </span></div>`);
  }
  // guard — list of veto entries on rescreen
  const g = j.guard;
  if (Array.isArray(g) && g.length) {
    parts.push(`<div class="row"><span class="k">guard</span><span class="v">
      <span class="badge badge--bad">${g.length} veto(es)</span>
      <span class="mono" style="color:var(--ink-faint)">${esc(g.map((v) => v.symbol || v.reason || "?").slice(0, 4).join(", "))}${g.length > 4 ? "…" : ""}</span>
    </span></div>`);
  }
  // deployments + observed counts (rescreen)
  const deps = j.deployments || [];
  if (deps.length) {
    parts.push(`<div class="row"><span class="k">deployments</span><span class="v">${deps.length} grid(s) — ${deps.map((d) => `${esc(d.venue || "")}:${esc(d.symbol || "?")} ${esc(d.grid_type || "")}`).join(", ")}</span></div>`);
  }
  // optimizer card schema — capital / arbiter / swaps / vetoes (alias-tolerant)
  const capCommitted = _pick(j, RUN_CARD_KEYS.capital_committed);
  if (capCommitted != null) {
    const capCeiling = _pick(j, RUN_CARD_KEYS.capital_ceiling);
    const capIdle = _pick(j, RUN_CARD_KEYS.capital_idle);
    const capFree = _pick(j, RUN_CARD_KEYS.capital_free);
    parts.push(`<div class="row"><span class="k">capital</span><span class="v">${fmtUsd(capCommitted)} committed / ${fmtUsd(capCeiling)} ceiling · ${fmtUsd(capIdle || 0)} idle${capFree != null ? ` · ${esc(capFree)} free slot(s)` : ""}</span></div>`);
  }
  const ar = _pick(j, RUN_CARD_KEYS.arbiter);
  if (ar && typeof ar === "object") {
    parts.push(`<div class="row"><span class="k">arbiter</span><span class="v">${ar.approve === true ? '<span class="badge badge--ok">approve</span>' : '<span class="badge badge--bad">reject</span>'} · ${esc(ar.slot || "—")} → ${esc(ar.challenger || "—")} · ${esc(ar.llm || ar.provider || "mistral")}${ar.llm_degraded === true ? ' · <span class="badge badge--warn">degraded</span>' : ""}</span></div>`);
  }
  const swaps = _pick(j, RUN_CARD_KEYS.swaps);
  if (Array.isArray(swaps) && swaps.length) {
    parts.push(`<div class="row"><span class="k">swaps</span><span class="v">${swaps.map((s) => `<span class="badge ${s.ok ? "badge--ok" : "badge--bad"}">${esc(s.slot || "?")}${s.ok ? "" : " veto"}</span>`).join(" ")}</span></div>`);
  }
  const vetoes = _pick(j, RUN_CARD_KEYS.vetoes);
  if (Array.isArray(vetoes) && vetoes.length) {
    parts.push(`<div class="row"><span class="k">vetoes</span><span class="v">${vetoes.map((v) => `<span class="badge badge--warn">${esc(v.slot || "?")}</span>`).join(" ")}</span></div>`);
  }
  if (!parts.length) {
    return `<div class="empty-note" style="padding:6px 14px">No stats block on this card (older schema — view JSON below).</div>`;
  }
  return `<div class="card-head"><span class="card-title" style="font-size:12.5px">Quick stats</span></div>
    <div class="card-body"><div class="mini-kv">${parts.join("")}</div></div>`;
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
  // fail-soft companions: the ctl /status block (per-bot position
  // analysis + the fleet pnl projection), the journal sweep log, and
  // the swap-log/tracker rollup (per-slot idle timing + last arbiter
  // verdict — surfaced so the operator can see WHY every cycle passed)
  let st = null;
  try { st = await api("/api/status"); }
  catch (e) { st = null; }
  if (!st || st.error) st = null;
  let sweeps = null;
  try { sweeps = await api("/api/position-sweeps"); }
  catch (e) { sweeps = null; }
  let swapLog = null;
  try { swapLog = await api("/api/optimizer/swap-log"); }
  catch (e) { swapLog = null; }
  renderFastOptimizer(f, st);
  renderPositionAnalysis(st, sweeps);
  renderDataSources(st);
  renderSwapLog(swapLog);
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

  // Pending: group by slot (then venue+symbol) so a slot that has been
  // re-evaluated several times in a row shows ONE row with the latest rec
  // + an expandable history. Most slots have 0–1 pending; the ones that
  // have been oscillating across the apply gate are the interesting case.
  const bySlot = new Map();
  for (const r of pending) {
    const key = `${r.slot ?? "—"}|${r.venue}|${r.symbol}`;
    if (!bySlot.has(key)) bySlot.set(key, []);
    bySlot.get(key).push(r);
  }
  const groupRows = [];
  for (const [key, recs] of bySlot) {
    recs.sort((a, b) => (b.at || "").localeCompare(a.at || ""));
    const [head, ...rest] = recs;
    const gKey = `g-${key.replace(/[^a-zA-Z0-9_-]/g, "_")}`;
    const latestDelta = isNum(head.expected_delta_pct) ? Number(head.expected_delta_pct) : null;
    const deltaCls = latestDelta == null ? "m-value--dim"
      : latestDelta > 0 ? "m-value--good" : "m-value--bad";
    const latestConf = isNum(head.confidence) ? fmtNum(head.confidence, 2) : "—";
    const blocked = blockedByBadge(head.blocked_by);
    const at = esc(String(head.at || "").replace("T", " ").slice(5, 16));
    const slot = esc(head.slot ?? "—");
    const market = `<span class="venue-tag venue-tag--${esc(head.venue)}">${esc(head.venue || "")}</span>:${esc(head.symbol || "?")}`;
    const rec = `<span class="badge badge--violet">${esc(head.recommendation || "?")}</span>`;
    const delta = `<span class="${deltaCls}">${latestDelta == null ? "—" : `${latestDelta >= 0 ? "+" : ""}${fmtNum(latestDelta, 2)}%`}</span>`;
    const hasMore = rest.length > 0;
    const chev = hasMore
      ? `<span class="rel-chevron" aria-hidden="true">▸</span>`
      : `<span class="rel-chevron" style="visibility:hidden">▸</span>`;
    const trigger = esc(head.trigger || "—");
    const blockedCell = blocked;
    const rationale = `<div class="rationale" title="${esc(head.rationale || "")}">${esc(head.rationale || "—")}</div>`;
    groupRows.push(`<tr class="rec-group" data-gkey="${gKey}" data-count="${recs.length}">
      <td class="td-mono" title="${esc(head.at || "")}">${at}</td>
      <td class="td-mono">${slot}</td>
      <td class="td-mono">${market}</td>
      <td>${rec}</td>
      <td class="td-mono">${delta}</td>
      <td class="td-mono">${latestConf}</td>
      <td class="td-mono">${trigger}</td>
      <td>${blockedCell}</td>
      <td>${chev} ${hasMore ? `<span class="mono" style="color:var(--ink-faint);font-size:10.5px">+${rest.length} earlier</span>` : ""} ${rationale}</td>
    </tr>`);
    if (hasMore) {
      const hist = rest.map((r) => row(r, false)).join("");
      groupRows.push(`<tr class="rec-detail" data-gkey="${gKey}" hidden><td colspan="9">
        <div class="empty-note" style="margin:0 0 6px;font-size:11px">earlier recs for this slot (oldest first)</div>
        <table class="ledger rec-detail-table">${hist}</table>
      </td></tr>`);
    }
  }
  $("#opt-pending-body").innerHTML = groupRows.join("") ||
    `<tr><td colspan="9"><div class="empty-note">No pending recommendations \u2014 the position optimizer emits one when a bot\u2019s grid is off-price by more than the drift threshold (15 min cadence).</div></td></tr>`;
  // expand/collapse for grouped recs
  for (const head of document.querySelectorAll("#opt-pending-body tr.rec-group")) {
    head.addEventListener("click", (e) => {
      // ignore the inner rationale text selection
      if (window.getSelection && window.getSelection().toString()) return;
      const det = document.querySelector(`#opt-pending-body tr.rec-detail[data-gkey="${head.dataset.gkey}"]`);
      if (!det) return;
      const show = det.hidden;
      det.hidden = !show;
      const chev = head.querySelector(".rel-chevron");
      if (chev) chev.textContent = show ? "▾" : "▸";
    });
  }
  $("#opt-applied-body").innerHTML = applied.map((r) => row(r, true)).join("") ||
    `<tr><td colspan="8"><div class="empty-note">Nothing applied yet${applyEnabled ? "" : " \u2014 apply is disabled in config (advisory mode)"}.</div></td></tr>`;
}

/* ── fast slot optimizer (2–5m cadence capital reallocation) ───────── */

function renderFastOptimizer(f, st) {
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
      ${kv("Projected /24h", (st && st.pnl && isNum(st.pnl.projected_24h_usd)) ? fmtUsd(Number(st.pnl.projected_24h_usd)) : "—", "model-based expected grid income per 24h, net of round-trip fees (from the fleet PnL snapshot)")}
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
    </div>
    ${arbiterVerdictHTML(rep.arbiter)}`;
}

/* Last arbiter verdict (Mistral by default) — when the fast loop DID
   consult the model and what it said. Hidden when the loop has not
   needed an arbiter call yet (the band pre-filter skips the call when
   no swap is numerically possible — a healthy steady-state). */
function arbiterVerdictHTML(arb) {
  if (!arb || typeof arb !== "object") return "";
  const slot = arb.slot;
  const approve = arb.approve === true;
  const conf = isNum(arb.confidence) ? fmtNum(arb.confidence, 2) : "—";
  const pick = arb.challenger || "—";
  const reason = arb.reason || "";
  const degraded = arb.llm_degraded === true;
  const llm = arb.llm || (arb.provider || "mistral");
  const verdictBadge = approve
    ? `<span class="badge badge--ok">approve</span>`
    : `<span class="badge badge--bad">reject</span>`;
  return `<div class="card-body--tight table-wrap" style="border-top:1px solid var(--rule)">
    <div class="card-head" style="padding:6px 0 4px"><span class="card-title" style="font-size:12.5px">Last arbiter verdict</span>
      <span class="spacer"></span>
      ${degraded ? '<span class="badge badge--warn" title="LLM chain unavailable — rule fallback was used instead of the model">degraded</span>' : `<span class="mono" style="font-size:10.5px;color:var(--ink-faint)">${esc(llm)}</span>`}
    </div>
    <div class="mini-kv" style="padding:4px 0">
      <div class="row"><span class="k">verdict</span><span class="v">${verdictBadge} · slot ${esc(slot ?? "—")} → ${esc(pick)} · conf ${conf}</span></div>
      ${reason ? `<div class="row"><span class="k">reason</span><span class="v" title="${esc(reason)}">${esc(reason.slice(0, 200))}${reason.length > 200 ? "…" : ""}</span></div>` : ""}
    </div>
  </div>`;
}

/* ── position optimizer: latest per-bot analysis + sweep log ──────── */

/* The Pending/Applied tables below only carry recs gated at Δ≥2% — by
   design, so they sit empty most of the time. This card shows what the
   engine LAST concluded per bot (state.active_bots[*].position_optimizer,
   via ctl /status), including keeps and sub-threshold deltas, plus the
   journal sweep log underneath. */
function renderPositionAnalysis(st, sweeps) {
  const box = $("#opt-latest-analysis");
  if (!box) return;
  const ab = (st && typeof st.active_bots === "object" && st.active_bots) || {};
  const rows = Object.entries(ab).map(([slot, bot]) => {
    const po = (bot && typeof bot.position_optimizer === "object"
      && bot.position_optimizer) || {};
    return {
      slot, symbol: (bot || {}).symbol, venue: (bot || {}).venue,
      rec: po.last_recommendation ?? null,
      delta: isNum(po.last_delta_pct) ? Number(po.last_delta_pct) : null,
      conf: isNum(po.last_confidence) ? Number(po.last_confidence) : null,
      trigger: po.last_trigger ?? null,
      at: po.last_analyzed_at ?? null,
      hop: po.last_fetch_hop ?? null,
    };
  }).filter((r) => r.at != null || r.rec != null)
    .sort((a, b) => (Number(b.at) || 0) - (Number(a.at) || 0));

  const sweepList = (sweeps && Array.isArray(sweeps.sweeps)
    ? sweeps.sweeps : []).slice(0, 10);
  const recBadge = (rec) => rec === "keep"
    ? `<span class="badge badge--dim">keep</span>`
    : `<span class="badge badge--violet">${esc(rec || "?")}</span>`;

  box.innerHTML = `
    <div class="card-head"><span class="card-title">Latest position analysis</span>
      <span class="spacer"></span><span class="mono" style="font-size:11px;color:var(--ink-faint)" title="per-bot last analysis from state.active_bots[*].position_optimizer (all recs, including keeps and sub-threshold Δ — the Pending/Applied tables below only carry Δ≥2% gated recs)">${rows.length} bot${rows.length === 1 ? "" : "s"} analyzed</span></div>
    <div class="card-body--tight table-wrap">
      <table class="ledger">
        <thead><tr>
          <th>slot</th><th>market</th><th>rec</th><th>Δ%</th>
          <th>conf</th><th>trigger</th><th>analyzed</th><th>candle hop</th>
        </tr></thead>
        <tbody>
          ${rows.map((r) => `<tr>
            <td class="td-mono">${esc(r.slot)}</td>
            <td class="td-mono"><span class="venue-tag venue-tag--${esc(r.venue || "")}">${esc(r.venue || "")}</span>:${esc(r.symbol || "?")}</td>
            <td>${recBadge(r.rec)}</td>
            <td class="td-mono ${(r.delta || 0) >= 0 ? "m-value--good" : "m-value--bad"}">${r.delta == null ? "\u2014" : `${r.delta >= 0 ? "+" : ""}${fmtNum(r.delta, 2)}`}</td>
            <td class="td-mono">${r.conf == null ? "\u2014" : fmtNum(r.conf, 2)}</td>
            <td class="td-mono">${esc(r.trigger || "\u2014")}</td>
            <td class="td-mono" title="${esc(r.at != null ? String(r.at) : "")}">${esc(relTimeEpoch(r.at))}</td>
            <td>${r.hop == null ? "\u2014" : `<span class="badge badge--dim" title="how the analysis candles were fetched">${esc(r.hop)}</span>`}</td>
          </tr>`).join("") || `<tr><td colspan="8"><div class="empty-note">No bot has been analyzed yet \u2014 the position optimizer runs on its 15 min cadence (plus an on-entry pass after every deploy).</div></td></tr>`}
        </tbody>
      </table>
    </div>
    <details style="padding:10px 14px;border-top:1px solid var(--rule)">
      <summary class="mono" style="font-size:11px;color:var(--ink-faint);cursor:pointer">sweep history \u00b7 last ${sweepList.length} (journal)</summary>
      <ul class="feed" style="max-height:220px;overflow:auto">
        ${sweepList.map((e) => `<li><span class="f-at">${esc(String(e.at || "").replace("T", " ").slice(5, 16))}</span><span class="f-kind k--${esc(String(e.kind || "?").replace(/_/g, "-"))}">${esc(String(e.kind || "?").replace(/_/g, "-"))}</span><span class="f-msg">${esc(e.msg || "")}</span></li>`).join("") || `<li><span class="f-msg">No position-optimizer journal entries yet.</span></li>`}
      </ul>
    </details>`;
}

/* ── tvcli data sources: what the candle/confluence feeds found ───── */

/* Debugging surface for every tvcli-backed consumer: which hop served
   each candle fetch (direct / vision mirror / tvcli), and what the
   screen's /hunt confluence pass found per skill. All from ctl /status
   data_sources (fail-soft empty shapes). */
function renderDataSources(st) {
  const box = $("#opt-data-sources");
  if (!box) return;
  const ds = (st && typeof st.data_sources === "object"
    && st.data_sources) || null;
  if (!ds) {
    box.innerHTML = `
      <div class="card-head"><span class="card-title">tvcli data sources</span>
        <span class="spacer"></span><span class="badge badge--warn" title="ctl /status not responding">offline</span></div>
      <div class="card-body"><div class="empty-note">Data-source observability unavailable — this panel refills automatically once the daemon ctl plane is reachable again.</div></div>`;
    return;
  }
  const events = Array.isArray(ds.fetch_events) ? ds.fetch_events : [];
  const hs = (ds.hunt_stats && typeof ds.hunt_stats === "object")
    ? ds.hunt_stats : {};
  const skills = (hs.skills && typeof hs.skills === "object") ? hs.skills : {};
  const hopCounts = {};
  for (const e of events) {
    const h = e && e.hop;
    if (h) hopCounts[h] = (hopCounts[h] || 0) + 1;
  }
  const hops = Object.entries(hopCounts).sort((a, b) => b[1] - a[1]);
  const hopBadge = (h) => h === "tvcli"
    ? `<span class="badge badge--violet" title="TradingView WebSocket via the tvcli /fetch fallback">${esc(h)}</span>`
    : h === "vision"
      ? `<span class="badge badge--ok" title="Binance public data mirror (data-api.binance.vision)">${esc(h)}</span>`
      : `<span class="badge badge--dim" title="primary venue API (e.g. Hyperliquid)">${esc(h)}</span>`;
  const skillRows = Object.entries(skills).map(([name, s]) => {
    const hunted = Number((s || {}).hunted) || 0;
    const ok = Number((s || {}).ok) || 0;
    const allOk = hunted > 0 && ok === hunted;
    return `<tr>
      <td class="td-mono">${esc(name)}</td>
      <td class="td-mono">${ok}/${hunted}</td>
      <td>${allOk ? '<span class="badge badge--ok">all parsed</span>' : hunted === 0 ? '<span class="badge badge--dim">not hunted</span>' : `<span class="badge badge--warn">${hunted - ok} failed</span>`}</td>
    </tr>`;
  }).join("");

  box.innerHTML = `
    <div class="card-head"><span class="card-title">tvcli data sources</span>
      <span class="spacer"></span><span class="mono" style="font-size:10.5px;color:var(--ink-faint)" title="candle-hop attribution (market_regime fetch ring) + screen /hunt confluence counters — what the tvcli-backed systems found">${events.length ? `${events.length} recent fetch(es)` : "no fetches yet"}</span></div>
    <div class="card-body"><div class="mini-kv">
      <div class="row"><span class="k">candle hops</span><span class="v">${hops.length ? hops.map(([h, n]) => `${hopBadge(h)} \u00d7${n}`).join(" ") : "\u2014"}</span></div>
      <div class="row"><span class="k">confluence boosted</span><span class="v" title="candidates whose score_final the tvcli bonus moved in the last screen">${esc(String(hs.candidates_boosted ?? "\u2014"))} candidate(s)</span></div>
    </div></div>
    ${skillRows ? `<div class="card-body--tight table-wrap">
      <table class="ledger">
        <thead><tr><th>hunt skill</th><th>ok / hunted</th><th>state</th></tr></thead>
        <tbody>${skillRows}</tbody>
      </table>
    </div>` : `<div class="card-body"><div class="empty-note">No confluence hunt reported yet — the screen runs it over its top candidates (every rescreen).</div></div>`}
    <details style="padding:10px 14px;border-top:1px solid var(--rule)">
      <summary class="mono" style="font-size:11px;color:var(--ink-faint);cursor:pointer">candle fetch log \u00b7 last ${Math.min(events.length, 12)}</summary>
      <ul class="feed" style="max-height:200px;overflow:auto">
        ${events.slice(-12).reverse().map((e) => `<li><span class="f-at">${esc(relTimeEpoch(e && e.ts))}</span><span class="f-kind">${esc(String((e && e.venue) || "?"))}:${esc(String((e && e.symbol) || "?"))} ${esc(String((e && e.interval) || ""))}</span><span class="f-msg">${esc(String((e && e.hop) || "?"))} \u00b7 ${esc(String((e && e.rows) ?? "?"))} rows \u00b7 ${esc(String((e && e.ms) ?? "?"))} ms</span></li>`).join("") || `<li><span class="f-msg">No candle fetches recorded yet this daemon process.</span></li>`}
      </ul>
    </details>`;
}

/* ── fast-optimizer swap log + per-slot idle trackers ─────────────── */

/* Two tables: (1) per-slot idle timing — when each slot last saw a
   fill (the dials that drive the optimizer's idle flag), and (2) the
   swap_log itself — every swap the loop has ATTEMPTED with the ok/not
   verdict (a single cycle can record both a veto and the eventual
   succeed once a different challenger cleared). The last arbiter
   verdict is repeated here too in case the operator opened the tab
   directly without seeing renderFastOptimizer. */
function renderSwapLog(sl) {
  const idTrack = $("#opt-trackers");
  const idSwaps = $("#opt-swaps");
  if (!idTrack && !idSwaps) return;
  const trackers = (sl && sl.trackers) || [];
  const swaps = (sl && sl.swaps) || [];
  const arb = sl && sl.last_arbiter;
  const meta = `<span class="mono" style="font-size:10.5px;color:var(--ink-faint)">${esc(sl ? (sl.cycles || 0) : 0)} cycles · ${esc(sl ? (sl.swaps_total || 0) : 0)} swaps total</span>`;
  if (idTrack) {
    idTrack.innerHTML = `
      <div class="card-head"><span class="card-title">Per-slot idle trackers</span>
        <span class="spacer"></span>${meta}</div>
      <div class="card-body--tight table-wrap">
        <table class="ledger">
          <thead><tr><th>slot</th><th>last fills</th><th>idle (min)</th><th>last increase</th></tr></thead>
          <tbody>
            ${trackers.map((t) => {
              const idle = t.idle_min;
              const cls = idle == null ? "m-value--dim"
                : idle >= 60 ? "m-value--bad"
                : idle >= 15 ? "m-value--warn" : "m-value--dim";
              return `<tr>
                <td class="td-mono">${esc(t.slot ?? "—")}</td>
                <td class="td-mono">${isNum(t.last_fills) ? fmtNum(t.last_fills, 1) : "—"}</td>
                <td class="td-mono"><span class="${cls}">${idle == null ? "—" : fmtNum(idle, 0)}</span></td>
                <td class="td-mono">${t.last_increase_at ? esc(relTimeEpoch(t.last_increase_at)) : "—"}</td>
              </tr>`;
            }).join("") || `<tr><td colspan="4"><div class="empty-note">No slot trackers yet — the first optimize cycle populates them.</div></td></tr>`}
          </tbody>
        </table>
      </div>`;
  }
  if (idSwaps) {
    const arbHead = arb && typeof arb === "object"
      ? `<div class="card-head" style="padding:6px 0 0"><span class="card-title" style="font-size:12.5px">Last arbiter verdict</span>
          <span class="spacer"></span>
          ${arb.llm_degraded === true ? '<span class="badge badge--warn">degraded</span>' : `<span class="mono" style="font-size:10.5px;color:var(--ink-faint)">${esc(arb.llm || arb.provider || "mistral")}</span>`}
        </div>
        <div class="mini-kv" style="padding:4px 0 8px">
          <div class="row"><span class="k">verdict</span><span class="v">${arb.approve === true ? '<span class="badge badge--ok">approve</span>' : '<span class="badge badge--bad">reject</span>'} · slot ${esc(arb.slot ?? "—")} → ${esc(arb.challenger || "—")} · conf ${isNum(arb.confidence) ? fmtNum(arb.confidence, 2) : "—"}</span></div>
          ${arb.reason ? `<div class="row"><span class="k">reason</span><span class="v" title="${esc(arb.reason)}">${esc(arb.reason.slice(0, 200))}${arb.reason.length > 200 ? "…" : ""}</span></div>` : ""}
        </div>` : "";
    idSwaps.innerHTML = `
      ${arbHead}
      <div class="card-head"><span class="card-title">Swap log</span>
        <span class="spacer"></span><span class="mono" style="font-size:10.5px;color:var(--ink-faint)">last ${swaps.length}</span></div>
      <div class="card-body--tight table-wrap">
        <table class="ledger">
          <thead><tr><th>at</th><th>slot</th><th>verdict</th></tr></thead>
          <tbody>
            ${swaps.map((s) => `<tr>
              <td class="td-mono" title="${esc(s.at_iso || String(s.at || ""))}">${s.at_iso ? esc(String(s.at_iso).replace("T", " ").slice(5, 16)) : esc(relTimeEpoch(s.at))}</td>
              <td class="td-mono">${esc(s.slot ?? "—")}</td>
              <td>${s.ok ? '<span class="badge badge--ok">swapped</span>' : '<span class="badge badge--bad">vetoed</span>'}</td>
            </tr>`).join("") || `<tr><td colspan="3"><div class="empty-note">No swaps yet — the optimizer cycles every ${esc("2–5")} min; a swap only happens when the arbiter approves one inside the relaxed Δscore band.</div></td></tr>`}
          </tbody>
        </table>
      </div>`;
  }
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
    } else if (rel.missing || (rel.note && !rel.stale)) {
      notes.push(`<div class="banner banner--info"><div>${esc(rel.note || "No closed round-trips yet.")}</div></div>`);
    } else if (age != null) {
      notes.push(`<div class="banner banner--info"><div>Ledger snapshot age: <b>${fmtNum(age, 1)}h</b> (refresh cadence ${esc(rel.refresh_cadence_h ?? 24)}h).</div></div>`);
    }
    const anySynth = archs.some(([, s]) => (s.synthetic_samples || 0) > 0);
    if (anySynth) {
      notes.push(`<div class="banner banner--bad"><div><div class="banner-title">Synthetic/seeded samples pollute the ledger</div>
        Archetypes below carry seeded or backfilled samples (see the “real / synth” column). Expectancy and profit factor include them — they are not evidence from live round-trips.</div></div>`);
    }
    // Ladder thresholds card — pinned at the top so an operator can read
    // OFF the page exactly what an archetype needs to climb (or what
    // would kill it).
    const kt = (rel.kill_thresholds) || {};
    const full = ladder.full_samples || 30, probe = ladder.probe_samples || 10;
    notes.push(`<div class="banner banner--info" style="margin:8px 0 0"><div>
      <div class="banner-title">Sizing ladder thresholds</div>
      <div class="mini-kv" style="font-size:12px">
        <div class="row"><span class="k">base → probe</span><span class="v"><b>${probe}</b> closed samples</span></div>
        <div class="row"><span class="k">probe → full</span><span class="v"><b>${full}</b> closed samples AND PF ≥ <b>${ladder.pf_pass ?? 1.3}</b></span></div>
        <div class="row"><span class="k">recent PF kills archetype</span><span class="v">PF &lt; <b>${ladder.pf_kill ?? 1.0}</b> on last <b>${kt.recent_window ?? 20}</b> trips (binding only with ≥ <b>${kt.kill_min_samples ?? 10}</b> samples)</span></div>
        <div class="row"><span class="k">live gate</span><span class="v">≥ <b>${kt.live_min_samples ?? 30}</b> samples AND PF ≥ <b>${ladder.pf_pass ?? 1.3}</b> AND recent PF ≥ <b>${ladder.pf_kill ?? 1.0}</b></span></div>
      </div>
    </div></div>`);
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
    // ladder_next cell — tells the operator how many samples to the
    // NEXT tier, so the table answers "what unlocks the next ladder
    // rung" without clicking into a card.
    const ladderCell = s.tier === "killed"
      ? `<span class="badge badge--bad" title="recent PF &lt; 1.0 with ≥ kill_min_samples trips — refuses new deployments">kill-flagged</span>`
      : s.tier === "full"
      ? `<span class="mono" style="color:var(--ink-faint);font-size:10.5px">top rung</span>`
      : `${s.ladder_progress_pct != null ? `<span class="tier-track-mini"><span class="fill" style="width:${s.ladder_progress_pct.toFixed(1)}%"></span></span>` : ""}<span class="mono" style="font-size:10.5px;color:var(--ink-faint)">→ ${esc(s.ladder_next)} @ ${esc(s.ladder_next_at)}</span>`;
    return `<tr class="rel-row" data-arch="${esc(name)}" role="button" tabindex="0" title="click to expand recent closed round-trips">
      <td><span class="rel-chevron" aria-hidden="true">▸</span> <b>${esc(name)}</b></td>
      <td class="td-mono">${esc(s.samples ?? 0)}</td>
      <td class="td-mono">${synthCell}</td>
      <td><div class="tier-track" title="${esc(s.samples)} / ${full} samples to full">
        <div class="fill${s.tier === "killed" ? " fill--killed" : ""}" style="width:${pctFull.toFixed(1)}%"></div>
        <div class="mark" style="left:${(probe / full * 100).toFixed(1)}%" title="probe @${probe}"></div>
      </div></td>
      <td class="td-mono">${ladderCell}</td>
      <td class="td-mono ${(pfReal || 0) >= (ladder.pf_pass || 1.3) ? "m-value--good" : ""}"${synth ? ` title="includes ${synth} synthetic samples"` : ""}>${esc(fmtNum(pfReal, 2))}${synth ? "†" : ""}</td>
      <td class="td-mono ${(recentReal || 0) < (ladder.pf_kill || 1.0) ? "m-value--bad" : ""}">${esc(fmtNum(recentReal, 2))}</td>
      <td class="td-mono">${esc(fmtPct(s.win_rate))}</td>
      <td class="td-mono">${fmtUsd(expReal)}${synth ? "†" : ""}</td>
      <td class="td-mono">${fmtUsd(s.max_dd_usd)}</td>
      <td><span class="badge ${tierBadge}">${esc(s.tier)}</span></td>
    </tr>`;
  }).join("") || `<tr><td colspan="11"><div class="empty-note">No closed round-trips yet — the ledger fills as bots complete trades (24h refresh, or force one from Fleet).</div></td></tr>`;
  wireReliabilityExpansion();
}

/* Lazy-load /api/reliability/archive and inject a sub-row with the recent
   closed round-trips. First click fetches; subsequent clicks toggle. */
function wireReliabilityExpansion() {
  for (const row of document.querySelectorAll("#rel-body tr.rel-row")) {
    const open = () => toggleReliabilityRow(row);
    row.addEventListener("click", open);
    row.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
    });
  }
}

async function toggleReliabilityRow(row) {
  const next = row.nextElementSibling;
  if (next && next.classList.contains("rel-detail")) {
    next.remove();
    const chev = row.querySelector(".rel-chevron");
    if (chev) chev.textContent = "▸";
    return;
  }
  const arch = row.dataset.arch;
  const chev = row.querySelector(".rel-chevron");
  if (chev) chev.textContent = "▾";
  const det = document.createElement("tr");
  det.className = "rel-detail";
  det.innerHTML = `<td colspan="11"><div class="empty-note">Loading recent closed round-trips…</div></td>`;
  row.after(det);
  let resp;
  try { resp = await api(`/api/reliability/archive?limit=20`); }
  catch (e) {
    det.innerHTML = `<td colspan="11"><div class="empty-note">Trip history unavailable (${esc(e.message)})</div></td>`;
    return;
  }
  const trips = ((resp && resp.archetypes) || {})[arch] || [];
  if (!trips.length) {
    det.innerHTML = `<td colspan="11"><div class="empty-note">No archived trades for <b>${esc(arch)}</b> yet — archive grows when a bot rotates out.</div></td>`;
    return;
  }
  const rows = trips.map((t) => {
    const r = isNum(t.realized) ? Number(t.realized) : 0;
    const cls = r > 0 ? "m-value--good" : r < 0 ? "m-value--bad" : "m-value--dim";
    const hold = isNum(t.hold_s) ? formatHold(Number(t.hold_s)) : "—";
    const ts = t.ts ? esc(relTimeEpoch(t.ts / (t.ts > 1e12 ? 1000 : 1))) : "—";
    return `<tr>
      <td class="td-mono">${ts}</td>
      <td class="td-mono">${esc(t.symbol || "—")}</td>
      <td class="td-mono">${esc(t.venue || "—")}</td>
      <td class="td-mono ${cls}">${fmtSignedUsd(r)}${t.is_panic ? " <span class=\"badge badge--warn\" title=\"panic-exit\">P</span>" : ""}</td>
      <td class="td-mono">${hold}</td>
      <td>${t.is_synthetic ? '<span class="badge badge--bad" title="seeded/backfilled — does not count toward the ladder">synthetic</span>' : ""}</td>
    </tr>`;
  }).join("");
  det.innerHTML = `<td colspan="11">
    <div class="rel-detail-head">Recent closed round-trips — <b>${esc(arch)}</b> · last ${trips.length}</div>
    <table class="ledger rel-detail-table">
      <thead><tr><th>closed</th><th>symbol</th><th>venue</th><th>realized</th><th>hold</th><th>notes</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  </td>`;
}

function formatHold(sec) {
  if (!isFinite(sec) || sec < 0) return "—";
  if (sec < 60) return `${Math.round(sec)}s`;
  if (sec < 3600) return `${Math.round(sec / 60)}m`;
  if (sec < 86400) return `${(sec / 3600).toFixed(1)}h`;
  return `${(sec / 86400).toFixed(1)}d`;
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
const LLM_CLEAR = "__CLEAR__"; // explicit-delete sentinel for /api/llm

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
    // Per-provider "clear key" — only visible when a key is already stored.
    // Sets the input to the __CLEAR__ sentinel and marks it dirty so the
    // next save POSTs an explicit delete; the server's apply_llm strips
    // the key from the sidecar and the provider falls out of the chain.
    const clearBtn = prov.key_present
      ? el("button", { class: "btn btn--ghost llm-key-clear", title: "remove the stored API key",
          onclick: () => {
            keyInput.value = LLM_CLEAR;
            keyInput.dataset.dirty = "1";
            keyInput.classList.add("llm-key-clearing");
            clearBtn.disabled = true;
            clearBtn.textContent = "will clear on save";
            setTimeout(() => { keyInput.classList.remove("llm-key-clearing"); }, 800);
          } }, "clear key")
      : null;
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
          keyInput, clearBtn ? clearBtn : "")));
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
// append-only diff state: the filter + last rendered line used to decide
// whether the next poll can append a delta or must re-render the window.
let logPrevGrep = "";
let logPrevLines = "";
let logPrevLast = "";

async function loadLogs(force = false) {
  if (activeView !== "logs" && !force) return;
  const follow = $("#log-follow").checked;
  const grep = ($("#log-grep").value || "").trim();
  const lines = $("#log-lines").value;
  const params = new URLSearchParams({
    lines,
    ...(grep ? { grep } : {}),
  });
  let data;
  try { data = await api(`/api/logs?${params}`); }
  catch (e) { return; }
  const newLines = data.lines || [];
  const box = $("#logbox");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  // Append-only diff mode: while following with an unchanged filter, keep
  // the rendered window and only append the lines that appeared since the
  // last poll (highlighted), instead of replacing the whole box. Anchor is
  // the previous last line — if it still occurs in the new window, the
  // lines after it are the delta; if the tail rolled past it (too much
  // output, rotation, truncation), fall back to a full re-render.
  const sameFilter = logPrevGrep === grep && logPrevLines === lines;
  if (follow && sameFilter && logPrevLast && newLines.length) {
    const anchor = logPrevLast;
    const j = newLines.lastIndexOf(anchor);
    if (j >= 0) {
      const fresh = newLines.slice(j + 1);
      if (fresh.length) {
        const frag = document.createDocumentFragment();
        for (const ln of fresh) {
          const div = document.createElement("div");
          div.className = "log-line log-line--new";
          div.textContent = ln;
          frag.append(div);
        }
        box.append(frag);
        $("#log-count").textContent = `${data.total} line(s)`;
        if (atBottom) box.scrollTop = box.scrollHeight;
        logPrevLast = newLines[newLines.length - 1];
        return;
      }
      return; // no new lines — nothing to paint
    }
  }
  logPrevGrep = grep; logPrevLines = lines;
  logPrevLast = newLines.length ? newLines[newLines.length - 1] : "";
  box.textContent = "";
  if (!newLines.length) {
    box.textContent = "— no matching lines —";
  } else {
    const frag = document.createDocumentFragment();
    for (const ln of newLines) {
      const div = document.createElement("div");
      div.className = "log-line";
      div.textContent = ln;
      frag.append(div);
    }
    box.append(frag);
  }
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
$("#ctl-rotate-go").addEventListener("click", ctlRotateFromPanel);

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

/* grid/autonomy console — components/capital-rail.js
   Fleet capital-utilization rail (UI_AUDIT P2-17: the per-slot sleeve
   numbers were verified present in the live payload and rendered
   nowhere). This is the operator's answer to "why are funds idle":

     a. portfolio line  — committed / ceiling · idle · projected /24h
     b. per-slot rows    — slot · venue · balance · max commitment ·
                          committed · share-of-venue-sleeve bar
     c. plan-cap lines  — paper/demo bot cap + venue plan caps
     d. why-idle note   — the honest static reasons capital sits idle

   Data is everything app.js already holds from the ctl /status proxy
   (slots[], committed, pnl, capacity, demo_cap) — nothing is fetched
   here and no server fields were added. app.js calls
   window.CapitalRail.render(ov, st) once per poll (ONE hook line in
   loadOverview); the component keeps its own digest and repaints only
   when the capital inputs (or the minute bucket for the age stamp)
   actually changed.

   DOM: owns a #capital-rail card inserted as the first child of the
   fleet view's .rail aside. Self-contained + idempotent (reuses the
   node across renders), fail-soft on every entry point. */
(function () {
  "use strict";

  var lastDigest = null;

  /* tiny local helpers — the component must not depend on app.js
     internals beyond call-time typeof-guarded niceties (fmtUsd) */
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function num(v) {
    var n = Number(v);
    return isFinite(n) ? n : null;
  }
  function usd(v) {
    var n = num(v);
    if (n == null) return "\u2014";
    if (typeof fmtUsd === "function") { try { return fmtUsd(n); } catch (e) { /* fall through */ } }
    return "$" + n.toFixed(2);
  }
  function pct(v, d) {
    var n = num(v);
    return n == null ? "\u2014" : n.toFixed(d == null ? 1 : d) + "%";
  }

  /* ── data extraction (alias-tolerant, all fail-soft) ─────────────── */

  function ctlStatus(ov, st) {
    if (st && typeof st === "object") return st;
    return (ov && ov.ctl && ov.ctl.status) || null;
  }

  function portfolioLine(ov, st) {
    var p = (st && st.pnl) || {};
    var committed = num(p.committed_usd);
    var idle = num(p.idle_usd);
    var total = (ov && ov.config_digest && num(ov.config_digest.total_usd)) || null;
    if (total == null && committed != null && idle != null) total = committed + idle;
    return {
      committed: committed, idle: idle, total: total,
      projected: num(p.projected_24h_usd),
      projected_annual_return_pct: num(p.projected_annual_return_pct),
    };
  }

  /* ── render ───────────────────────────────────────────────────────── */

  function render(ov, st) {
    try { paint(ov, st); } catch (e) { /* fail-soft: rail is telemetry */ }
  }

  function paint(ov, st) {
    var host = document.querySelector(".fleet-layout .rail");
    if (!host) return;
    var cs = ctlStatus(ov, st);

    var slots = (cs && Array.isArray(cs.slots)) ? cs.slots : [];
    var committedMap = (cs && cs.committed && typeof cs.committed === "object")
      ? cs.committed : {};
    var p = portfolioLine(ov, st);
    var cap = (cs && cs.capacity) || null;
    var demo = (cs && cs.demo_cap) || null;
    var at = (st && st.last_cycle) || (ov && ov.at) || null;

    // digest: repaint only when the capital inputs (or the minute bucket
    // behind the age stamp) changed — fits the renderFleet digest-skip
    var digest = JSON.stringify([slots, committedMap, p, cap, demo,
      Math.floor(Date.now() / 60000)]);
    if (digest === lastDigest) return;
    lastDigest = digest;

    var card = document.getElementById("capital-rail");
    if (!card) {
      card = document.createElement("div");
      card.className = "card cr-card";
      card.id = "capital-rail";
      host.insertBefore(card, host.firstChild);
    }

    if (!cs) {
      card.innerHTML = `<div class="card-head"><span class="card-title">Capital</span></div>
        <div class="empty-note">Capital telemetry offline — the daemon /status proxy is unreachable.</div>`;
      return;
    }

    /* (a) portfolio line */
    var idlePct = (p.idle != null && p.total) ? (p.idle / p.total) * 100 : null;
    var portfolio = `<div class="cr-portfolio">
      <span class="cr-pl" title="capital at work in grid positions / the whole fund (config total_usd)">committed <b>${usd(p.committed)}</b> / ceiling ${usd(p.total)}</span>
      <span class="cr-pl" title="fund not currently deployed in any grid — see the why-idle note below">idle <b>${usd(p.idle)}</b>${idlePct != null ? ` (${idlePct.toFixed(0)}%)` : ""}</span>
      <span class="cr-pl" title="model-based expected grid income per 24h, net of round-trip fees">proj <b>${usd(p.projected)}</b>/24h</span>
      ${p.projected_annual_return_pct != null ? `<span class="cr-pl" title="approximate annualized return on committed capital IF the projected rate held (model, not a guarantee)">\u2248 ${pct(p.projected_annual_return_pct, 1)}/yr</span>` : ""}
    </div>`;

    /* (b) per-slot rows with share-of-sleeve bars */
    var rows = slots.map(function (s) {
      var slot = s && s.slot != null ? s.slot : "?";
      var committed = num(committedMap[String(slot)]);
      var sleeve = num(s && s.venue_sleeve);
      var share = (committed != null && sleeve) ? Math.min(100, (committed / sleeve) * 100) : null;
      var bar = share == null ? `<span class="cr-bar cr-bar--na" title="venue sleeve not reported"></span>`
        : `<span class="cr-bar" title="committed ${usd(committed)} of the ${esc(s.venue || "?")} sleeve ${usd(sleeve)} (${share.toFixed(1)}%)"><span class="cr-bar-fill" style="width:${share.toFixed(1)}%"></span></span>`;
      return `<div class="cr-slot" data-slot="${esc(slot)}">
        <span class="cr-slot-id">S${esc(slot)}</span>
        <span class="cr-slot-venue">${esc(s && s.venue || "?")}${s && s.dynamic ? ` <span class="badge badge--violet" title="slot opened dynamically by the daemon while the token cleared the open-slot score threshold and spare capital covered the worst case">dyn</span>` : ""}</span>
        <span class="cr-slot-cell cr-slot-bal" title="profile balance assigned to this slot">bal <b>${usd(s && s.balance)}</b></span>
        <span class="cr-slot-cell cr-slot-max" title="max capital this slot may commit — balance after reliability-tier and risk-multiplier discounts">max <b>${usd(s && s.max_commitment)}</b></span>
        <span class="cr-slot-cell cr-slot-in" title="capital actually committed to the live grid">in <b>${usd(committed)}</b></span>
        ${bar}
      </div>`;
    }).join("") || `<div class="empty-note">No slots reported — the fleet is empty or the daemon predates the sleeve fields.</div>`;

    /* (c) plan caps — demo/paper bot cap + venue plan caps */
    var capLines = [];
    if (demo && demo.total && num(demo.total.active) != null) {
      var dt = demo.total;
      var full = num(dt.cap) != null && dt.active >= dt.cap;
      var prof = "";
      if (demo.per_profile && typeof demo.per_profile === "object") {
        var parts = [];
        for (var k in demo.per_profile) {
          if (!Object.prototype.hasOwnProperty.call(demo.per_profile, k)) continue;
          var pp = demo.per_profile[k] || {};
          if (num(pp.active) == null) continue;
          parts.push(`<span class="cr-prof" title="WT profile ${esc(k)} \u00b7 active ${esc(pp.active)} of cap ${pp.cap == null ? "\u221e" : esc(pp.cap)}">${esc(String(k).slice(-6))} ${esc(pp.active)}/${pp.cap == null ? "\u221e" : esc(pp.cap)}</span>`);
        }
        if (parts.length) prof = `<span class="cr-profline">${parts.join(" ")}</span>`;
      }
      capLines.push(`<div class="cr-capline${full ? " cr-capline--full" : ""}" title="paper/demo grid-bot platform cap (WunderTrading demo-bot limit) \u00b7 headroom ${dt.headroom == null ? "?" : esc(dt.headroom)}">
        paper bots <b>${esc(dt.active)}${num(dt.cap) != null ? "/" + esc(dt.cap) : ""}</b>${num(dt.headroom) != null ? ` \u00b7 headroom ${esc(dt.headroom)}` : ""}${full ? " \u2014 deploys blocked" : ""}${prof}
      </div>`);
    }
    if (cap) {
      var ma = (cap.max_active) || {}, ac = (cap.active) || {};
      var otherMax = num(ma.other), otherAct = num(ac.other);
      var premAct = null;
      if (ac.premium && typeof ac.premium === "object") {
        premAct = 0;
        for (var pk in ac.premium) premAct += num(ac.premium[pk]) || 0;
      }
      capLines.push(`<div class="cr-capline" title="enforced per-exchange plan caps: non-premium venues 1 active grid bot, premium (Hyperliquid swap) 200">
        venue caps: other ${otherAct == null ? "?" : esc(otherAct)}/${otherMax == null ? "?" : esc(otherMax)} \u00b7 premium ${premAct == null ? "?" : esc(premAct)}/${num(ma.premium) == null ? "?" : esc(ma.premium)}
      </div>`);
    }

    /* (d) the honest why-idle note */
    var why = `<div class="cr-why" title="the static reasons idle capital is NOT a bug">
      <b>why idle:</b> each slot's max commitment is its balance after reliability-tier &amp; risk-multiplier discounts \u00b7 ~15% of the fund is held back as cash buffer \u00b7 plan caps (5 paper bots total, 1 active grid bot per non-premium exchange) veto new deploys regardless of spare capital.
    </div>`;

    card.innerHTML = `
      <div class="card-head">
        <span class="card-title">Capital</span>
        <span class="spacer"></span>
        <span class="mono cr-at" title="ctl /status snapshot age">${esc(relAge(at))}</span>
      </div>
      <div class="cr-body">
        ${portfolio}
        <div class="cr-slots">${rows}</div>
        ${capLines.join("")}
        ${why}
      </div>`;
  }

  function relAge(at) {
    var t = at ? Date.parse(at) : 0;
    if (!t || !isFinite(t)) return "\u2014";
    var s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 90) return Math.round(s) + "s ago";
    if (s < 5400) return Math.round(s / 60) + "m ago";
    return Math.round(s / 3600) + "h ago";
  }

  window.CapitalRail = { render: render };
})();

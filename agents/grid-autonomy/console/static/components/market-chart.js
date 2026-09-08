/* grid/autonomy console — components/market-chart.js
   Owns the market modal chart (UI_AUDIT P1-2). The old openMarketModal
   baked W = max(320, min(innerWidth*0.9, 900)) into the SVG viewBox at
   open time and never repainted, so rotating a phone ballooned/stretched
   the chart. This component measures the host container's clientWidth at
   paint time (drawPnlChart pattern) and repaints on a debounced resize +
   orientationchange while the modal is open. Height drops to 280 on
   short viewports (innerHeight < 640). Everything else (markup, channel
   refs, captions, Escape/backdrop/Close teardown, cold-cache spinner
   with 12s bounded fetch) is ported as-is from app.js.

   app.js's openMarketModal is now a 2-line shim. chartCache/fetchChart/
   lastOverview stay in app.js and are reached by bare reference (classic
   scripts share the global lexical environment) — only ever at CALL time. */
(function () {
  "use strict";

  const RX = 62;                  // right gutter for channel labels
  const RESIZE_DEBOUNCE_MS = 120; // repaint lag after resize/orientationchange
  const FETCH_TIMEOUT_MS = 12000; // tvcli is normally <3s cached — never strand on a spinner

  /* fail-soft entry point — a modal that cannot build must never take the
     slot-card click handler down with it. */
  function open(key, slot) {
    try { openModal(key, slot); } catch (e) { /* fail-soft: no modal beats a dead one */ }
  }

  function openModal(key, slot) {
    const parts = String(key).split(":");
    const venue = parts[0] || "?", symbol = parts.slice(1).join(":") || "?";
    const root = $("#modal-root");
    const box = el("div", { class: "modal-backdrop" });

    const modal = el("div", { class: "modal modal--chart", role: "dialog", "aria-modal": "true" },
      el("h3", {}, `MARKET — ${esc(venue)}:${esc(symbol)}`),
      el("div", { class: "modal-body" },
        el("div", { class: "mk-chart" })),
      el("div", { class: "modal-actions" },
        el("button", { class: "btn", onclick: () => done() }, "Close")));
    const chartHost = modal.querySelector(".mk-chart");

    let alive = true;        // false once done() ran — repaints must not fire after
    let lastBars = null;     // last painted bars — resize repaints from these
    let resizeTimer = null;

    /* P1-2: measure the host at paint time instead of innerWidth math.
       The modal is in the DOM before the first paint, so clientWidth is
       real; fall back to the old viewport heuristic if it reads 0
       (display:none edge cases). Short viewports get a shorter chart. */
    function geom() {
      const w = chartHost ? chartHost.clientWidth : 0;
      const W = w > 40 ? Math.max(320, w)
        : Math.max(320, Math.min(window.innerWidth * 0.9, 900));
      const H = window.innerHeight < 640 ? 280 : 360;
      return { W, H };
    }

    function paint(bars) {
      lastBars = bars;
      const bots = (lastOverview && lastOverview.bots) || [];
      const bot = bots.find((b) => String(b.slot) === String(slot)) || null;
      const ch = (bot && bot.channel) || null;
      const { W, H } = geom();
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

    function onResize() {
      if (!alive) return;
      if (resizeTimer) clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        if (alive && lastBars) paint(lastBars);  // repaint from the cached bars
      }, RESIZE_DEBOUNCE_MS);
    }

    function done() {
      alive = false;
      if (resizeTimer) clearTimeout(resizeTimer);
      window.ModalFocus?.close();   // P2-8: unlock scroll + restore focus first
      root.innerHTML = "";
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onResize);
      window.removeEventListener("orientationchange", onResize);
    }
    function onKey(e) { if (e.key === "Escape") done(); }

    // wire listeners + append FIRST so clientWidth is measurable at paint
    // time (the old code painted before the modal entered the DOM).
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", onResize);
    window.addEventListener("orientationchange", onResize);
    box.append(modal);
    box.addEventListener("mousedown", (e) => { if (e.target === box) done(); });
    root.append(box);
    window.ModalFocus?.open(modal);   // P2-8: trap Tab + lock page scroll while open
    modal.querySelector(".modal-actions .btn").focus();

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
      const timeout = new Promise((_, rej) => { timer = setTimeout(() => rej(new Error("timeout")), FETCH_TIMEOUT_MS); });
      const work = fetchChart(venue, symbol, "1h", 96)
        .then((d) => (d && d.bars) || [])
        .catch(() => null);
      Promise.race([work, timeout]).then((bars) => {
        clearTimeout(timer);
        // If the user closed the modal in the meantime, chartHost is no
        // longer in the DOM — skip the second paint.
        if (!alive || !chartHost.isConnected) return;
        paint(bars && bars.length >= 2 ? bars : []);
      });
    }
  }

  window.MarketChart = { open };
})();

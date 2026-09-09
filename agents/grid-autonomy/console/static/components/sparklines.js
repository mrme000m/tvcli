/* grid/autonomy console — components/sparklines.js
   Inline SVG sparkline renderers — extracted from app.js (operator's
   "frontend components in separate files instead of existing already
   large files" requirement). Two sites share one min-max scale core:

     window.Sparklines.render(node, points, channel)
       Paints ONE .slot-spark node: 220x48 min-max scaled polyline with
       3px pad, teal when the window is up / crimson when down, plus
       dashed channel high/low refs when the bot carries a channel.
       `points` is the same raw bars array (entries with .c) the old
       renderSlotSparklines loop consumed; the caller keeps the
       chartCache data plumbing and the dataset.at epoch guard so
       re-renders stay idempotent.

     window.Sparklines.scoreSVG(history)
       The "last screen" rail's top-of-score trend SVG string (110x18,
       2px pad): green trending up, crimson down, gray flat.

   Both fail soft — missing node / short input → no-op / "" — and the
   rendered output is byte-identical to the original inline code. */
(function () {
  "use strict";

  /* local mirror of app.js's top-level `const isNum` (script-scoped,
     NOT a window property — see mobile.js's note on lastPnlPoints). The
     component must not depend on app.js internals. */
  function isNum(v) {
    return typeof v === "number" && isFinite(v) ||
      (typeof v === "string" && v !== "" && !isNaN(Number(v)));
  }

  /* shared core: min-max scale mapping, same math as the extracted
     sites. expandFlat=true mirrors renderSlotSparklines (widen lo/hi by
     0.1% when the series is flat); false mirrors scoreSVG (clamp the
     span to >=1e-6, keep lo/hi). denom is the X denominator — the slot
     site spans X across ALL bars (bars.length), not just the finite
     closes, so gappy series keep their original point spacing. */
  function scale(values, W, H, P, expandFlat, denom) {
    var n = denom || values.length;
    var lo = Math.min.apply(null, values), hi = Math.max.apply(null, values);
    var span;
    if (expandFlat) {
      if (hi - lo < 1e-12) { var e = Math.abs(hi) * 0.001 || 0.001; hi += e; lo -= e; }
      span = hi - lo;
    } else {
      span = Math.max(1e-6, hi - lo);
    }
    return {
      X: function (i) { return P + (i / (n - 1)) * (W - 2 * P); },
      Y: function (v) { return P + (1 - (v - lo) / span) * (H - 2 * P); },
    };
  }

  /* ── .slot-spark painter ────────────────────────────────────────── */

  function render(node, points, channel) {
    if (!node || !Array.isArray(points) || points.length < 2) return;
    var bars = points;
    const closes = [];
    for (const b of bars) { const c = Number(b && b.c); if (isFinite(c)) closes.push(c); }
    if (closes.length < 2) return;
    const W = 220, H = 48, P = 3;
    const m = scale(closes, W, H, P, true, bars.length);
    const X = m.X, Y = m.Y;
    let pts = "";
    bars.forEach((b, i) => {
      const c = Number(b && b.c);
      if (isFinite(c)) pts += `${X(i).toFixed(2)},${Y(c).toFixed(2)} `;
    });
    const first = closes[0], last = closes[closes.length - 1];
    const up = last >= first;
    const delta = first ? ((last - first) / first) * 100 : 0;
    let refs = "";
    const ch = channel || null;
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

  /* ── "last screen" rail score trend ─────────────────────────────── */

  function scoreSVG(history) {
  if (!Array.isArray(history) || history.length < 2) return "";
  const W = 110, H = 18, P = 2;
  const scores = history.map((h) => Number(h.score)).filter(isFinite);
  if (scores.length < 2) return "";
  const m = scale(scores, W, H, P, false);
  const X = m.X, Y = m.Y;
  const pts = scores.map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(" ");
  const first = scores[0], last = scores[scores.length - 1];
  const up = last > first, flat = last === first;
  const stroke = flat ? "var(--ink-faint)" : up ? "var(--teal)" : "var(--crimson)";
  return `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" aria-hidden="true" class="screen-spark">
    <polyline fill="none" stroke="${stroke}" stroke-width="1.2" points="${pts}"/>
  </svg>`;
  }

  window.Sparklines = { render: render, scoreSVG: scoreSVG };
})();

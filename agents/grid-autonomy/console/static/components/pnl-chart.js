/* grid/autonomy console — components/pnl-chart.js
   PnL timeline chart — extracted from app.js drawPnlChart (operator's
   "frontend components in separate files instead of existing already
   large files" requirement). Canvas DPR-aware 2-line series: net (solid
   + area) and realized (thin), zero line, newest on the right.
   Container-driven width measured from the canvas's parent (.pnl-chart)
   content box, 360 fallback for tests/headless; the two empty-state
   texts are preserved verbatim.

   app.js keeps a thin top-level shim (window.drawPnlChart) so all
   existing call sites — incl. mobile.js's resize handler — keep working
   unchanged. Fail-soft: no-op when the canvas node or 2d context is
   missing. The local isNum/relTime helpers below are self-contained
   mirrors of app.js's globals (same semantics; output identical when
   app.js is present). */
(function () {
  "use strict";

  /* local mirror of app.js's top-level `const isNum` (script-scoped,
     NOT a window property — see mobile.js's note on lastPnlPoints). The
     component must not depend on app.js internals. */
  function isNum(v) {
    return typeof v === "number" && isFinite(v) ||
      (typeof v === "string" && v !== "" && !isNaN(Number(v)));
  }
  /* relTime is a top-level function declaration in app.js → window.relTime.
     Call-time guarded so a missing app.js never throws from here. */
  function relTime(iso) {
    if (typeof window.relTime === "function") return window.relTime(iso);
    return "";
  }

function draw(points) {
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
  // container-driven width: measure the canvas's parent (.pnl-chart) content
  // box — clientWidth includes padding, and desktop .pnl-chart carries
  // padding-left 18px, so subtract both paddings or the canvas pokes past
  // the card edge. 360 fallback for tests/headless where the element has
  // no measured box. Clamped so tiny rails don't crush the chart.
  const parentEl = canvas.parentElement;
  let parentW = 0;
  if (parentEl) {
    const cs = (typeof getComputedStyle === "function")
      ? getComputedStyle(parentEl) : null;
    const padL = cs ? parseFloat(cs.paddingLeft) || 0 : 0;
    const padR = cs ? parseFloat(cs.paddingRight) || 0 : 0;
    parentW = Math.max(0, parentEl.clientWidth - padL - padR);
  }
  const W = parentW > 0 ? Math.min(480, Math.max(240, parentW)) : 360;
  const H = 96;
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

  window.PnlChart = { draw: draw };
})();

/* grid/autonomy console — components/stale-guard.js
   Persistent stale-marker for console outages (UI_AUDIT P1-7). When a
   view's load* keeps failing, the old UI showed only a 4.2s toast and
   then left plausible-looking stale data on screen with no marker.
   This component tracks a last-ok/first-fail timestamp per load key and,
   once a key backing the ACTIVE view has been failing >10s, renders a
   fixed banner--warn strip at the top of that view ("view stale since
   HH:MM — backend unreachable, retrying…") plus a subtle .stale-dim on
   the view content (CSS in components/components.css).

   app.js hooks: one StaleGuard?.fail(view)/.ok(view) line per load*
   catch/success (loadOverview/Decisions/Reports/Reliability/Config/Llm/
   Logs). Key "llm" shares the config view's banner with "config".

   Fail-soft by design: every entry point is try/catch'd — the guard
   itself must never break the console. */
(function () {
  "use strict";

  const THRESHOLD_MS = 10000;  // failing longer than this → banner
  const RENDER_TICK_MS = 5000; // re-evaluate without waiting for a poll

  // load keys that back each view's banner (config + llm share a view)
  const VIEW_KEYS = {
    fleet: ["fleet"], decisions: ["decisions"], reports: ["reports"],
    optimizer: ["optimizer"], reliability: ["reliability"],
    config: ["config", "llm"], logs: ["logs"],
  };

  const state = {}; // key -> { okAt: ms, failAt: ms | null }

  function st(key) {
    if (!state[key]) state[key] = { okAt: 0, failAt: null };
    return state[key];
  }

  function fail(key) {
    const s = st(key);
    if (s.failAt == null) s.failAt = Date.now();
    render();
  }

  function ok(key) {
    const s = st(key);
    s.failAt = null;
    s.okAt = Date.now();
    render();
  }

  function currentView() {
    try {
      // activeView is app.js's top-level let — reachable by bare reference
      if (typeof activeView !== "undefined" && activeView) return activeView;
    } catch (e) { /* TDZ before app.js runs — fall through to the DOM scan */ }
    for (const v of document.querySelectorAll("section.view")) {
      if (!v.hidden) return String(v.id || "").replace(/^view-/, "");
    }
    return null;
  }

  function hhmm(ms) {
    return new Date(ms).toTimeString().slice(0, 5);
  }

  function render() {
    try {
      const view = currentView();
      const sec = view ? document.querySelector(`#view-${view}`) : null;
      if (!sec) return;
      const keys = VIEW_KEYS[view] || [];
      const failing = keys.filter((k) => state[k] && state[k].failAt != null);
      let banner = sec.querySelector(":scope > .stale-banner");
      if (!failing.length) {
        if (banner) banner.remove();
        sec.classList.remove("stale-dim");
        return;
      }
      // all failing keys count from their FIRST failure, not the last retry
      const since = Math.min(...failing.map((k) => state[k].failAt));
      if (Date.now() - since <= THRESHOLD_MS) return; // transient blips pass silently
      if (banner) {
        const b = banner.querySelector(".stale-since");
        if (b) b.textContent = hhmm(since);
      } else {
        sec.insertAdjacentHTML("afterbegin",
          `<div class="banner banner--warn stale-banner" role="status" aria-live="polite">` +
          `<div>view stale since <b class="stale-since">${hhmm(since)}</b> — backend unreachable, retrying…</div></div>`);
        banner = sec.querySelector(":scope > .stale-banner");
      }
      sec.classList.add("stale-dim");
    } catch (e) { /* fail-soft: never break the console */ }
  }

  // some views (reports/config/reliability) only load on tab switch —
  // without a timer the banner would never cross the 10s threshold for them.
  setInterval(render, RENDER_TICK_MS);

  window.StaleGuard = { fail, ok, render };
})();

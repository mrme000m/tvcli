/* grid/autonomy console — components/llm-health.js
   Owns the Fleet "LLM brains" card lifecycle (UI_AUDIT P1-1).
   The old inline renderLlmBrains() was fired by every 5s poll tick with
   no dedupe (~13 parallel /api/llm/health requests on a cold ping), no
   timeout, and late responses could clobber newer ones. This component
   adds: a singleton in-flight promise (chartInflight pattern), an
   AbortController with a 15s client timeout, a "pinging…" placeholder
   while the first response is outstanding, and a monotonic guard (a
   response older than the last rendered one is dropped).

   Server contract (server.py llm_health): warm cache → normal payload;
   cold/expired → immediate 200 with pending:true + last-known-good
   results ([] when none), while ONE background thread refreshes — so the
   client timeout only ever bounds transport, not the provider ping.

   Loaded BEFORE app.js; touches app.js helpers ($, esc, relTime, api)
   only at CALL time (classic scripts share the global lexical env). A
   failure here degrades to app.js's guarded shim, never breaks the poll. */
(function () {
  "use strict";

  let inflight = null;     // singleton promise — dedupes the 5s poll ticks
  let renderedAt = 0;      // Date.parse() of the last payload we painted
  let hasRendered = false; // first successful paint done?

  const PING_TIMEOUT_MS = 15000;

  const box = () => document.querySelector("#llm-brains");
  const atEl = () => document.querySelector("#llm-brains-at");

  /* "pinging…" placeholder — shown while the very first response is in
     flight, and again when a pending:true response arrives with no
     last-known-good results yet. */
  function showPinging() {
    const b = box();
    if (b) b.innerHTML = `<div class="empty-note"><span class="spinner"></span> pinging LLM providers…</div>`;
  }

  /* muted marker lines for the async server contract */
  function noteHTML(d) {
    if (d && d.stale) return `<div class="llm-stale-note mono">stale — showing last-known results while a refresh pings the providers</div>`;
    if (d && d.pending) return `<div class="llm-stale-note mono">refreshing…</div>`;
    return "";
  }

  /* ported verbatim from the old renderLlmBrains body — same markup so
     the existing CSS (ready-cell, mini-kv, badge) applies unchanged. */
  function paint(d) {
    const results = d.results || [];
    const roles = d.roles || {};
    const arbProv = d.arbiter_provider || "mistral";
    const a = atEl();
    if (a && d.at) a.textContent = `pinged ${relTime(d.at)}`;
    const dot = (ok) => ok ? "●" : "○";
    const cls = (ok) => ok ? "ready-cell--on" : "ready-cell--off";
    const provRow = results.map((r) => {
      const name = r.provider;
      const labelMap = { cf: "CF Workers AI", nvidia: "NVIDIA", openrouter: "OpenRouter", mistral: "Mistral" };
      const lbl = labelMap[name] || name;
      const err = r.ok ? "" : (r.error || "FAIL");
      return `<div class="ready-cell ${cls(r.ok)}" title="${esc(err || "ok")}">
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
      return `<div class="row"><span class="k" title="Pinned provider for ${esc(roleKey)}. Default follows the chain.">${esc(label)}</span>
        <span class="v">${using === "follow chain"
          ? `<span class="badge badge--dim">follow chain</span>`
          : `<span class="badge badge--violet">${esc(using)}</span>`}</span></div>`;
    };
    const arbRow = `<div class="row"><span class="k" title="Pinned provider for the fast-lane arbiter. Default = ${esc(arbProv || "mistral")}. Override: config.optimizer.llm_provider.">arbiter (fast lane)</span>
      <span class="v"><span class="badge badge--violet">${esc(arbUsing)}</span>${arbPinned ? "" : ` <span class="mono" style="color:var(--ink-faint);font-size:10.5px">default</span>`}</span></div>`;
    const swarmRows = swarmRoles.map((r) => roleRow(r.replace(/_/g, " "), r)).join("");
    const b = box();
    if (b) b.innerHTML = `
      <div class="readiness-cells" style="margin-bottom:8px">${provRow}</div>
      <div class="mini-kv">${arbRow}${swarmRows}</div>
      ${noteHTML(d)}`;
  }

  function render() {
    if (!box()) return Promise.resolve();
    if (inflight) return inflight;   // dedupe: one request across poll ticks
    if (!hasRendered) showPinging(); // first paint — don't sit on the static placeholder
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), PING_TIMEOUT_MS);
    inflight = (async () => {
      try {
        const d = await api("/api/llm/health", { signal: ctl.signal });
        const at = Date.parse((d && d.at) || "") || 0;
        // monotonic guard: a response older than the last rendered one
        // (a late cold ping resolving after a warm one) is dropped.
        if (at < renderedAt) return;
        renderedAt = at;
        const empty = !(d && d.results || []).length;
        if (empty && (d.pending || !hasRendered)) { showPinging(); return; }
        paint(d);
        hasRendered = true;
      } catch (e) {
        // abort (15s timeout) or transport failure — keep the last-known
        // card on screen; only the very first failure shows a note.
        if (!hasRendered) {
          const b = box();
          if (b) b.innerHTML = `<div class="empty-note">Provider health unreachable.</div>`;
        }
      } finally {
        clearTimeout(timer);
        inflight = null;
      }
    })();
    return inflight;
  }

  window.LlmHealth = { render };
})();

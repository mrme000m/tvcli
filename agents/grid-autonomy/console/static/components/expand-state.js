/* grid/autonomy console — components/expand-state.js
   Remembers expanded rows across the 20s poll re-renders (UI_AUDIT P1-5):
   loadDecisions rewrites #dec-body and loadOptimizer rewrites
   #opt-pending-body every 4th tick, which collapsed open decision
   evidence rows (tr.dec-detail) and optimizer grouped-rec histories
   (tr.rec-detail) mid-read. This module keeps a Set of open decision
   ids + open rec-group gkeys and restores them after each repaint.

   Key routing for toggle(): decision ids are UUIDs from decisions.jsonl;
   rec-group gkeys are always emitted as `g-…` (app.js renderOptimizer) —
   that prefix decides which set a toggle lands in.

   app.js hooks (all typeof-guarded, fail-soft):
   * renderDecisions tail  → ExpandState.restoreDecisions()
   * dec-row click delegate → ExpandState.toggle(id) on expand/collapse
   * renderOptimizer tail   → ExpandState.restoreRecGroups()
   * rec-group click       → ExpandState.toggle(gkey)

   Loaded BEFORE app.js; reaches decisions/decEvidenceHTML by bare
   reference at CALL time only (classic scripts share the global
   lexical environment). The cohort fetched by the click path lives on
   the in-memory `decisions` rows, so restored evidence renders offline. */
(function () {
  "use strict";

  const openDec = new Set(); // decision ids with an expanded evidence row
  const openRec = new Set(); // optimizer rec-group gkeys with visible history

  function isOpen(id) { return openDec.has(id) || openRec.has(id); }

  function toggle(id) {
    if (openDec.has(id)) { openDec.delete(id); return false; }
    if (openRec.has(id)) { openRec.delete(id); return false; }
    if (String(id).startsWith("g-")) openRec.add(id);
    else openDec.add(id);
    return true;
  }

  function forget(id) { openDec.delete(id); openRec.delete(id); }

  /* after a renderDecisions repaint: re-insert tr.dec-detail for every
     open id whose row survived the filter/sort/limit. Ids that scrolled
     out of the loaded ledger entirely are pruned to bound the set. */
  function restoreDecisions() {
    try {
      const list = (typeof decisions !== "undefined" && Array.isArray(decisions)) ? decisions : [];
      for (const id of [...openDec]) {
        const tr = document.querySelector(`tr.dec-row[data-id="${CSS.escape(id)}"]`);
        if (!tr) {
          if (!list.some((d) => d && d.id === id)) openDec.delete(id);
          continue;
        }
        if (tr.nextElementSibling && tr.nextElementSibling.classList.contains("dec-detail")) continue;
        const row = list.find((d) => d && d.id === id);
        if (!row) { openDec.delete(id); continue; }
        const det = document.createElement("tr");
        det.className = "dec-detail";
        det.innerHTML = `<td colspan="12">${decEvidenceHTML(row)}</td>`;
        tr.after(det);
        tr.setAttribute("aria-expanded", "true");
      }
    } catch (e) { /* fail-soft: expansion memory is a nicety, never a hard dep */ }
  }

  /* after a renderOptimizer repaint: un-hide tr.rec-detail for open
     gkeys + flip their chevrons/aria state. Groups that vanished from
     the table are pruned. */
  function restoreRecGroups() {
    try {
      for (const det of document.querySelectorAll("#opt-pending-body tr.rec-detail")) {
        const gkey = det.dataset.gkey || "";
        if (!gkey || !openRec.has(gkey)) continue;
        const grp = document.querySelector(`#opt-pending-body tr.rec-group[data-gkey="${CSS.escape(gkey)}"]`);
        if (!grp) continue;
        det.hidden = false;
        const chev = grp.querySelector(".rel-chevron");
        if (chev) chev.textContent = "▾";
        grp.setAttribute("aria-expanded", "true");
      }
      for (const gkey of [...openRec]) {
        if (!document.querySelector(`#opt-pending-body tr.rec-group[data-gkey="${CSS.escape(gkey)}"]`))
          openRec.delete(gkey);
      }
    } catch (e) { /* fail-soft */ }
  }

  window.ExpandState = { isOpen, toggle, forget, restoreDecisions, restoreRecGroups };
})();

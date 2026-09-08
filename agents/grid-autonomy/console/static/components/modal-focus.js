/* grid/autonomy console — components/modal-focus.js
   Focus trap + background scroll lock for the console modals (UI_AUDIT
   P2-8). app.js confirmDialog() and components/market-chart.js append
   modal-backdrop overlays into #modal-root; they already focus their
   primary button and close on Escape/backdrop, but Tab walked out into
   the page behind the backdrop, and that page kept scrolling.

   Contract: call ModalFocus.open(modalEl) right after the modal enters
   the DOM (before focusing its primary button, so the pre-modal focus
   is captured), and ModalFocus.close() as the FIRST step of teardown.
   close() restores focus to the pre-modal element and unlocks page
   scroll. Idempotent and re-entrant: the console only ever shows one
   modal at a time (both call sites clear #modal-root), so a stray
   open() while one is active just re-points the trap; close() is a
   no-op when nothing is open.

   Fail-soft by design: every entry point try/catch'd — the trap is a
   nicety and must never break the modal it guards. */
(function () {
  "use strict";

  var FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]),'
    + ' select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

  var active = null; // { modal, prevFocus, prevOverflow }

  function focusables(modal) {
    if (!modal || !modal.querySelectorAll) return [];
    return Array.prototype.filter.call(
      modal.querySelectorAll(FOCUSABLE),
      function (n) { return n.offsetParent !== null || n === document.activeElement; }
    );
  }

  function onKey(e) {
    try {
      if (e.key !== "Tab" || !active || !active.modal || !active.modal.isConnected) return;
      var list = focusables(active.modal);
      if (!list.length) { e.preventDefault(); return; }
      var first = list[0], last = list[list.length - 1];
      var inside = active.modal.contains(document.activeElement);
      if (e.shiftKey) {
        if (!inside || document.activeElement === first) { e.preventDefault(); last.focus(); }
      } else {
        if (!inside || document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    } catch (err) { /* fail-soft */ }
  }

  function open(modal) {
    try {
      if (!modal) return;
      // keep the FIRST pre-modal focus when re-opened over a live trap
      if (!active || !active.prevFocus || !active.prevFocus.isConnected) {
        active = { prevFocus: document.activeElement,
                   prevOverflow: document.documentElement.style.overflow || "" };
      }
      active.modal = modal;
      // lock the page behind the backdrop (both axes; the modal itself
      // scrolls in .modal-body / .modal overflow-y)
      document.documentElement.style.overflow = "hidden";
      if (!open.hooked) {
        document.addEventListener("keydown", onKey, true);
        open.hooked = true;
      }
    } catch (e) { /* fail-soft */ }
  }

  function close() {
    try {
      if (!active) return;
      var prevOverflow = active.prevOverflow;
      var pf = active.prevFocus;
      active = null;
      document.documentElement.style.overflow = prevOverflow;
      if (pf && pf.isConnected && typeof pf.focus === "function") {
        try { pf.focus({ preventScroll: true }); } catch (e) { pf.focus(); }
      }
    } catch (e) { /* fail-soft */ }
  }

  window.ModalFocus = { open: open, close: close };
})();

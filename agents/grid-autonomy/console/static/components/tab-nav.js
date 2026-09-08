/* grid/autonomy console — components/tab-nav.js
   Roving-tabindex + arrow-key navigation for the top tab strip
   (UI_AUDIT P2-9). The tablist markup is real (role=tab,
   aria-selected, aria-controls) but had no arrow support: every tab
   sat in the Tab order. WAI-ARIA Authoring Practices for a tablist:
   exactly ONE tab (the selected one) in the page tab order, and
   Left/Right/Home/End move focus AND selection together.

   Selection goes through the tab's own click handler (app.js wires
   selectView per tab) — no app.js globals are touched beyond
   HTMLElement.click(), so a rename degrades to a no-op.

   At <=760px the top strip is display:none (responsive.css — the
   bottom nav is the mobile surface) but the DOM nodes stay, so
   mobile.js keeps building/syncing the bnav from them; bnav buttons
   are plain buttons and remain reachable by Tab, so keyboard users
   never lose navigation. */
(function () {
  "use strict";

  function strip() {
    return document.querySelector('.tabs[role="tablist"]');
  }

  function tabList() {
    var t = strip();
    if (!t) return [];
    return Array.prototype.slice.call(t.querySelectorAll('.tab[role="tab"]'));
  }

  /* roving tabindex: selected tab = 0, the rest = -1 */
  function refresh() {
    var list = tabList();
    for (var i = 0; i < list.length; i++) {
      list[i].tabIndex = list[i].getAttribute("aria-selected") === "true" ? 0 : -1;
    }
  }

  function select(tab) {
    if (!tab) return;
    refresh();
    tab.focus();
    if (typeof tab.click === "function") tab.click(); // app.js selectView
  }

  function onKey(e) {
    var t = strip();
    if (!t) return;
    var tab = e.target && e.target.closest ? e.target.closest('.tab[role="tab"]') : null;
    if (!tab || !t.contains(tab)) return;
    var list = tabList();
    var i = list.indexOf(tab);
    if (i < 0) return;
    var n = null;
    if (e.key === "ArrowRight") n = (i + 1) % list.length;
    else if (e.key === "ArrowLeft") n = (i - 1 + list.length) % list.length;
    else if (e.key === "Home") n = 0;
    else if (e.key === "End") n = list.length - 1;
    if (n == null) return;
    e.preventDefault();
    select(list[n]);
  }

  function init() {
    var t = strip();
    if (!t || t.__tabnavHooked) return;
    t.__tabnavHooked = true;
    t.setAttribute("aria-orientation", "horizontal");
    t.addEventListener("keydown", function (e) {
      try { onKey(e); } catch (err) { /* fail-soft */ }
    });
    refresh();
    // app.js flips aria-selected in selectView — keep the roving index
    // in sync after every selection change (tab set is static, so the
    // observer is cheap and lives as long as the strip).
    if (window.MutationObserver) {
      new MutationObserver(function () { try { refresh(); } catch (e) {} }).observe(t, {
        attributes: true, attributeFilter: ["aria-selected"], subtree: true,
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { try { init(); } catch (e) {} });
  } else {
    try { init(); } catch (e) {}
  }
})();

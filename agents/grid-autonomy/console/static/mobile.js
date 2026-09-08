/* mobile.js — progressive mobile layer for the grid/autonomy mission console.
   A classic script loaded AFTER app.js: no modules, no imports, no build step.
   Everything is defensive — each feature no-ops when its elements are absent
   and nothing here may throw (fail-soft pass-throughs everywhere).

   Responsibilities:
     a. bottom navigation (<=760px, visibility handled by responsive.css)
     b. table "card mode": data-label injection + .ledger--cards toggle
     c. table scroll hints (.table-wrap--hint) when a table really overflows
     d. tab strip scroll affordance (.tabs--more)
     e. PnL chart redraw on resize (calls app.js globals drawPnlChart /
        lastPnlPoints through window, guarded with typeof checks)

   The 5s polling loop rewrites innerHTML of the tabular views, so the DOM
   decoration passes re-run via a debounced MutationObserver on document.body.
*/
(function () {
  "use strict";

  var MQ_MOBILE = "(max-width: 760px)";
  var BNAV_ICONS = {
    "tab-fleet": "\u25a6",       /* ▦ */
    "tab-decisions": "\u2261",   /* ≡ */
    "tab-reports": "\u25a4",     /* ▤ */
    "tab-optimizer": "\u2733",   /* ✳ */
    "tab-reliability": "\u2605", /* ★ */
    "tab-config": "\u2699",      /* ⚙ */
    "tab-logs": "\u2263"         /* ≣ */
  };

  var bnav = null;
  var passTimer = null;
  var resizeTimer = null;

  function safe(fn) {
    try { fn(); } catch (e) { /* never break the console UI */ }
  }

  function isMobile() {
    return !!(window.matchMedia && window.matchMedia(MQ_MOBILE).matches);
  }

  /* ── (a) bottom navigation ───────────────────────────────────────── */

  function tabsNav() {
    return document.querySelector(".tabs");
  }

  function bnavSyncActive() {
    var tabs = tabsNav();
    if (!tabs || !bnav) return;
    var active = tabs.querySelector('.tab[aria-selected="true"]');
    var btns = bnav.querySelectorAll("button");
    for (var i = 0; i < btns.length; i++) {
      var on = !!(active && btns[i].getAttribute("data-tab") === active.id);
      btns[i].classList.toggle("is-active", on);
      if (on) btns[i].setAttribute("aria-current", "page");
      else btns[i].removeAttribute("aria-current");
    }
  }

  function destroyBnav() {
    if (bnav && bnav.parentNode) bnav.parentNode.removeChild(bnav);
    bnav = null;
  }

  function buildBnav() {
    var tabs = tabsNav();
    if (!tabs) return; /* no tab strip → no bottom bar at all */
    var tabBtns = Array.prototype.slice.call(tabs.querySelectorAll(".tab"));
    if (!tabBtns.length) return;
    if (bnav) {
      /* rebuild only if the tab set changed since we built it */
      if (bnav.querySelectorAll("button").length === tabBtns.length) {
        bnavSyncActive();
        return;
      }
      destroyBnav();
    }
    var nav = document.createElement("nav");
    nav.className = "bnav";
    nav.setAttribute("aria-label", "Console views");
    tabBtns.forEach(function (tab) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.setAttribute("data-tab", tab.id || "");
      /* label = tab text minus the count badge */
      var clone = tab.cloneNode(true);
      var badges = clone.querySelectorAll(".count");
      for (var i = 0; i < badges.length; i++) {
        if (badges[i].parentNode) badges[i].parentNode.removeChild(badges[i]);
      }
      var label = (clone.textContent || "").replace(/\s+/g, " ").trim();
      var ico = document.createElement("span");
      ico.className = "bnav-ico";
      ico.textContent = Object.prototype.hasOwnProperty.call(BNAV_ICONS, tab.id || "")
        ? BNAV_ICONS[tab.id] : "\u2022";
      var lbl = document.createElement("span");
      lbl.className = "bnav-label";
      lbl.textContent = label;
      btn.appendChild(ico);
      btn.appendChild(lbl);
      btn.addEventListener("click", function () {
        var el = document.getElementById(tab.id);
        if (el && typeof el.click === "function") el.click();
      });
      nav.appendChild(btn);
    });
    if (!document.body) return;
    document.body.appendChild(nav);
    bnav = nav;
    bnavSyncActive();
  }

  function watchTabs() {
    var tabs = tabsNav();
    if (!tabs || !window.MutationObserver) return;
    var obs = new MutationObserver(function () {
      safe(buildBnav); /* syncs active state; rebuilds if the tab set changed */
    });
    obs.observe(tabs, {
      attributes: true, attributeFilter: ["aria-selected"],
      childList: true, subtree: true,
    });
  }

  /* ── (b) table card mode: data-label injection ───────────────────── */

  function decorateTable(table) {
    var ths = table.querySelectorAll("thead th");
    if (!ths.length) return;
    for (var t = 0; t < table.tBodies.length; t++) {
      var rows = table.tBodies[t].rows;
      for (var r = 0; r < rows.length; r++) {
        var cells = rows[r].cells;
        for (var c = 0; c < cells.length; c++) {
          var td = cells[c];
          if (td.tagName !== "TD") continue;
          if (td.colSpan && td.colSpan > 1) continue; /* empty-state / detail rows */
          var th = ths[c];
          if (th) {
            td.setAttribute("data-label",
              (th.textContent || "").replace(/\s+/g, " ").trim());
          }
        }
      }
    }
  }

  function applyCardMode() {
    var mobile = isMobile();
    var tables = document.querySelectorAll("table.ledger");
    for (var i = 0; i < tables.length; i++) {
      decorateTable(tables[i]);
      tables[i].classList.toggle("ledger--cards", mobile);
    }
  }

  /* ── (c) table scroll hints (only when the table really overflows) ── */

  function applyScrollHints() {
    if (isMobile()) return; /* card mode owns the layout there */
    var wraps = document.querySelectorAll(".table-wrap");
    for (var i = 0; i < wraps.length; i++) {
      var wrap = wraps[i];
      wrap.classList.toggle("table-wrap--hint",
        wrap.scrollWidth > wrap.clientWidth + 1);
    }
  }

  /* ── (d) tab strip scroll affordance ──────────────────────────────── */

  function updateTabsAffordance() {
    var tabs = tabsNav();
    if (!tabs || !isMobile()) return;
    var canScroll = tabs.scrollWidth > tabs.clientWidth + 4;
    var atEnd = tabs.scrollLeft + tabs.clientWidth >= tabs.scrollWidth - 4;
    tabs.classList.toggle("tabs--more", canScroll && !atEnd);
  }

  function attachTabsScroll() {
    var tabs = tabsNav();
    if (!tabs || tabs.__mobileScrollHooked) return;
    tabs.__mobileScrollHooked = true;
    tabs.addEventListener("scroll", function () {
      safe(updateTabsAffordance);
    }, { passive: true });
  }

  /* ── shared debounced pass (poll re-renders → re-decorate) ───────── */

  function runPass() {
    safe(applyCardMode);
    safe(applyScrollHints);
    safe(updateTabsAffordance);
  }

  function schedulePass() {
    if (passTimer) return;
    passTimer = setTimeout(function () {
      passTimer = null;
      runPass();
    }, 150);
  }

  function isOurNode(node) {
    return !!(node && (node === bnav ||
      (node.classList && node.classList.contains("bnav"))));
  }

  function startBodyObserver() {
    if (!window.MutationObserver || !document.body) return;
    var obs = new MutationObserver(function (mutations) {
      for (var i = 0; i < mutations.length; i++) {
        var m = mutations[i];
        if (m.type !== "childList") continue;
        var nodes = (m.addedNodes && m.addedNodes.length) ? m.addedNodes
          : m.removedNodes;
        for (var j = 0; j < nodes.length; j++) {
          if (isOurNode(nodes[j])) return; /* self-inflicted: ignore the batch */
        }
      }
      schedulePass();
    });
    obs.observe(document.body, { childList: true, subtree: true });
  }

  /* ── (e) PnL chart redraw on resize ──────────────────────────────── */

  function onResize() {
    if (resizeTimer) clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      resizeTimer = null;
      safe(function () {
        if (typeof window.drawPnlChart === "function") {
          window.drawPnlChart(window.lastPnlPoints || []);
        }
      });
      safe(applyScrollHints);
      safe(updateTabsAffordance);
    }, 200);
  }

  /* ── init ────────────────────────────────────────────────────────── */

  var initialized = false;

  function init() {
    if (initialized) return;
    initialized = true;
    safe(buildBnav);
    safe(watchTabs);
    safe(attachTabsScroll);
    safe(startBodyObserver);
    schedulePass(); /* debounced first pass right after load */
    if (typeof window.addEventListener === "function") {
      window.addEventListener("resize", function () { safe(onResize); },
        { passive: true });
    }
    var mq = window.matchMedia && window.matchMedia(MQ_MOBILE);
    if (mq) {
      var onMQ = function () { schedulePass(); };
      if (typeof mq.addEventListener === "function") mq.addEventListener("change", onMQ);
      else if (typeof mq.addListener === "function") mq.addListener(onMQ);
    }
    /* views can render after the first poll tick (~5s) — re-check once */
    setTimeout(function () {
      safe(buildBnav);
      safe(attachTabsScroll);
      runPass();
    }, 6000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { safe(init); });
  } else {
    safe(init);
  }
})();

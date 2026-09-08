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

  /* Narrow phones give each bnav button ~44px of label space — the full
     tab names ellipsize to "RELIABILIT…". Short labels keep every view
     named; unknown tab ids fall back to the full tab text. */
  var BNAV_SHORT = {
    "tab-fleet": "FLEET",
    "tab-decisions": "DECS",
    "tab-reports": "CARDS",
    "tab-optimizer": "OPT",
    "tab-reliability": "REL",
    "tab-config": "CFG",
    "tab-logs": "LOGS"
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
      btn.title = label; /* full name on long-press / hover */
      var lbl = document.createElement("span");
      lbl.className = "bnav-label";
      lbl.textContent = Object.prototype.hasOwnProperty.call(BNAV_SHORT, tab.id || "")
        ? BNAV_SHORT[tab.id] : label;
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

  /* ── (f) card-mode sort control ────────────────────────────────────
     Card mode hides thead, which is where the decision ledger's sort
     affordances live (th.dec-sort in app.js). Mirror them in a compact
     <select> above the table, driving the SAME app.js globals: decSort
     is a top-level `let` object (writable via bare reference from this
     classic script) and renderDecisions is a function declaration
     (window-reachable). Every global access is typeof-guarded so a
     rename in app.js degrades to a no-op, not a throw. The table
     element itself survives the 5s poll (only thead/tbody innerHTML is
     rewritten), so the injected select persists and is re-asserted
     from decSort on each pass. */

  var SORT_ARROWS = /[\u25be\u25b4]/g; /* ▾ ▴ the active-sort markers */

  function addSortOption(sel, key, label, dir) {
    var o = document.createElement("option");
    o.value = key + "|" + dir;
    o.textContent = label + (dir < 0 ? " \u25be" : " \u25b4");
    sel.appendChild(o);
  }

  function onCardSort(sel) {
    safe(function () {
      var parts = String(sel.value || "").split("|");
      var key = parts[0];
      var dir = Number(parts[1]);
      if (!key || !dir) return;
      if (typeof decSort === "object" && decSort !== null) {
        decSort.key = key;
        decSort.dir = dir;
      }
      if (typeof renderDecisions === "function") renderDecisions();
      /* the re-render rebuilds thead → the MutationObserver pass runs
         applyCardSort again, which re-asserts the value from decSort */
    });
  }

  function applyCardSort() {
    var mobile = isMobile();
    var tables = document.querySelectorAll("table.ledger");
    for (var i = 0; i < tables.length; i++) {
      var table = tables[i];
      var sortables = table.querySelectorAll("thead th.dec-sort");
      var sel = table.__cardsortSel || null;
      if (!mobile || !sortables.length) {
        if (sel && sel.parentNode) sel.parentNode.removeChild(sel);
        table.__cardsortSel = null;
        continue;
      }
      var host = table.parentNode; /* .table-wrap holds the whole card */
      if (!host || typeof host.insertBefore !== "function") continue;
      if (!sel || sel.parentNode !== host) {
        sel = document.createElement("select");
        sel.className = "cardsort";
        sel.setAttribute("aria-label", "Sort ledger");
        var s = sel; /* capture per-table */
        sel.addEventListener("change", function () { onCardSort(s); });
        host.insertBefore(sel, table);
        table.__cardsortSel = sel;
        sel.__cardsortSig = ""; /* force option rebuild below */
      }
      /* options come from the hidden thead, which the poll rewrites —
         rebuild only when the column set actually changed */
      var sig = [];
      for (var j = 0; j < sortables.length; j++) {
        sig.push(sortables[j].getAttribute("data-key") || "");
      }
      var sigStr = sig.join(";");
      if (sel.__cardsortSig !== sigStr) {
        sel.__cardsortSig = sigStr;
        sel.innerHTML = "";
        for (var k = 0; k < sortables.length; k++) {
          var th = sortables[k];
          var key = th.getAttribute("data-key") || "";
          var label = (th.textContent || "")
            .replace(SORT_ARROWS, "").replace(/\s+/g, " ").trim();
          addSortOption(sel, key, label, -1);
          addSortOption(sel, key, label, 1);
        }
      }
      /* re-assert the selection from decSort after each poll re-render */
      var val = "";
      if (typeof decSort === "object" && decSort !== null && decSort.key) {
        val = decSort.key + "|" + (Number(decSort.dir) || 1);
      }
      if (sel.value !== val) sel.value = val;
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
    safe(applyCardSort);
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
    /* bnav + cardsort nodes are ours: injecting them must not re-trigger
       the pass (self-inflicted mutation batches are ignored) */
    return !!(node && (node === bnav ||
      (node.classList && (node.classList.contains("bnav") ||
        node.classList.contains("cardsort")))));
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
          /* lastPnlPoints is a top-level `let` in app.js — a global lexical
             binding, NOT a window property, so window.lastPnlPoints is
             always undefined and the redraw used to pass []. Classic
             scripts share the global lexical environment, so the bare
             reference reaches it; typeof guards an app.js rename. */
          window.drawPnlChart(typeof lastPnlPoints === "undefined" ? [] : lastPnlPoints);
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

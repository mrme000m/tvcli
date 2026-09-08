/* grid/autonomy console — components/touch-tips.js
   First-tap-reveals-title tooltips for touch (UI_AUDIT P2-12).
   Critical console explanations — veto-strip chips, exit profiles,
   metric caveats ("proj /24h", "~ret/yr", "worst avg"), control
   buttons — live in title= attributes, which do not exist on touch
   devices. On a coarse-pointer phone (<=760px) the first tap on an
   element carrying a non-empty title shows that title in a small
   floating chip and suppresses the click; the NEXT tap activates
   normally.

   Deliberately conservative:
   * intercepts only when BOTH (pointer: coarse) and (max-width:
     760px) match — desktops and tablets keep native tooltips
   * opt OUT with data-notip on the element or any ancestor
   * the armed state clears as soon as any other element is tapped,
     so a tip never swallows more than one click
   * runs in the capture phase, so it also guards app.js's delegated
     handlers; nothing here runs for mouse/keyboard users
   * keyboard focus paths are untouched (Enter/Space on
     role="button" spans still fire — those are keydown, not click) */
(function () {
  "use strict";

  var chip = null;
  var armed = null;

  function enabled() {
    try {
      if (!window.matchMedia) return false;
      return window.matchMedia("(pointer: coarse)").matches
        && window.matchMedia("(max-width: 760px)").matches;
    } catch (e) { return false; }
  }

  /* the closest element with a non-empty title, honouring data-notip
     opt-out on the element itself or any ancestor */
  function titledEl(target) {
    if (!target || !target.closest) return null;
    var el = target.closest("[title]");
    if (!el) return null;
    if (!String(el.getAttribute("title") || "").trim()) return null;
    if (el.closest("[data-notip]")) return null;
    return el;
  }

  function ensureChip() {
    if (chip && chip.isConnected) return chip;
    chip = document.createElement("div");
    chip.className = "touch-tip";
    chip.setAttribute("role", "status");
    document.body.appendChild(chip);
    return chip;
  }

  function hideChip() {
    if (chip) chip.classList.remove("touch-tip--on");
  }

  function showChip(el) {
    var c = ensureChip();
    c.textContent = String(el.getAttribute("title") || "").trim();
    c.classList.add("touch-tip--on");
    // place above the tapped element, clamped into the viewport
    var r = el.getBoundingClientRect();
    c.style.left = "0px";
    c.style.top = "0px";
    var cw = c.offsetWidth || 200, ch = c.offsetHeight || 40;
    var left = Math.max(8, Math.min(
      (window.innerWidth || 360) - cw - 8,
      r.left + r.width / 2 - cw / 2));
    var top = r.top - ch - 8;
    if (top < 8) top = Math.min((window.innerHeight || 640) - ch - 8, r.bottom + 8);
    c.style.left = Math.round(left) + "px";
    c.style.top = Math.round(Math.max(8, top)) + "px";
  }

  function onClick(e) {
    try {
      if (!enabled()) { armed = null; hideChip(); return; }
      var el = titledEl(e.target);
      if (!el) { armed = null; hideChip(); return; }
      if (armed === el) { armed = null; hideChip(); return; } // 2nd tap: activate
      armed = el;
      showChip(el);
      e.preventDefault();
      e.stopPropagation();
    } catch (err) { armed = null; hideChip(); }
  }

  document.addEventListener("click", onClick, true);
  // any scroll invalidates the chip position (and the read it explains)
  window.addEventListener("scroll", function () { armed = null; hideChip(); },
    { passive: true, capture: true });
})();

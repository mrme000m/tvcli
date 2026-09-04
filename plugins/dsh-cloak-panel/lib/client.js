window.__ModuleLoader__.load({
  id: "dsh-cloak-panel",
  factory: (require) => {
    var module = { exports: {} };
    var exports = module.exports;
    Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
    let React = require("react");

    // ---- CSS ----
    const css = `
.cloak-panel-root{position:fixed;inset:0 0 0 auto;width:520px;max-width:88vw;background:var(--dsw-alias-bg-base,#fff);border-left:1px solid var(--dsw-alias-border-l2,#e5e7eb);box-shadow:-8px 0 24px rgba(15,23,42,.12);display:flex;flex-direction:column;z-index:30;overflow:hidden}
.cloak-panel-root[data-collapsed="true"]{width:0!important;border-left:none;box-shadow:none;pointer-events:none}
.cloak-panel-rail{position:fixed;right:0;top:50%;transform:translateY(-50%);z-index:31;display:flex;flex-direction:column;align-items:center;gap:8px}
.cloak-panel-rail button{width:36px;height:64px;border-radius:10px 0 0 10px;border:1px solid var(--dsw-alias-border-l2,#e5e7eb);border-right:none;background:var(--dsw-alias-bg-base,#fff);box-shadow:-4px 0 12px rgba(15,23,42,.12);cursor:pointer;display:flex;align-items:center;justify-content:center;color:var(--dsw-alias-text-secondary,#64748b);writing-mode:vertical-lr;font-size:11px;letter-spacing:.04em}
.cloak-panel-rail button:hover{background:var(--dsw-alias-bg-hover,#f8fafc)}
.cloak-panel-handle{position:absolute;left:0;top:0;bottom:0;width:8px;cursor:col-resize;z-index:2;margin-left:-4px;touch-action:none}
.cloak-panel-handle::after{content:"";position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);width:4px;height:40px;border-radius:999px;background:var(--dsw-alias-border-l2,#e5e7eb);opacity:0;transition:opacity .15s}
.cloak-panel-root:hover .cloak-panel-handle::after{opacity:1}
.cloak-panel-header{display:flex;align-items:center;gap:8px;padding:10px 12px;border-bottom:1px solid var(--dsw-alias-border-l2,#e5e7eb);background:var(--dsw-alias-bg-subtle,#f8fafc);flex:none}
.cloak-panel-title{font-weight:600;font-size:13px;white-space:nowrap}
.cloak-panel-dot{width:8px;height:8px;border-radius:50%;flex:none}
.cloak-panel-dot.on{background:#16a34a;box-shadow:0 0 6px rgba(22,163,74,.6)}
.cloak-panel-dot.off{background:#dc2626}
.cloak-panel-url{flex:1;min-width:0;font-size:11px;color:var(--dsw-alias-text-secondary,#64748b);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cloak-panel-header button{flex:none;border:1px solid var(--dsw-alias-border-l2,#e5e7eb);background:var(--dsw-alias-bg-base,#fff);border-radius:6px;padding:4px 8px;font-size:12px;cursor:pointer}
.cloak-panel-header button:hover{background:var(--dsw-alias-bg-hover,#f1f5f9)}
.cloak-panel-toolbar{display:flex;gap:6px;padding:8px 10px;border-bottom:1px solid var(--dsw-alias-border-l2,#e5e7eb);flex:none;align-items:center}
.cloak-panel-toolbar input{flex:1;min-width:0;border:1px solid var(--dsw-alias-border-l2,#e5e7eb);border-radius:6px;padding:6px 8px;font-size:12px;background:var(--dsw-alias-bg-base,#fff)}
.cloak-panel-toolbar button{border:1px solid var(--dsw-alias-border-l2,#e5e7eb);background:var(--dsw-alias-bg-base,#fff);border-radius:6px;padding:6px 10px;font-size:12px;cursor:pointer;white-space:nowrap}
.cloak-panel-toolbar button.primary{background:#2563eb;color:#fff;border-color:#2563eb}
.cloak-panel-toolbar button:hover{filter:brightness(.98)}
.cloak-panel-quick{display:flex;gap:4px;padding:6px 10px;border-bottom:1px solid var(--dsw-alias-border-l2,#e5e7eb);flex-wrap:wrap;flex:none}
.cloak-panel-quick button{font-size:10px;padding:3px 7px;border-radius:999px;border:1px solid var(--dsw-alias-border-l2,#e5e7eb);background:var(--dsw-alias-bg-subtle,#f8fafc);cursor:pointer}
.cloak-panel-quick button:hover{background:var(--dsw-alias-bg-hover,#eef2ff)}
.cloak-panel-body{flex:1;overflow:auto;background:#0b1220;position:relative;display:flex;flex-direction:column}
.cloak-panel-body img{width:100%;height:auto;display:block;cursor:crosshair;user-select:none}
.cloak-panel-zoombar{display:flex;align-items:center;gap:6px;padding:6px 10px;border-bottom:1px solid var(--dsw-alias-border-l2,#e5e7eb);background:var(--dsw-alias-bg-base,#fff);flex:none}
.cloak-panel-zoombar button{border:1px solid var(--dsw-alias-border-l2,#e5e7eb);background:var(--dsw-alias-bg-base,#fff);border-radius:6px;padding:4px 8px;font-size:12px;cursor:pointer;min-width:28px}
.cloak-panel-zoombar button:hover{background:var(--dsw-alias-bg-hover,#f1f5f9)}
.cloak-panel-zoombar button.active{background:#0b1220;color:#fff;border-color:#0b1220}
.cloak-panel-zoombar input[type=range]{flex:1;min-width:60px}
.cloak-panel-zoomlabel{font-size:11px;color:var(--dsw-alias-text-secondary,#64748b);min-width:36px;text-align:center}
.cloak-panel-imgwrap{flex:1;overflow:auto;background:#0b1220;position:relative;display:flex;justify-content:center;align-items:flex-start;padding:0}
.cloak-panel-imgwrap img{transform-origin:top center;display:block;cursor:crosshair;user-select:none;max-width:none}
.cloak-panel-imgwrap[data-fit="contain"] img{width:100%;height:auto;max-width:100%}
.cloak-panel-imgwrap[data-fit="actual"] img{width:auto;height:auto}
.cloak-panel-empty{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;padding:24px;color:#94a3b8;text-align:center}
.cloak-panel-empty code{background:rgba(255,255,255,.08);padding:2px 6px;border-radius:4px;font-size:11px}
.cloak-panel-meta{display:flex;gap:8px;padding:6px 10px;border-top:1px solid var(--dsw-alias-border-l2,#e5e7eb);font-size:11px;color:var(--dsw-alias-text-secondary,#64748b);flex:none;align-items:center}
.cloak-panel-meta .spacer{flex:1}
.cloak-panel-toast{position:absolute;top:8px;left:50%;transform:translateX(-50%);background:rgba(15,23,42,.92);color:#fff;padding:6px 12px;border-radius:999px;font-size:11px;pointer-events:none;opacity:0;transition:opacity .2s}
.cloak-panel-toast[data-show="true"]{opacity:1}
`;
    const tagId = "dsh-cloak-panel/styles";
    if (typeof document !== "undefined" && document.querySelector('style[data-plugin-css="' + tagId + '"]') === null) {
      const tag = document.createElement("style");
      tag.dataset.plugin = "dsh-cloak-panel";
      tag.dataset.pluginCss = tagId;
      tag.textContent = css;
      document.head.appendChild(tag);
    }

    const STORAGE_W = "dsh.cloakPanel.width";
    const STORAGE_COLLAPSED = "dsh.cloakPanel.collapsed";
    const MIN_W = 320, MAX_W = 860, DEFAULT_W = 520;

    function clamp(n, a, b) { return Math.min(b, Math.max(a, Math.round(n))); }

    function usePersisted(key, fallback) {
      const [v, setV] = React.useState(() => {
        try { const raw = localStorage.getItem(key); if (raw !== null) return JSON.parse(raw); } catch {}
        return fallback;
      });
      React.useEffect(() => { try { localStorage.setItem(key, JSON.stringify(v)); } catch {} }, [key, v]);
      return [v, setV];
    }

    function CloakPanel() {
      const [width, setWidth] = usePersisted(STORAGE_W, DEFAULT_W);
      const [collapsed, setCollapsed] = usePersisted(STORAGE_COLLAPSED, false);
      const [status, setStatus] = React.useState(null); // {alive,port,targets,current}
      const [screenshotUrl, setScreenshotUrl] = React.useState(null);
      const [shotTick, setShotTick] = React.useState(0);
      const [imgError, setImgError] = React.useState(null);
      const [urlInput, setUrlInput] = React.useState("");
      const [busy, setBusy] = React.useState(false);
      const [toast, setToast] = React.useState("");
      const [dragging, setDragging] = React.useState(false);
      const imgRef = React.useRef(null);
      const dragOrigin = React.useRef(0);
      const dragBase = React.useRef(DEFAULT_W);
      const [zoom, setZoom] = usePersisted("dsh.cloakPanel.zoom", 1);
      const [fit, setFit] = usePersisted("dsh.cloakPanel.fit", "contain"); // contain | actual
      const wrapRef = React.useRef(null);

      // show toast helper
      const showToast = React.useCallback((msg) => { setToast(msg); setTimeout(() => setToast(""), 2000); }, []);

      // poll status
      React.useEffect(() => {
        let abort = false;
        let timer;
        async function poll() {
          try {
            const r = await fetch("/cloak/status", { cache: "no-store" });
            const j = await r.json();
            if (!abort) {
              setStatus(j);
              if (j.current?.url && !urlInput) setUrlInput(j.current.url);
            }
          } catch {}
          if (!abort) timer = setTimeout(poll, 2500);
        }
        poll();
        return () => { abort = true; if (timer) clearTimeout(timer); };
      }, []);

      // refresh screenshot periodically when expanded and alive
      React.useEffect(() => {
        if (collapsed || !status?.alive) { setScreenshotUrl(null); return; }
        let abort = false;
        let timer;
        async function tick() {
          const bust = Date.now();
          const u = "/cloak/screenshot?t=" + bust + (status?.current?.id ? "&targetId=" + encodeURIComponent(status.current.id) : "");
          if (!abort) setScreenshotUrl(u);
          timer = setTimeout(tick, 1600);
        }
        tick();
        return () => { abort = true; if (timer) clearTimeout(timer); };
      }, [collapsed, status?.alive, status?.current?.id, shotTick]);

      const onNavigate = React.useCallback(async (navUrl) => {
        const u = (navUrl || urlInput || "").trim();
        if (!u) return;
        let full = u;
        if (!/^https?:\/\//i.test(full)) full = "https://" + full;
        setBusy(true);
        try {
          const r = await fetch("/cloak/navigate", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ url: full }) });
          const j = await r.json();
          if (!j.ok) throw new Error(j.error || "navigate failed");
          showToast("Navigating…");
          setTimeout(() => setShotTick(x => x + 1), 900);
        } catch (e) { showToast(String(e.message || e).slice(0, 80)); }
        finally { setBusy(false); }
      }, [urlInput, showToast]);

      const onClickImage = React.useCallback(async (e) => {
        if (!status?.alive || busy) return;
        // use the rendered img rect (already scaled); ratio is 0-1 over the
        // underlying cssContentSize, so zoom/pan via scroll is handled.
        const rect = e.currentTarget.getBoundingClientRect();
        const xRatio = (e.clientX - rect.left) / rect.width;
        const yRatio = (e.clientY - rect.top) / rect.height;
        if (xRatio < 0 || xRatio > 1 || yRatio < 0 || yRatio > 1) return;
        try {
          await fetch("/cloak/click", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ xRatio, yRatio }) });
          showToast(`Click ${Math.round(xRatio*100)}% × ${Math.round(yRatio*100)}%`);
          setTimeout(() => setShotTick(x => x + 1), 500);
        } catch {}
      }, [status, busy, showToast]);

      const setZoomClamped = React.useCallback((v) => {
        const n = Math.min(3, Math.max(0.25, Math.round(v * 100) / 100));
        setZoom(n);
        if (n !== 1) setFit("actual");
      }, [setZoom, setFit]);
      const onWheelZoom = React.useCallback((e) => {
        if (!e.ctrlKey && !e.metaKey) return;
        e.preventDefault();
        const delta = e.deltaY > 0 ? -0.1 : 0.1;
        setZoomClamped(zoom + delta);
      }, [zoom, setZoomClamped]);

      // drag handle
      const onPointerDown = React.useCallback((e) => {
        e.preventDefault();
        e.currentTarget.setPointerCapture(e.pointerId);
        dragOrigin.current = e.clientX;
        dragBase.current = width;
        setDragging(true);
      }, [width]);
      const onPointerMove = React.useCallback((e) => {
        if (!dragging) return;
        if (!e.currentTarget.hasPointerCapture || !e.currentTarget.hasPointerCapture(e.pointerId)) return;
        const dx = dragOrigin.current - e.clientX; // drag left expands
        const next = clamp(dragBase.current + dx, MIN_W, MAX_W);
        setWidth(next);
      }, [dragging, setWidth]);
      const onPointerUp = React.useCallback((e) => {
        if (!dragging) return;
        try { e.currentTarget.releasePointerCapture(e.pointerId); } catch {}
        setDragging(false);
      }, [dragging]);

      if (collapsed) {
        return React.createElement("div", { className: "cloak-panel-rail" },
          React.createElement("button", { onClick: () => setCollapsed(false), title: "Expand CloakBrowser panel" }, "◀  CLOAK"),
          status?.alive ? React.createElement("div", { style: { fontSize: 10, color: "#16a34a" } }, "● live") : null
        );
      }

      const alive = !!status?.alive;
      const currentUrl = status?.current?.url || "";
      const title = status?.current?.title || "";

      return React.createElement("div", { className: "cloak-panel-root", style: { width: width + "px" }, "data-collapsed": collapsed ? "true" : undefined },
        React.createElement("div", { className: "cloak-panel-handle", onPointerDown, onPointerMove, onPointerUp, "data-dragging": dragging ? "true" : undefined }),
        // header
        React.createElement("div", { className: "cloak-panel-header" },
          React.createElement("div", { className: "cloak-panel-dot " + (alive ? "on" : "off"), title: alive ? "CDP connected" : "CDP offline" }),
          React.createElement("div", { className: "cloak-panel-title" }, "Cloak"),
          React.createElement("div", { className: "cloak-panel-url", title: currentUrl || (alive ? "connected" : "offline") }, title ? title.slice(0, 48) : (currentUrl ? currentUrl.slice(0, 60) : (alive ? "Connected" : "Offline"))),
          React.createElement("button", { onClick: () => setShotTick(x=>x+1), disabled: !alive, title: "Refresh screenshot" }, "⟳"),
          React.createElement("button", { onClick: () => setCollapsed(true), title: "Collapse panel" }, "→"),
        ),
        // toolbar
        React.createElement("div", { className: "cloak-panel-toolbar" },
          React.createElement("input", { value: urlInput, onChange: (e) => setUrlInput(e.target.value), onKeyDown: (e) => { if (e.key === 'Enter') onNavigate(); }, placeholder: "https://tradingview.com/chart/ or wundertrading.com", spellCheck: false }),
          React.createElement("button", { className: "primary", onClick: () => onNavigate(), disabled: busy || !alive }, busy ? "…" : "Go")
        ),
        // quick buttons
        React.createElement("div", { className: "cloak-panel-quick" },
          React.createElement("button", { onClick: () => { setUrlInput("https://www.tradingview.com/chart/"); onNavigate("https://www.tradingview.com/chart/"); } }, "TradingView"),
          React.createElement("button", { onClick: () => { setUrlInput("https://wundertrading.com/en/trader/grid_bots"); onNavigate("https://wundertrading.com/en/trader/grid_bots"); } }, "WunderTrading"),
          React.createElement("button", { onClick: () => { setUrlInput("https://www.tradingview.com/symbols/OANDA-XAUUSD/"); onNavigate("https://www.tradingview.com/symbols/OANDA-XAUUSD/"); } }, "XAUUSD"),
          React.createElement("button", { onClick: async () => { try { await navigator.clipboard.writeText(currentUrl); showToast("URL copied"); } catch {} }, disabled: !currentUrl }, "Copy URL"),
          React.createElement("span", { style: { marginLeft: "auto", fontSize: 10, color: "#64748b" } }, alive ? `:${status.port} • ${status.targets?.length||0} targets` : "no CDP")
        ),
        // zoom bar
        React.createElement("div", { className: "cloak-panel-zoombar" },
          React.createElement("button", { onClick: () => setZoomClamped(zoom - 0.25), title: "Zoom out (Ctrl+wheel)" }, "−"),
          React.createElement("input", { type: "range", min: 0.25, max: 3, step: 0.25, value: zoom, onChange: (e) => setZoomClamped(parseFloat(e.target.value)) }),
          React.createElement("button", { onClick: () => setZoomClamped(zoom + 0.25), title: "Zoom in (Ctrl+wheel)" }, "+"),
          React.createElement("span", { className: "cloak-panel-zoomlabel" }, Math.round(zoom * 100) + "%"),
          React.createElement("button", { onClick: () => { setZoom(1); setFit("contain"); }, title: "Reset zoom" }, "100%"),
          React.createElement("button", { className: fit === "contain" ? "active" : "", onClick: () => { setFit("contain"); setZoom(1); }, title: "Fit to panel width" }, "Fit"),
          React.createElement("button", { className: fit === "actual" ? "active" : "", onClick: () => { setFit("actual"); }, title: "Actual size (scroll to pan)" }, "Actual"),
          React.createElement("span", { style: { marginLeft: "auto", fontSize: 10, color: "#64748b" } }, "Ctrl+wheel to zoom • drag to pan when zoomed")
        ),
        // body
        React.createElement("div", { className: "cloak-panel-body" },
          alive && screenshotUrl
            ? React.createElement("div", { ref: wrapRef, className: "cloak-panel-imgwrap", "data-fit": fit, onWheel: onWheelZoom },
                React.createElement("img", { ref: imgRef, src: screenshotUrl, alt: "CloakBrowser view",
                  onClick: onClickImage, onError: () => setImgError("screenshot failed"), onLoad: () => setImgError(null),
                  style: { opacity: busy ? .7 : 1, transform: fit === "contain" ? "none" : `scale(${zoom})`, width: fit === "contain" ? "100%" : "auto" } })
              )
            : React.createElement("div", { className: "cloak-panel-empty" },
                React.createElement("div", { style: { fontSize: 13, fontWeight: 600 } }, alive ? "No screenshot" : "CloakBrowser offline"),
                React.createElement("div", { style: { fontSize: 11, lineHeight: 1.5, maxWidth: 320 } },
                  !alive
                    ? React.createElement(React.Fragment, null, "No CDP session on 9222–9321.", React.createElement("br"), "Launch the headful browser:", React.createElement("br"), React.createElement("code", null, "node browser-debug/launch.mjs"), React.createElement("br"), "or", React.createElement("br"), React.createElement("code", null, "node browser-debug/tv.mjs"), " · ", React.createElement("code", null, "node browser-debug/wt.mjs"))
                    : "Waiting for first frame…"
                ),
                React.createElement("button", { onClick: () => setShotTick(x=>x+1), style: { marginTop: 8, padding: "6px 12px", borderRadius: 6, border: "1px solid rgba(255,255,255,.2)", background: "rgba(255,255,255,.08)", color: "#e2e8f0", cursor: "pointer" } }, "Retry")
              ),
          toast ? React.createElement("div", { className: "cloak-panel-toast", "data-show": "true" }, toast) : null,
          imgError ? React.createElement("div", { style: { position:"absolute", bottom:8, left:8, right:8, background:"rgba(220,38,38,.9)", color:"#fff", padding:"6px 8px", borderRadius:6, fontSize:11 } }, imgError) : null
        ),
        // footer meta
        React.createElement("div", { className: "cloak-panel-meta" },
          React.createElement("span", null, currentUrl ? new URL(currentUrl).hostname : ""),
          React.createElement("span", { className: "spacer" }),
          React.createElement("span", null, "Click to interact • Ctrl+wheel zoom • drag left edge to resize • noVNC on :6080 for full desktop"),
          React.createElement("a", { href: "/cloak/targets", target: "_blank", style: { marginLeft: 8, fontSize: 10 } }, "targets"),
          React.createElement("a", { href: "http://" + (typeof location !== "undefined" ? location.hostname : "localhost") + ":6080/vnc.html?autoconnect=1&resize=scale", target: "_blank", style: { fontSize: 10 } }, "noVNC ↗")
        )
      );
    }

    function apply(ctx) {
      // 'slots' is a Cordis service — must be injected before access.
      ctx.inject(['slots'], (scoped) => {
        scoped.effect(() => {
          return scoped.slots.inject("shell.overlay", () => {
            return scoped.slots.register({ name: "shell.overlay", id: "cloak-panel", order: 10 }, CloakPanel);
          });
        }, "cloak-panel overlay");
      });
    }

    exports.apply = apply;
    exports.inject = ['slots'];
    return module.exports;
  }
});

"""TradingView chart reference — drawing tools, hotkeys, and mouse gestures.

This is the module an agent reads first to *understand* the loaded chart
surface: what can be drawn, how to control/configure the chart, and which
physical keys/gestures map to which actions.

Two sources of truth, both exposed through `Chart.hotkeys()`:
  1. The curated tables below (common TradingView bindings; `verified: false`
     means "confirm against the live sheet").
  2. A best-effort live scrape of TradingView's own Keyboard Shortcuts dialog
     (`Chart.hotkeys(scrape=True)`) — always preferred when it succeeds.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Curated shortcut reference. `keys` is a list of chord parts in press order.
# `mac` swaps Ctrl -> Cmd automatically when `describe()` is given mac=True.
# `confidence` is high/medium/low; treat medium/low as "verify live".
# --------------------------------------------------------------------------
HOTKEY_GROUPS = [
    {
        "title": "Search & navigation",
        "entries": [
            {"keys": ["/"], "action": "Open symbol search", "confidence": "high"},
            {"keys": ["Ctrl", "K"], "action": "Command palette / symbol search (varies by build)", "confidence": "low"},
            {"keys": ["Ctrl", "P"], "action": "Symbol search (varies by build)", "confidence": "low"},
        ],
    },
    {
        "title": "Chart manipulation (keyboard)",
        "entries": [
            {"keys": ["Left", "Right"], "action": "Pan one bar back / forward", "confidence": "high"},
            {"keys": ["+"], "action": "Zoom in", "confidence": "high"},
            {"keys": ["-"], "action": "Zoom out", "confidence": "high"},
            {"keys": ["Home"], "action": "Scroll to chart start", "confidence": "medium"},
            {"keys": ["End"], "action": "Scroll to live edge", "confidence": "medium"},
            {"keys": ["Esc"], "action": "Cancel active drawing / close dialog", "confidence": "high"},
            {"keys": ["Ctrl", "Z"], "action": "Undo last drawing/action", "confidence": "high"},
            {"keys": ["Ctrl", "Shift", "Z"], "action": "Redo", "confidence": "high"},
        ],
    },
    {
        "title": "Chart manipulation (mouse / drag)",
        "entries": [
            {"keys": ["Scroll"], "action": "Zoom around cursor", "confidence": "high"},
            {"keys": ["Drag", "Time axis"], "action": "Zoom time axis", "confidence": "high"},
            {"keys": ["Drag", "Price axis"], "action": "Compress / expand vertical scale", "confidence": "high"},
            {"keys": ["Double-click", "Price axis"], "action": "Re-enable auto-scale", "confidence": "high"},
            {"keys": ["Double-click", "Time axis"], "action": "Fit content", "confidence": "medium"},
            {"keys": ["Shift", "Drag"], "action": "Measure tool (bars x price delta)", "confidence": "medium"},
            {"keys": ["Drag"], "action": "Pan chart", "confidence": "high"},
        ],
    },
    {
        "title": "Pine Editor",
        "entries": [
            {"keys": ["Ctrl", "Enter"], "action": "Save and add to chart / compile", "confidence": "high"},
            {"keys": ["Ctrl", "S"], "action": "Save script", "confidence": "high"},
        ],
    },
    {
        "title": "Panels",
        "entries": [
            {"keys": ["Alt", "F"], "action": "Toggle fullscreen chart", "confidence": "low"},
        ],
    },
]

# Left-toolbar drawing tools and their internal `shape` id used by
# createShape / createMultipointShape. These are the canonical TradingView
# drawing tools; `shape` is the undocumented id used by window.TradingViewApi.
DRAWING_TOOLS = [
    {"tool": "Crosshair / cursor", "shape": None, "kind": "select"},
    {"tool": "Dot", "shape": "dot", "kind": "point"},
    {"tool": "Arrow", "shape": "arrow", "kind": "line"},
    {"tool": "Trend line", "shape": "trend_line", "kind": "line"},
    {"tool": "Info (callout)", "shape": "callout", "kind": "point"},
    {"tool": "Ray", "shape": "ray", "kind": "line"},
    {"tool": "Extended line", "shape": "extended", "kind": "line"},
    {"tool": "Horizontal line", "shape": "horizontal_line", "kind": "level"},
    {"tool": "Vertical line", "shape": "vertical_line", "kind": "level"},
    {"tool": "Horizontal ray", "shape": "horizontal_ray", "kind": "level"},
    {"tool": "Price range", "shape": "price_range", "kind": "range"},
    {"tool": "Date range", "shape": "date_range", "kind": "range"},
    {"tool": "Price label", "shape": "price_label", "kind": "label"},
    {"tool": "Date & price range", "shape": "date_price_range", "kind": "range"},
    {"tool": "Rectangle", "shape": "rectangle", "kind": "zone"},
    {"tool": "Rotated rectangle", "shape": "rotated_rectangle", "kind": "zone"},
    {"tool": "Ellipse", "shape": "ellipse", "kind": "zone"},
    {"tool": "Triangle", "shape": "triangle", "kind": "zone"},
    {"tool": "Fib retracement", "shape": "fib_retracement", "kind": "zone"},
    {"tool": "Fib extension", "shape": "fib_extension", "kind": "zone"},
    {"tool": "Fib channel", "shape": "fib_channel", "kind": "zone"},
    {"tool": "Trend angle", "shape": "trend_angle", "kind": "line"},
    {"tool": "Arc", "shape": "arc", "kind": "line"},
    {"tool": "Brush", "shape": "brush", "kind": "freehand"},
    {"tool": "Magnet", "shape": "magnet", "kind": "mode"},
    {"tool": "Measure", "shape": "measure", "kind": "range"},
    {"tool": "Text", "shape": "text", "kind": "label"},
    {"tool": "Anchor", "shape": "anchor", "kind": "mode"},
]

# The only shapes this package draws programmatically today.
SUPPORTED_SHAPES = ["horizontal_line", "trend_line", "rectangle", "text", "arrow", "ray"]


def describe(mac: bool = False) -> dict:
    """Return the curated reference as a plain dict (JSON-safe)."""
    out = {"source": "curated", "note":
           "Curated TradingView bindings. Prefer the live scrape via Chart.hotkeys(scrape=True). "
           "TradingView's own in-app Keyboard Shortcuts sheet is authoritative.",
           "groups": []}
    for g in HOTKEY_GROUPS:
        entries = []
        for e in g["entries"]:
            keys = list(e["keys"])
            if mac:
                keys = [("Cmd" if k == "Ctrl" else k) for k in keys]
            entries.append({"keys": keys, "action": e["action"], "confidence": e["confidence"]})
        out["groups"].append({"title": g["title"], "entries": entries})
    out["drawing_tools"] = DRAWING_TOOLS
    out["supported_shapes"] = SUPPORTED_SHAPES
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(describe(), indent=2))

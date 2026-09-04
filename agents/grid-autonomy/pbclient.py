#!/usr/bin/env python3
"""Stdlib-only PocketBase REST client for the grid-autonomy daemon.

PocketBase is an optional, side-channel persistence layer: the daemon keeps
its file state (`state.json`, `decisions.jsonl`, `reliability.json`, …) as the
system of record, and this adapter *writes through* to a PocketBase instance
so state becomes queryable and realtime-pushable (SSE) without the trading
path depending on a pre-1.0 server.

Design constraints, matching the rest of grid-autonomy:
  * stdlib only (`urllib.request`, `json`, `ssl`) — no pip deps.
  * Non-fatal by default: every write is best-effort and never raises
    (mirrors `reliability_grid.save` / `save_state` semantics). Set
    `strict=True` to surface errors instead.
  * Config from env, read at call time (like `GRID_STATE_DIR` elsewhere):
        PB_URL         base URL, default http://127.0.0.1:8090
        PB_TOKEN       Authorization token (superuser or API-key record token)
        PB_TIMEOUT     seconds, default 5.0
        PB_DISABLED    any non-empty value => client becomes a no-op
  * Lazy: creating a client does no I/O; the first real call fails soft if
    PocketBase is not running.

Collection map (see .agents/skills/pocketbase/references/grid-integration.md):
    journal      <- daemon.log(state, event)  (kind, msg, at, slot, …)
    decisions    <- reflect.record_decision / record_outcome
    reliability  <- reliability_grid.save (full archetype ledger)
    bots         <- state["active_bots"] entries
    slots        <- state["slots"]
    market_cache <- resolve market_map / market_meta payloads
    run_cards    <- reflect.write_run_card (md text; file field optional)

Usage:
    from pbclient import PB

    pb = PB()                              # reads env, non-fatal
    pb.create("journal", {"kind": "veto", "msg": "…", "at": "…"})
    pb.update("decisions", "d20260905-1", {"outcome": {…}})
    rows = pb.list("decisions", filter='kind = "veto"', sort="-created")
    pb.close()                             # no-op, present for symmetry
"""
from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8090"
DEFAULT_TIMEOUT = 5.0

# Shared superuser token cache. Set by _reauth() and reused by later PB()
# instances when PB_TOKEN env is empty (avoids re-logging-in per instance in
# the long-running daemon, which creates a PB() lazily in each worker module).
_shared_token: str | None = None


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v


class PBError(RuntimeError):
    """Raised only when strict=True; carries the HTTP status + body."""

    def __init__(self, status: int, body: str, url: str):
        super().__init__(f"PocketBase {status} for {url}: {body[:300]}")
        self.status = status
        self.body = body
        self.url = url


class PB:
    """Minimal PocketBase client. Non-fatal unless `strict=True`."""

    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        timeout: float | None = None,
        strict: bool = False,
    ):
        self.url = (url or _env("PB_URL") or DEFAULT_URL).rstrip("/")
        self.token = token if token is not None else (_env("PB_TOKEN") or _shared_token)
        self.timeout = float(
            timeout if timeout is not None else (_env("PB_TIMEOUT") or DEFAULT_TIMEOUT)
        )
        self.strict = strict
        self.disabled = bool(_env("PB_DISABLED"))
        # Superuser credentials for transparent re-auth on JWT expiry.
        self._admin_email = _env("PB_ADMIN_EMAIL")
        self._admin_pass = _env("PB_ADMIN_PASS")

    def _reauth(self) -> bool:
        """Re-authenticate as the superuser; True on success.

        Refreshes `self.token` (and the shared module cache) from
        `PB_ADMIN_EMAIL`/`PB_ADMIN_PASS`. Non-fatal: on any failure the current
        token is left unchanged and False is returned.
        """
        global _shared_token
        if not (self._admin_email and self._admin_pass):
            return False
        body = self._request_raw(
            "POST",
            "/api/collections/_superusers/auth-with-password",
            {"identity": self._admin_email, "password": self._admin_pass},
            auth=False,
            _retry=False,
        )
        token = body.get("token") if isinstance(body, dict) else None
        if not token:
            return False
        self.token = token
        _shared_token = token
        return True

    def _request_raw(self, method, path, payload=None, auth=True, _retry=True):
        """Low-level request returning parsed JSON; no auto-re-auth here."""
        # If we're meant to send an authenticated request but have no token,
        # re-auth first. PocketBase returns 400 (not 401) for a create that
        # fails an auth-only rule, so a post-hoc 401-retry is not enough.
        if auth and not self.token and _retry and self._reauth():
            pass  # token now populated below
        url = self.url + path
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "grid-autonomy-pbclient/1.0",
        }
        if auth and self.token:
            headers["Authorization"] = self.token
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            if exc.code == 401 and auth and _retry and self._reauth():
                return self._request_raw(method, path, payload, auth=True, _retry=False)
            if self.strict:
                raise PBError(exc.code, body, url)
            return {}
        except (urllib.error.URLError, ssl.SSLError, OSError, TimeoutError):
            if self.strict:
                raise
            return {}
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _request(self, method: str, path: str, payload=None) -> dict:
        """Send one request; return parsed JSON body ({} if empty)."""
        return self._request_raw(method, path, payload, auth=True, _retry=True)

    def _guard(self) -> bool:
        """False => no-op (disabled). True => attempt a real call."""
        return not self.disabled

    # ── record operations ────────────────────────────────────────────────
    def create(self, collection: str, data: dict) -> dict | None:
        """POST one record. Returns the created record, or None on failure."""
        if not self._guard():
            return None
        return self._request("POST", f"/api/collections/{collection}/records", data) or None

    def update(self, collection: str, record_id: str, data: dict) -> dict | None:
        """PATCH one record by id."""
        if not self._guard():
            return None
        path = f"/api/collections/{collection}/records/{record_id}"
        return self._request("PATCH", path, data) or None

    def get(self, collection: str, record_id: str) -> dict | None:
        """GET one record by id."""
        if not self._guard():
            return None
        path = f"/api/collections/{collection}/records/{record_id}"
        return self._request("GET", path) or None

    def delete(self, collection: str, record_id: str) -> bool:
        """DELETE one record; True if it succeeded."""
        if not self._guard():
            return False
        path = f"/api/collections/{collection}/records/{record_id}"
        return bool(self._request("DELETE", path))

    def list(
        self,
        collection: str,
        filter: str | None = None,
        sort: str | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> list[dict]:
        """GET a page of records. Returns `items` (empty list on failure)."""
        if not self._guard():
            return []
        q = urllib.parse.urlencode(
            {
                "page": page,
                "perPage": per_page,
                **({"filter": filter} if filter else {}),
                **({"sort": sort} if sort else {}),
            }
        )
        body = self._request("GET", f"/api/collections/{collection}/records?{q}")
        if not isinstance(body, dict):
            return []
        items = body.get("items")
        return items if isinstance(items, list) else []

    # ── write-through mirrors of the daemon's file sinks ─────────────────
    def journal(self, event: dict) -> dict | None:
        """Mirror daemon.log(): one journal record. `event` should already
        carry kind/msg; `at` is added by the caller's log() before this."""
        return self.create("journal", event)

    def decision(self, line: dict) -> dict | None:
        """Mirror reflect.record_decision(): one decisions record.
        `line` is the raw decision dict from reflect (with an `id` key); we
        rename `id` -> `decision_id` because PocketBase reserves `id` for its
        own auto-generated record id and rejects a custom value there."""
        data = dict(line)
        if "id" in data and "decision_id" not in data:
            data["decision_id"] = data.pop("id")
        return self.create("decisions", data)

    def decision_outcome(self, decision_id: str, final: dict) -> dict | None:
        """Mirror reflect.record_outcome(): attach outcome by decision id.
        PocketBase records use their own auto id, so we match on the daemon's
        `decision_id` field via filter, then PATCH that record."""
        rows = self.list("decisions", filter=f'decision_id = "{decision_id}"', per_page=1)
        if not rows:
            return None
        return self.update("decisions", rows[0]["id"], {"outcome": final})

    def reliability(self, ledger: dict) -> dict | None:
        """Mirror reliability_grid.save(): upsert the whole archetype ledger.
        Stored as a single `reliability` record with a stable id."""
        return self.create("reliability", ledger)

    def close(self) -> None:
        """No-op (HTTP client is stateless); present for symmetry."""
        return None


# ── CLI: import + health check + one-shot convenience ──────────────────────
def _main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="PocketBase write-through helper")
    ap.add_argument("--health", action="store_true", help="ping /api/health")
    ap.add_argument("--list", metavar="COLLECTION", help="list records (JSON to stdout)")
    ap.add_argument("--filter", default=None, help="filter for --list")
    ap.add_argument("--import-dir", metavar="DIR", help="import existing state/*.json[l]")
    args = ap.parse_args(argv)

    pb = PB()
    if args.health:
        body = pb._request("GET", "/api/health")
        ok = isinstance(body, dict) and body.get("code") == 200
        print(json.dumps({"ok": ok, "url": pb.url, "body": body}, indent=2))
        return 0 if ok else 1
    if args.list:
        rows = pb.list(args.list, filter=args.filter, per_page=100)
        print(json.dumps(rows, indent=2, sort_keys=True))
        return 0
    if args.import_dir:
        _import_dir(pb, args.import_dir)
        return 0
    ap.print_help()
    return 0


def _import_dir(pb: PB, dirpath: str) -> None:
    """One-shot importer: read the known state files and write them through.
    Best-effort; prints a summary line per file."""
    import os.path as _p

    def _load(name):
        p = _p.join(dirpath, name)
        if not _p.isfile(p):
            return None
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            print(f"import: skip {name} ({exc})")
            return None

    state = _load("state.json")
    if isinstance(state, dict):
        for slot, bot in (state.get("active_bots") or {}).items():
            pb.create("bots", {"slot": str(slot), **({"spec": bot} if isinstance(bot, dict) else {})})
        for slot, plan in (state.get("slots") or {}).items():
            pb.create("slots", {"slot": str(slot), "plan": plan})
        print(f"import: state.json -> bots/slots ({len(state.get('active_bots') or {})} bots)")

    dec = _p.join(dirpath, "decisions.jsonl")
    if _p.isfile(dec):
        n = 0
        with open(dec, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    pb.decision(json.loads(line))
                    n += 1
                except Exception:
                    pass
        print(f"import: decisions.jsonl -> {n} decisions")

    rel = _load("reliability.json")
    if isinstance(rel, dict):
        pb.reliability(rel)
        print("import: reliability.json -> reliability")

    for name, coll in (("market_meta.json", "market_cache"), ("market_map-derivative.json", "market_cache")):
        m = _load(name)
        if isinstance(m, dict):
            pb.create(coll, {"source": name, "payload": m})
            print(f"import: {name} -> {coll}")


if __name__ == "__main__":
    raise SystemExit(_main())